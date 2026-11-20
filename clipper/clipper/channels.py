"""More than one channel on one computer.

The first channel lives in the data folder itself. Each further channel is a folder of its own under
channels/, with its own settings, ledger, clips, accounts and sign-ins: a second game, a second brand.
The list is kept in channels.json in the first channel's folder. The app shows one channel at a time;
the daily run and the poster go through all of them.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from . import settings

LIST = "channels.json"
MAIN = "main"


def base() -> Path:
    """The first channel's data folder: where the list of channels is kept."""
    home = os.environ.get("CLIPPER_BASE") or os.environ.get("CLIPPER_DATA")
    if home:
        return Path(home).expanduser()
    from .config import Config

    return Config().root


def _read(home: Path) -> list[dict[str, str]]:
    try:
        found = json.loads((home / LIST).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [c for c in found if isinstance(c, dict) and c.get("id") and c.get("name")] if isinstance(found, list) else []


def listing(home: Path | None = None) -> list[dict[str, Any]]:
    """Every channel: the first one, then the further ones, each with its folder."""
    home = home or base()
    first = settings.load(home).get("brand", {}).get("handle") or "First channel"
    return [{"id": MAIN, "name": first, "folder": str(home)}] + [{**c, "folder": str(home / "channels" / c["id"])} for c in _read(home)]


def folder(ident: str, home: Path | None = None) -> Path:
    found = next((c for c in listing(home) if c["id"] == ident), None)
    if found is None:
        raise KeyError(ident)
    return Path(found["folder"])


def create(name: str, home: Path | None = None) -> dict[str, Any]:
    """Add a channel. It starts with the first channel's settings (searches, lengths, posting times) and no accounts."""
    home = home or base()
    name = " ".join(name.split())[:40]
    if not name:
        raise ValueError("give the channel a name")
    known = _read(home)
    if any(c["name"].lower() == name.lower() for c in known):
        raise ValueError("there is already a channel of that name")
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:24] or "channel"
    ident = next(i for i in [slug, *(f"{slug}-{n}" for n in range(2, 100))] if i != MAIN and all(c["id"] != i for c in known))
    place = home / "channels" / ident
    place.mkdir(parents=True, exist_ok=True)
    (place / "channel.json").write_text(json.dumps({"id": ident, "name": name}), encoding="utf-8")
    # What carries over: how clips are made and when they go out. Not the accounts, the sign-ins or the money.
    carried = {k: v for k, v in settings.load(home).items() if k in ("moments", "schedule", "filters", "render", "monetize", "edits", "housekeeping", "searches")}
    carried["brand"] = {"handle": ""}
    carried["publish"] = {"mode": "dry_run", "hold_for_review": True}
    settings.save(place, carried)
    (home / LIST).write_text(json.dumps([*known, {"id": ident, "name": name}], indent=1), encoding="utf-8")
    return {"id": ident, "name": name, "folder": str(place)}


def rename(ident: str, name: str, home: Path | None = None) -> None:
    home = home or base()
    name = " ".join(name.split())[:40]
    known = _read(home)
    if not name or not any(c["id"] == ident for c in known):
        raise KeyError(ident)
    (home / LIST).write_text(json.dumps([{**c, "name": name} if c["id"] == ident else c for c in known], indent=1), encoding="utf-8")
    (home / "channels" / ident / "channel.json").write_text(json.dumps({"id": ident, "name": name}), encoding="utf-8")


def unlist(ident: str, home: Path | None = None) -> Path:
    """Take a channel off the list. Its folder, with its clips and records, is left where it is. Returns that folder."""
    home = home or base()
    known = _read(home)
    if not any(c["id"] == ident for c in known):
        raise KeyError(ident)
    (home / LIST).write_text(json.dumps([c for c in known if c["id"] != ident], indent=1), encoding="utf-8")
    return home / "channels" / ident
