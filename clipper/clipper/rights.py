"""The record of rights: whose footage may be used on what basis, and every claim, strike and takedown.

Kept in state/rights.json.

  permissions  per channel: what was agreed and when ("clip policy on their channel page", an e-mail, a campaign).
               Clips of that channel carry it, in their record and in the report.
  events       claims, strikes, takedowns and blocks, each tied to the clip, its platform and its source channel.
               They are entered by hand in the app, and by themselves when a platform reports that it rejected
               or blocked a post for copyright.
  blocked      channels nothing is taken from any more: after one strike or takedown, or after the number of
               claims set in `rights.claims_before_block`. Clips of theirs that have not gone out are not sent.
"""

from __future__ import annotations

import re
import secrets
from typing import Any

from . import store
from .config import Config
from .state import Ledger, now_iso

KINDS = {"claim": "Copyright claim", "strike": "Strike", "takedown": "Takedown", "blocked": "Blocked in some countries"}
SERIOUS = ("strike", "takedown")
_COPYRIGHT = re.compile(r"copyright|claim|infring|trademark|taken down|removed for", re.I)


def key(channel: str) -> str:
    return " ".join(str(channel).lower().split())


def _load(cfg: Config) -> dict[str, Any]:
    data = store.read(cfg, "rights")
    return {"permissions": data.get("permissions", {}), "events": data.get("events", []), "blocked": data.get("blocked", {})}


def record(cfg: Config) -> dict[str, Any]:
    """Everything on file, as the app shows it."""
    data = _load(cfg)
    return {"permissions": sorted(data["permissions"].values(), key=lambda p: key(p["channel"])),
            "events": list(reversed(data["events"])),  # newest first
            "blocked": sorted(data["blocked"].values(), key=lambda b: key(b["channel"])),
            "kinds": KINDS, "claims_before_block": cfg.rights.claims_before_block}


def permit(cfg: Config, channel: str, note: str) -> None:
    channel, note = " ".join(channel.split())[:80], " ".join(note.split())[:300]
    if not channel or not note:
        raise ValueError("name the channel and say on what basis its footage may be used")
    data = _load(cfg)
    data["permissions"][key(channel)] = {"channel": channel, "note": note, "at": now_iso()}
    store.write(cfg, "rights", data)


def unpermit(cfg: Config, channel: str) -> None:
    data = _load(cfg)
    data["permissions"].pop(key(channel), None)
    store.write(cfg, "rights", data)


def permission_for(cfg: Config, channel: str) -> str:
    return _load(cfg)["permissions"].get(key(channel), {}).get("note", "") if channel else ""


def is_blocked(cfg: Config, channel: str) -> str:
    """Why nothing is taken from this channel, or empty."""
    if not channel:
        return ""
    if key(channel) in {key(b) for b in cfg.rights.block_channels}:
        return "on the list of channels never taken from"
    return _load(cfg)["blocked"].get(key(channel), {}).get("why", "")


def unblock(cfg: Config, channel: str) -> None:
    data = _load(cfg)
    data["blocked"].pop(key(channel), None)
    store.write(cfg, "rights", data)


def add_event(cfg: Config, ledger: Ledger, kind: str, platform: str, clip_id: str, note: str = "", auto: bool = False) -> dict[str, Any]:
    """Put a claim, strike or takedown on record. Returns the event; `blocked` in it says whether the source is now blocked."""
    if kind not in KINDS:
        raise ValueError("a claim, a strike, a takedown or a block")
    rec = ledger.clips.get(clip_id)
    if rec is None:
        raise KeyError(clip_id)
    channel = str(rec.get("creator", ""))
    data = _load(cfg)
    if auto and any(e["clip"] == clip_id and e["platform"] == platform and e["kind"] == kind for e in data["events"]):
        return {}  # already on record
    event = {"id": secrets.token_hex(4), "at": now_iso(), "kind": kind, "platform": platform, "clip": clip_id, "title": str(rec.get("title", "")),
             "channel": channel, "note": " ".join(note.split())[:300], "auto": auto, "blocked": False}
    data["events"].append(event)
    claims = sum(1 for e in data["events"] if key(e["channel"]) == key(channel) and e["kind"] == "claim")
    if channel and "own" not in str(rec.get("permission", "")).lower():
        if kind in SERIOUS or (kind == "claim" and claims >= cfg.rights.claims_before_block):
            why = f"a {kind} on {platform}" if kind in SERIOUS else f"{claims} copyright claims"
            data["blocked"][key(channel)] = {"channel": channel, "since": now_iso(), "why": f"blocked after {why}"}
            event["blocked"] = True
    store.write(cfg, "rights", data)
    rec.setdefault("rights", []).append({"kind": kind, "platform": platform, "at": event["at"]})
    ledger.save()
    return event


def remove_event(cfg: Config, ident: str) -> None:
    data = _load(cfg)
    data["events"] = [e for e in data["events"] if e["id"] != ident]
    store.write(cfg, "rights", data)


def notice(cfg: Config, ledger: Ledger, rec: dict, platform: str, error: str) -> dict[str, Any]:
    """A platform said a post failed: if it was over copyright, put it on record by itself."""
    if not _COPYRIGHT.search(error or ""):
        return {}
    kind = "takedown" if re.search(r"taken down|removed|rejected", error, re.I) else "claim"
    return add_event(cfg, ledger, kind, platform, rec["id"], error[:200], auto=True)
