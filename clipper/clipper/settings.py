"""Settings made on this computer, and the posting key.

config.toml comes with the code and is replaced on every update. What the user
changes in the app is kept in settings.json in the data folder and laid over
config.toml when it is loaded, so it survives updates.

The posting-service key is a secret: on Windows it goes into the Windows
Credential Manager; elsewhere into a file only the user can read. It is never
written to settings.json or sent back to the browser.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote_plus, urlparse

SETTINGS = "settings.json"
SECRETS = "secrets.json"
SERVICE = "clipper"
THIS_WEEK = "EgIIAw%253D%253D"  # YouTube's "uploaded this week" search filter
SEARCH_TITLE_ANY = ["no commentary", "no talking", "gameplay only", "raw gameplay"]
SEARCH_TITLE_NONE = ["reaction", "reacts", "live", "stream", "podcast", "review", "trailer", "leak"]

# What the app may change: section -> setting names.
EDITABLE: dict[str, set[str]] = {
    "brand": {"handle"},
    "moments": {"min_len", "max_len", "max_per_source", "whole_clip_min", "whole_clip_max"},
    "schedule": {"timezone", "slots"},
    "publish": {"mode", "profile", "platforms", "tiktok_post_mode", "youtube_privacy", "hold_for_review", "facebook_page_id"},
    "filters": {"no_facecam", "no_commentary"},
    "housekeeping": {"keep_downloads_days", "keep_clips_days"},
    "review": {"enabled"},
    "render": {"progress_bar", "layout"},
}


def load(data_root: Path) -> dict[str, Any]:
    path = data_root / SETTINGS
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def save(data_root: Path, values: dict[str, Any]) -> None:
    data_root.mkdir(parents=True, exist_ok=True)
    tmp = data_root / (SETTINGS + ".tmp")
    tmp.write_text(json.dumps(values, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, data_root / SETTINGS)


def update(data_root: Path, changes: dict[str, Any]) -> dict[str, Any]:
    """Apply changes from the app. Anything outside the editable settings is refused."""
    current = load(data_root)
    for section, values in changes.items():
        if section == "searches":
            current["searches"] = [
                {"query": str(s["query"]).strip(), "section": str(s.get("section", "moments"))}
                for s in values if str(s.get("query", "")).strip()
            ]
            continue
        allowed = EDITABLE.get(section)
        if allowed is None or not isinstance(values, dict):
            raise ValueError(f"{section} cannot be changed here")
        refused = set(values) - allowed
        if refused:
            raise ValueError(f"{section}: {', '.join(sorted(refused))} cannot be changed here")
        current.setdefault(section, {}).update(values)
    save(data_root, current)
    return current


def overlay(base: dict[str, Any], local: dict[str, Any]) -> dict[str, Any]:
    """config.toml with the local settings laid over it."""
    merged = {k: (dict(v) if isinstance(v, dict) else v) for k, v in base.items()}
    for section, values in local.items():
        if section == "searches":
            continue
        if isinstance(values, dict) and isinstance(merged.get(section), dict):
            merged[section].update(values)
        else:
            merged[section] = values
    if "searches" in local:
        # The app's search list replaces the online sources; other sources (the inbox) stay.
        kept = [s for s in merged.get("sources", [{"type": "folder", "path": "inbox", "permission": "own footage"}]) if s.get("type") != "ytdlp"]
        merged["sources"] = [search_source(s["query"], s.get("section", "moments")) for s in local["searches"]] + kept
    return merged


def search_source(query: str, section: str) -> dict[str, Any]:
    return {
        "type": "ytdlp",
        "url": f"https://www.youtube.com/results?search_query={quote_plus(query)}&sp={THIS_WEEK}",
        "title_any": list(SEARCH_TITLE_ANY),
        "title_none": list(SEARCH_TITLE_NONE),
        "section": section,
        "min_views": 200,
        "min_minutes": 1,
        "max_minutes": 240,
        "look_at": 25,
        "max_items": 1,
        "max_hours": 0.5,
    }


def search_text(url: str) -> str:
    """The words of a YouTube search link, or "" if the link is something else."""
    parsed = urlparse(url)
    if "youtube.com" not in parsed.netloc or parsed.path != "/results":
        return ""
    return (parse_qs(parsed.query).get("search_query") or [""])[0]


# --- the posting key ----------------------------------------------------------

def _keyring():
    if os.name != "nt":
        return None
    try:
        import keyring

        return keyring
    except Exception:
        return None


def get_secret(data_root: Path, name: str) -> str:
    ring = _keyring()
    if ring is not None:
        try:
            value = ring.get_password(SERVICE, name)
            if value:
                return value
        except Exception:
            pass
    path = data_root / SECRETS
    if path.exists():
        try:
            return str(json.loads(path.read_text(encoding="utf-8")).get(name, ""))
        except json.JSONDecodeError:
            return ""
    return ""


def set_secret(data_root: Path, name: str, value: str) -> str:
    """Store a secret; returns where it was put ("Windows Credential Manager" or the file path)."""
    ring = _keyring()
    if ring is not None:
        try:
            if value:
                ring.set_password(SERVICE, name, value)
            else:
                ring.delete_password(SERVICE, name)
            return "Windows Credential Manager"
        except Exception:
            pass
    data_root.mkdir(parents=True, exist_ok=True)
    path = data_root / SECRETS
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
    if value:
        data[name] = value
    else:
        data.pop(name, None)
    path.write_text(json.dumps(data), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return str(path)
