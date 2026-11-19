"""Collect new footage from the configured sources.

folder  - video files dropped into a directory
urls    - a text file of direct video links
ytdlp   - anything yt-dlp can list: a Twitch channel's top clips, a channel's
          videos, a playlist, a single video, or a search such as
          "ytsearch10:gta 6 glitches"

Listed videos are filtered by views, length and game before anything is
downloaded. Whatever is already in the ledger is skipped.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from .config import Config, Source
from .media import VIDEO_EXTS, file_id, text_id
from .state import Ledger, now_iso

Log = Callable[[str], None]
BOT_CHECK = ("confirm you", "not a bot", "captcha", "429", "too many requests")
PAUSE = 1.0  # seconds between requests to a site


@dataclass
class Item:
    path: Path
    source_id: str
    name: str
    creator: str
    permission: str
    origin: str
    chat_file: Path | None = None
    title: str = ""  # the video's own title, when it has one
    views: int = 0
    section: str = ""  # section hint from the source


def _seen(ledger: Ledger, source_id: str) -> bool:
    """Done, skipped and failed videos are not fetched again; blocked ones are retried another day."""
    return ledger.sources.get(source_id, {}).get("status") in ("done", "skipped", "failed")


def _skip(ledger: Ledger, sid: str, name: str, origin: str, status: str, reason: str) -> None:
    ledger.sources[sid] = {"name": name, "origin": origin, "status": status, "reason": reason[:300], "processed": now_iso()}


def _trouble(ledger: Ledger, sid: str, name: str, origin: str, reason: str) -> None:
    """A fetch that went wrong is retried on later runs, and given up on after the third try."""
    tries = ledger.sources.get(sid, {}).get("tries", 0) + 1
    _skip(ledger, sid, name, origin, "failed" if tries >= 3 else "blocked", reason)
    ledger.sources[sid]["tries"] = tries


def _folder(cfg: Config, src: Source, ledger: Ledger, log: Log) -> list[Item]:
    folder = Path(src.path or cfg.paths.inbox)
    folder = folder if folder.is_absolute() else cfg.root / folder
    if not folder.is_dir():
        return []
    items: list[Item] = []
    for path in sorted((p for p in folder.iterdir() if p.suffix.lower() in VIDEO_EXTS), key=lambda p: p.stat().st_mtime):
        sid = file_id(path)
        if ledger.sources.get(sid, {}).get("status") in ("done", "skipped"):
            continue  # a file that failed is tried again, in case it was replaced
        chat = Path(src.chat_file) if src.chat_file else path.with_suffix(".chat.json")
        items.append(Item(path, sid, path.name, src.creator, src.permission or "local file", str(path),
                          chat if chat.exists() else None, section=src.section))
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
        if _seen(ledger, sid):
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
            _skip(ledger, sid, url, url, "failed", str(exc))
            dest.unlink(missing_ok=True)
            continue
        items.append(Item(dest, sid, Path(urlparse(url).path).name or url, src.creator, src.permission, url, section=src.section))
        if len(items) >= src.max_items:
            break
    return items


def _yt(args: list[str], timeout: int = 1800) -> subprocess.CompletedProcess:
    """Run yt-dlp. Kept in one place so tests can replace it."""
    return subprocess.run([sys.executable, "-m", "yt_dlp", "--no-warnings", *args], capture_output=True, text=True, timeout=timeout)


def _json(text: str) -> dict:
    try:
        data = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def listing(src: Source) -> tuple[list[dict], str]:
    """Videos a source currently lists (id, url, title, views, duration), most viewed first."""
    entries: list[dict] = []
    for attempt in range(2):  # sites sometimes answer a burst of requests with an empty list
        got = _yt(["-J", "--flat-playlist", "--playlist-end", str(src.look_at), src.url], timeout=300)
        data = _json(got.stdout)
        entries = data.get("entries") if data.get("_type") == "playlist" else [data]
        entries = [e for e in (entries or []) if isinstance(e, dict) and e.get("id")]
        if entries or got.returncode != 0:
            break
        time.sleep(PAUSE * 5)
    entries.sort(key=lambda e: -(e.get("view_count") or 0))
    return entries, got.stderr.strip()[-300:]


def _refused(err: str) -> bool:
    return any(marker in err.lower() for marker in BOT_CHECK)


def details(url: str) -> tuple[dict, str]:
    got = _yt(["-J", "--skip-download", "--no-playlist", url], timeout=300)
    return _json(got.stdout), got.stderr.strip()


def fetch_args(src: Source, url: str, dest_dir: Path, duration: float = 0.0) -> list[str]:
    args = ["--no-progress", "--no-playlist", "-f", "bv*[height<=1080]+ba/b[height<=1080]/b", "--merge-output-format", "mp4"]
    limit = int(src.max_hours * 3600)
    if not duration or duration > limit:
        args += ["--download-sections", f"*0-{limit}"]  # only the first hours of a long video
    return [*args, "-o", str(dest_dir / "%(extractor)s-%(id)s.%(ext)s"), "--print", "after_move:filepath", url]


def _channel(meta: dict) -> str:
    creators = meta.get("creators") or []
    return meta.get("channel") or meta.get("creator") or (creators[0] if creators else "") or meta.get("uploader") or ""


def _ytdlp(cfg: Config, src: Source, ledger: Ledger, log: Log) -> list[Item]:
    downloads = cfg.path("work") / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    entries, err = listing(src)
    if not entries:
        log(f"nothing listed at {src.url}: {err}")
        return []
    blocked = {b.lower() for b in cfg.rights.block_channels}
    items: list[Item] = []
    misses = 0
    for entry in entries:
        if misses >= 2:
            log(f"{src.url}: two fetches in a row failed; leaving this source for now")
            break
        sid = "y" + text_id(str(entry["id"]))
        url = entry.get("url") or entry.get("webpage_url") or src.url
        title = entry.get("title") or str(entry["id"])
        if _seen(ledger, sid):
            continue
        low = title.lower()
        # Filters that cost nothing to apply are not recorded: another source may want the same video.
        if (src.title_any and not any(t.lower() in low for t in src.title_any)) or any(t.lower() in low for t in src.title_none):
            continue
        views, minutes = entry.get("view_count"), (entry.get("duration") or 0) / 60
        if views is not None and views < src.min_views:
            continue
        if minutes and (minutes < src.min_minutes or (src.max_minutes and minutes > src.max_minutes)):
            continue

        time.sleep(PAUSE)
        meta, err = details(url)
        if not meta:
            if _refused(err):
                # The site is refusing automated requests from this machine. Stop asking for now.
                _skip(ledger, sid, title, url, "blocked", "the site's bot check refused this machine")
                log(f"{src.url}: the site's bot check refused this machine; leaving this source for now")
                break
            _trouble(ledger, sid, title, url, err)
            misses += 1
            continue
        channel = _channel(meta)
        categories = [str(c) for c in meta.get("categories") or []]
        if meta.get("live_status") in ("is_live", "is_upcoming") or meta.get("is_live"):
            continue
        if channel.lower() in blocked:
            _skip(ledger, sid, title, url, "skipped", f"blocked channel {channel}")
            continue
        if src.category and not any(src.category.lower() in c.lower() for c in categories):
            _skip(ledger, sid, title, url, "skipped", f"not {src.category} ({', '.join(categories) or 'no category'})")
            continue

        got = _yt(fetch_args(src, url, downloads, float(meta.get("duration") or 0)))
        path = next((Path(p.strip()) for p in reversed(got.stdout.splitlines()) if p.strip() and Path(p.strip()).exists()), None)
        if path is None:
            err = got.stderr.strip()
            log(f"could not fetch {url}: {err[:200]}")
            if _refused(err):
                _skip(ledger, sid, title, url, "blocked", "the site's bot check refused this machine")
                break
            _trouble(ledger, sid, title, url, err)
            misses += 1
            continue
        misses = 0
        permission = src.permission
        if not permission and "creative commons" in str(meta.get("license") or "").lower():
            permission = "Creative Commons licence (reuse allowed with credit)"
        items.append(Item(path, sid, title, src.creator or channel, permission, meta.get("webpage_url") or url,
                          title=meta.get("title") or title, views=int(meta.get("view_count") or views or 0), section=src.section))
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
