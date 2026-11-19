"""Thin wrappers around ffmpeg/ffprobe."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".webm", ".flv", ".ts", ".m4v"}


class MediaError(RuntimeError):
    pass


def run(cmd: list[str], what: str = "") -> str:
    """Run a command; on failure raise with the tail of its error output."""
    proc = subprocess.run(cmd, capture_output=True, text=True)
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
    """One image with a few frames from a stretch of video, side by side, for a quick look."""
    out.parent.mkdir(parents=True, exist_ok=True)
    span = max(end - start, 0.5)
    run(
        ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{start:.2f}", "-t", f"{span:.2f}", "-i", str(src),
         "-vf", f"fps={shots / span:.5f},scale=400:-2,tile={shots}x1", "-frames:v", "1", "-q:v", "5", str(out)],
        "contact sheet",
    )
    return out


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
