"""Thin wrappers around ffmpeg/ffprobe."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".webm", ".flv", ".ts", ".m4v"}


class MediaError(RuntimeError):
    pass


def sound_seconds(path: Path) -> float:
    """How long a sound file runs; raises when it cannot be read as sound."""
    out = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)], "ffprobe")
    info = json.loads(out)
    if not any(s.get("codec_type") == "audio" for s in info.get("streams", [])):
        raise MediaError(f"{Path(path).name}: no sound in it")
    try:
        return float(info["format"]["duration"])
    except (KeyError, TypeError, ValueError) as exc:
        raise MediaError(f"{Path(path).name}: its length cannot be read") from exc


def run(cmd: list[str], what: str = "") -> str:
    """Run a command; on failure raise with the tail of its error output."""
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-12:])
        raise MediaError(f"{what or cmd[0]} failed (exit {proc.returncode}):\n{tail}")
    return proc.stdout


@dataclass
class Probe:
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool
    pixel_shape: str = "1:1"  # sample aspect ratio; anything else means non-square pixels


def probe(path: Path) -> Probe:
    out = run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        "ffprobe",
    )
    info = json.loads(out)
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    if video is None:
        raise MediaError(f"{path.name}: no video stream")
    num, _, den = (video.get("avg_frame_rate") or "0/1").partition("/")
    fps = float(num) / float(den) if den and float(den) else 0.0
    duration = float(info.get("format", {}).get("duration") or video.get("duration") or 0.0)
    return Probe(
        duration=duration,
        width=int(video["width"]),
        height=int(video["height"]),
        fps=fps,
        has_audio=any(s.get("codec_type") == "audio" for s in info.get("streams", [])),
        pixel_shape=video.get("sample_aspect_ratio") or "1:1",
    )


def extract_audio(src: Path, dst: Path, rate: int = 16000) -> Path:
    """Mono WAV at the given rate, for transcription and loudness analysis."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", str(rate), "-c:a", "pcm_s16le", str(dst)],
        "audio extraction",
    )
    return dst


def contact_sheet(src: Path, start: float, end: float, out: Path, shots: int = 4) -> Path:
    """One image with frames taken evenly across a stretch of video, in reading order, for a quick look.

    Up to four frames sit side by side; more are laid out in rows of four. Each
    frame is fetched by jumping to its moment, so a minute and a half of 4K
    footage costs a few frames' worth of decoding, not all of it.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    span = max(end - start, 0.5)
    cols = min(shots, 4)
    rows = -(-shots // cols)
    with tempfile.TemporaryDirectory(dir=out.parent) as tmp:
        for n in range(shots):
            at = start + (n + 0.5) * span / shots  # the middle of each equal part
            run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{at:.2f}", "-i", str(src), "-frames:v", "1",
                 "-vf", "scale=400:-2", "-q:v", "4", str(Path(tmp) / f"{n:02d}.jpg")], "contact sheet")
        run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", "1", "-i", str(Path(tmp) / "%02d.jpg"),
             "-vf", f"tile={cols}x{rows}", "-frames:v", "1", "-q:v", "5", str(out)], "contact sheet")
    return out


def clock(seconds: float) -> str:
    """A time in a video the way a player shows it: 7:05, or 1:02:03 past the hour."""
    total = max(0, int(round(seconds)))
    hours, rest = divmod(total, 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60}:{rest % 60:02d}"


def seconds(text: object) -> float | None:
    """The reverse of `clock`: "7:05" is 425.0. A bare number is taken as seconds. None if it is neither."""
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return float(text) if text >= 0 else None
    parts = str(text).strip().lower().removesuffix("s").strip().split(":")
    try:
        numbers = [float(p) for p in parts]
    except ValueError:
        return None
    if not 1 <= len(numbers) <= 3 or any(n < 0 for n in numbers):
        return None
    total = 0.0
    for n in numbers:
        total = total * 60 + n
    return total


def grab(src: Path, at: float, out: Path, width: int = 480, exact: bool = True) -> bool:
    """One frame of a video as a JPEG; False if there is no frame there.

    `exact=False` takes the keyframe just before the moment asked for. That is a few
    seconds out at most and many times faster, which is what a storyboard of a whole
    video needs.
    """
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    if not exact:
        cmd.append("-noaccurate_seek")
    cmd += ["-ss", f"{max(0.0, at):.2f}", "-i", str(src), "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "4", str(out)]
    done = subprocess.run(cmd, capture_output=True)
    return done.returncode == 0 and out.exists() and out.stat().st_size > 0


def _tile(shots: list[tuple[float, object]], out_dir: Path, name: str, cols: int, rows: int, width: int) -> list[dict]:
    """Lay frames out on sheets, each frame with its time written on it. `shots` is (seconds, image or None)."""
    import cv2
    import numpy as np

    first = next((shot for _, shot in shots if shot is not None), None)
    if first is None:
        raise MediaError("no frames could be read")
    height = first.shape[0]
    blank = np.zeros((height, width, 3), dtype=np.uint8)
    per = cols * rows
    made: list[dict] = []
    for page in range(0, len(shots), per):
        chunk = shots[page:page + per]
        tiles = []
        for at, shot in chunk:
            tile = blank.copy() if shot is None or shot.shape != blank.shape else shot.copy()
            label = clock(at)
            (w, h), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.8, 2)
            cv2.rectangle(tile, (0, 0), (w + 14, h + 16), (0, 0, 0), -1)
            cv2.putText(tile, label, (7, h + 8), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2, cv2.LINE_AA)
            tiles.append(tile)
        used_cols = min(cols, len(tiles))
        while len(tiles) % used_cols:
            tiles.append(blank.copy())
        grid = np.vstack([np.hstack(tiles[r:r + used_cols]) for r in range(0, len(tiles), used_cols)])
        path = out_dir / f"{name}-{page // per + 1:02d}.jpg"
        cv2.imwrite(str(path), grid, [cv2.IMWRITE_JPEG_QUALITY, 82])
        made.append({"path": str(path), "from": chunk[0][0], "to": chunk[-1][0], "frames": len(chunk)})
    return made


def sheets(src: Path, times: list[float], out_dir: Path, name: str, cols: int = 4, rows: int = 5, width: int = 480,
           exact: bool = True) -> list[dict]:
    """Frames of a video at the given moments, laid out on sheets with each frame's time written on it.

    Returns one entry per sheet: {"path", "from", "to", "frames"}. Frames run left to
    right, then top to bottom.
    """
    import cv2
    from concurrent.futures import ThreadPoolExecutor

    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out_dir) as tmp:
        paths = [Path(tmp) / f"{n:04d}.jpg" for n in range(len(times))]
        with ThreadPoolExecutor(max_workers=4) as pool:  # each frame is its own short ffmpeg run
            got = list(pool.map(lambda pair: grab(src, pair[0], pair[1], width, exact), zip(times, paths)))
        shots = [(at, cv2.imread(str(path)) if ok else None) for at, ok, path in zip(times, got, paths)]
        return _tile(shots, out_dir, name, cols, rows, width)


def storyboard(src: Path, duration: float, step: float, out_dir: Path, name: str, cols: int = 5, rows: int = 5, width: int = 480) -> list[dict]:
    """A whole video as sheets of small frames, one every `step` seconds, each with its time on it.

    One pass that decodes only the keyframes, so half an hour of video takes seconds
    rather than minutes; each frame is the keyframe nearest its moment. A sheet of 5 by 5
    frames is 2400 pixels wide, inside what Claude reads at full resolution. If the fast
    pass comes back short (a file with very few keyframes), each frame is fetched by itself.
    """
    import cv2

    out_dir.mkdir(parents=True, exist_ok=True)
    wanted = max(1, int(duration // step))
    with tempfile.TemporaryDirectory(dir=out_dir) as tmp:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-skip_frame", "nokey", "-i", str(src), "-an",
                        "-vf", f"fps=1/{step:.3f},scale={width}:-2", "-q:v", "4", str(Path(tmp) / "%04d.jpg")], capture_output=True)
        found = sorted(Path(tmp).glob("*.jpg"))
        if len(found) >= 0.6 * wanted:
            shots = [(min(duration, n * step), cv2.imread(str(path))) for n, path in enumerate(found[:wanted + 1])]
            return _tile(shots, out_dir, name, cols, rows, width)
    times = [min(duration - 0.5, n * step + step / 2) for n in range(wanted)]
    return sheets(src, times, out_dir, name, cols, rows, width, exact=False)


def file_id(path: Path) -> str:
    """Stable id for a file: its size plus the first and last 4 MB."""
    size = path.stat().st_size
    h = hashlib.sha1(str(size).encode())
    with path.open("rb") as fh:
        h.update(fh.read(4 << 20))
        if size > 8 << 20:
            fh.seek(-(4 << 20), 2)
            h.update(fh.read(4 << 20))
    return h.hexdigest()[:16]


def text_id(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:16]
