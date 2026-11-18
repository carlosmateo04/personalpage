"""Collect new footage from the configured sources.

folder  - video files dropped into a directory
urls    - a text file of direct video links (for example files a campaign hands out)
ytdlp   - a channel, playlist or video page fetched with yt-dlp

Anything already recorded in the ledger is skipped.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from .config import Config, Source
from .media import VIDEO_EXTS, file_id, text_id
from .state import Ledger

Log = Callable[[str], None]


@dataclass
class Item:
    path: Path
    source_id: str
    name: str
    creator: str
    permission: str
    origin: str
    chat_file: Path | None = None


def _seen(ledger: Ledger, source_id: str) -> bool:
    return ledger.sources.get(source_id, {}).get("status") == "done"


def _folder(cfg: Config, src: Source, ledger: Ledger, log: Log) -> list[Item]:
    folder = Path(src.path or cfg.paths.inbox)
    folder = folder if folder.is_absolute() else cfg.root / folder
    if not folder.is_dir():
        return []
    items: list[Item] = []
    for path in sorted((p for p in folder.iterdir() if p.suffix.lower() in VIDEO_EXTS), key=lambda p: p.stat().st_mtime):
        sid = file_id(path)
        if _seen(ledger, sid):
            continue
        chat = Path(src.chat_file) if src.chat_file else path.with_suffix(".chat.json")
        items.append(Item(path, sid, path.name, src.creator, src.permission or "local file", str(path), chat if chat.exists() else None))
        if len(items) >= src.max_items:
            break
    return items


def _urls(cfg: Config, src: Source, ledger: Ledger, log: Log) -> list[Item]:
    import requests

    listing = Path(src.file)
    listing = listing if listing.is_absolute() else cfg.root / listing
    if not listing.exists():
        log(f"urls source: {listing} does not exist")
        return []
    items: list[Item] = []
    downloads = cfg.path("work") / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    for line in listing.read_text().splitlines():
        url = line.strip()
        if not url or url.startswith("#"):
            continue
        sid = "u" + text_id(url)
        if _seen(ledger, sid) or ledger.sources.get(sid, {}).get("status") == "failed":
            continue
        ext = Path(urlparse(url).path).suffix.lower()
        dest = downloads / f"{sid}{ext if ext in VIDEO_EXTS else '.mp4'}"
        try:
            with requests.get(url, stream=True, timeout=(15, 120)) as resp:
                resp.raise_for_status()
                with dest.open("wb") as fh:
                    for block in resp.iter_content(1 << 20):
                        fh.write(block)
        except requests.RequestException as exc:
            log(f"download failed for {url}: {exc}")
            ledger.sources[sid] = {"name": url, "origin": url, "status": "failed", "error": str(exc)[:300]}
            dest.unlink(missing_ok=True)
            continue
        items.append(Item(dest, sid, Path(urlparse(url).path).name or url, src.creator, src.permission, url))
        if len(items) >= src.max_items:
            break
    return items


def ytdlp_list_cmd(src: Source) -> list[str]:
    return [
        sys.executable, "-m", "yt_dlp", "--flat-playlist", "--no-warnings", "--ignore-errors",
        "--playlist-end", "15", "--print", "%(id)s\t%(webpage_url,url)s\t%(title)s", src.url,
    ]


def ytdlp_fetch_cmd(src: Source, url: str, dest_dir: Path) -> list[str]:
    seconds = int(src.max_hours * 3600)
    return [
        sys.executable, "-m", "yt_dlp", "--no-warnings", "--no-progress", "--no-playlist",
        "--match-filter", "!is_live",
        "-f", "bv*[height<=1080]+ba/b[height<=1080]/b",
        "--merge-output-format", "mp4",
        "--download-sections", f"*0-{seconds}",
        "-o", str(dest_dir / "%(extractor)s-%(id)s.%(ext)s"),
        "--print", "after_move:filepath",
        url,
    ]


def _ytdlp(cfg: Config, src: Source, ledger: Ledger, log: Log) -> list[Item]:
    downloads = cfg.path("work") / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    listing = subprocess.run(ytdlp_list_cmd(src), capture_output=True, text=True)
    entries = [line.split("\t") for line in listing.stdout.splitlines() if line.count("\t") >= 2]
    if not entries:
        log(f"yt-dlp found nothing at {src.url}: {listing.stderr.strip()[-300:]}")
        return []
    items: list[Item] = []
    for vid, url, title in entries:
        sid = "y" + text_id(vid)
        if _seen(ledger, sid) or ledger.sources.get(sid, {}).get("status") == "failed":
            continue
        got = subprocess.run(ytdlp_fetch_cmd(src, url, downloads), capture_output=True, text=True)
        path = next((Path(p) for p in reversed(got.stdout.splitlines()) if p.strip() and Path(p.strip()).exists()), None)
        if path is None:
            err = got.stderr.strip()[-300:]
            log(f"yt-dlp could not fetch {url}: {err}")
            ledger.sources[sid] = {"name": title, "origin": url, "status": "failed", "error": err}
            continue
        items.append(Item(path, sid, title, src.creator, src.permission, url))
        if len(items) >= src.max_items:
            break
    return items


def collect(cfg: Config, ledger: Ledger, log: Log = print) -> list[Item]:
    handlers = {"folder": _folder, "urls": _urls, "ytdlp": _ytdlp}
    items: list[Item] = []
    for src in cfg.sources:
        found = handlers[src.type](cfg, src, ledger, log)
        log(f"source {src.type} {src.path or src.file or src.url}: {len(found)} new item(s)")
        items.extend(found)
    return items


def load_chat(path: Path | None) -> list[float] | None:
    """Message times in seconds from a chat log: a JSON list of numbers or of objects with a time field."""
    if not path or not path.exists():
        return None
    times: list[float] = []
    for entry in json.loads(path.read_text()):
        if isinstance(entry, (int, float)):
            times.append(float(entry))
        elif isinstance(entry, dict):
            value = next((entry[k] for k in ("t", "time", "offset", "content_offset_seconds") if k in entry), None)
            if isinstance(value, (int, float)):
                times.append(float(value))
    return times or None
