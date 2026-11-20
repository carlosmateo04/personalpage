"""Tell gameplay-only footage from footage with a streamer on it.

Two checks, both best-effort:

facecam    A webcam overlay is a face that stays in the same spot while the game
           moves behind it. Frames are sampled across the footage and faces
           detected; a spot that holds a face in most of the frames is a facecam.
talk       A commentator talks about the game from outside it ("chat", "this
           game", "subscribe", "the graphics"). Characters in the game never do.
           Words like these in the transcript mean someone is talking over it.

A quiet commentator with no camera who never uses such words gets through; the
daily review reads the transcript and looks at the frames to catch those.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import numpy as np

from .config import Config

WIDTH = 960  # frames are scaled to this width before detection


def frames(path: Path, start: float, end: float, count: int) -> list[np.ndarray]:
    """Evenly spaced colour frames (blue, green, red) from a stretch of a video."""
    info = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    ).stdout.strip().split(",")
    if len(info) < 2 or not info[0].isdigit():
        return []
    height = int(int(info[1]) * WIDTH / int(info[0])) // 2 * 2
    span = max(end - start, 0.1)
    size = WIDTH * height * 3
    if span <= 120:  # short stretch: decode it once
        out = subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-ss", f"{start:.2f}", "-t", f"{span:.2f}", "-i", str(path),
             "-vf", f"fps={count / span:.5f},scale={WIDTH}:{height}", "-pix_fmt", "bgr24", "-f", "rawvideo", "-"],
            capture_output=True,
        ).stdout
    else:  # long video: jump straight to each frame
        out = b"".join(
            subprocess.run(
                ["ffmpeg", "-loglevel", "error", "-ss", f"{start + span * (k + 0.5) / count:.2f}", "-i", str(path),
                 "-frames:v", "1", "-vf", f"scale={WIDTH}:{height}", "-pix_fmt", "bgr24", "-f", "rawvideo", "-"],
                capture_output=True,
            ).stdout[:size]
            for k in range(count)
        )
    return [np.frombuffer(out[i * size : (i + 1) * size], dtype=np.uint8).reshape(height, WIDTH, 3) for i in range(len(out) // size)]


_detector = None


def _faces(image: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Face boxes (x, y, width, height) in one frame, found with the bundled YuNet model."""
    global _detector
    import cv2

    height, width = image.shape[:2]
    if _detector is None:
        model = Path(__file__).resolve().parent.parent / "assets" / "models" / "face_detection_yunet_2023mar.onnx"
        _detector = cv2.FaceDetectorYN.create(str(model), "", (width, height), 0.8, 0.3, 50)
    _detector.setInputSize((width, height))
    found = _detector.detect(image)[1]
    return [] if found is None else [tuple(int(v) for v in row[:4]) for row in found if row[2] >= 28]


def facecam(cfg: Config, path: Path, start: float, end: float) -> tuple[bool, str]:
    """Whether a face holds one spot through the footage, and where.

    A streamer's webcam face stays within a few percent of the frame for the whole
    clip. Characters in the game move with the camera and the cuts, so they only
    line up at one spot for a few frames.
    """
    shots = frames(path, start, end, cfg.filters.facecam_frames)
    if len(shots) < 4:
        return False, ""
    height = shots[0].shape[0]
    centres = [(i, x + w / 2, y + h / 2) for i, shot in enumerate(shots) for x, y, w, h in _faces(shot)]
    reach = WIDTH * 0.065
    best: tuple[int, float, float] = (0, 0.0, 0.0)
    for _, sx, sy in centres:
        held = {i for i, x, y in centres if abs(x - sx) <= reach and abs(y - sy) <= reach}
        if len(held) > best[0]:
            best = (len(held), sx, sy)
    count, x, y = best
    share = count / len(shots)
    if share < cfg.filters.facecam_share or count < 4:
        return False, ""
    side = "left" if x < WIDTH / 3 else "right" if x > WIDTH * 2 / 3 else "centre"
    level = "top" if y < height / 3 else "bottom" if y > height * 2 / 3 else "middle"
    return True, f"a face stays at the {level} {side} in {share:.0%} of the frames"


def talk(cfg: Config, text: str) -> list[str]:
    """Commentator phrases found in what is said.

    In a short clip one such phrase is enough. A long video holds a lot of game
    dialogue, where a stray "chat" can turn up, so it takes three phrases of at
    least two kinds.
    """
    low = " " + re.sub(r"[^a-z0-9' ]", " ", text.lower()) + " "
    low = re.sub(r"\s+", " ", low)
    counts = {w: low.count(f" {w.lower()} ") for w in cfg.filters.commentary_words}
    found = [w for w, n in counts.items() if n]
    if len(low.split()) > 150 and (sum(counts.values()) < 3 or len(found) < 2):
        return []
    return found


def talk_times(cfg: Config, words: list) -> list[tuple[float, str]]:
    """(time, phrase) for every commentator phrase in a timed transcript."""
    tokens = [re.sub(r"[^a-z0-9']", "", w.w.lower()) for w in words]
    hits: list[tuple[float, str]] = []
    for phrase in cfg.filters.commentary_words:
        parts = phrase.lower().split()
        for i in range(len(tokens) - len(parts) + 1):
            if tokens[i : i + len(parts)] == parts:
                hits.append((words[i].s, phrase))
    return sorted(hits)


def commentary(cfg: Config, words: list, duration: float) -> tuple[str, list[tuple[float, float]]]:
    """How much of a long video has someone talking about the game over it.

    Returns a verdict and the stretches to keep clear of:
      "none"        nothing worth acting on (at most a stray word or two)
      "parts"       a spoken intro, outro or aside: the video is usable, those stretches are not
      "throughout"  commentary all the way through: the video is not gameplay-only
    """
    hits = talk_times(cfg, words)
    if len(hits) < 3 or len({phrase for _, phrase in hits}) < 2:
        return "none", []
    stretches: list[list[float]] = []
    for t, _ in hits:  # phrases within a minute of each other belong to the same stretch
        if stretches and t - stretches[-1][1] <= 60:
            stretches[-1][1] = t
        else:
            stretches.append([t, t])
    padded = [(max(0.0, a - 30), min(duration, b + 30)) for a, b in stretches]
    covered = sum(b - a for a, b in padded) / max(duration, 1.0)
    if len(padded) >= 4 or covered > 0.25:
        return "throughout", padded
    return "parts", padded


def clock(seconds: float) -> str:
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


def check(cfg: Config, path: Path, start: float, end: float, text: str, title: str = "") -> list[str]:
    """Reasons this footage is not gameplay-only; empty means it passes."""
    reasons: list[str] = []
    f = cfg.filters
    if f.no_commentary:
        said = talk(cfg, text)
        if said:
            reasons.append(f"someone is talking over the game (says: {', '.join(said[:4])})")
    if f.no_facecam:
        found, where = facecam(cfg, path, start, end)
        if found:
            reasons.append(f"streamer facecam ({where})")
    return reasons
