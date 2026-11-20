"""Going live: the steps from a fresh install to clips posting by themselves, each with whether it is done.

Everything before this point was tested against stand-ins. These are the steps only the owner's own
computer and accounts can prove, in the order to take them. Each is read off what is actually there:
an installed tool, an account that answers, a post with a link. Nothing is ticked by assumption.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from . import claude, outbox, transcribe
from .config import Config
from .state import Ledger

NAMES = {"tiktok": "TikTok", "instagram": "Instagram", "youtube": "YouTube", "facebook": "Facebook"}


def steps(cfg: Config, ledger: Ledger, daily_on: bool = False, check_passed: bool | None = None) -> list[dict[str, Any]]:
    """[{"key", "title", "done", "detail", "where"}]: `where` is the tab that has the next thing to do."""
    out: list[dict[str, Any]] = []

    def step(key: str, title: str, done: bool, detail: str, where: str) -> None:
        out.append({"key": key, "title": title, "done": bool(done), "detail": detail, "where": where})

    tools = shutil.which("ffmpeg") is not None and transcribe.models_present(cfg)
    said = ("every part worked in the last check" if check_passed else "press Check everything to try each part on this computer"
            if check_passed is None else "the last check found a problem; see Activity")
    step("install", "Clipper is installed and every part works here", tools and check_passed is True, said if tools else "run the installer again", "activity")

    why = claude.unavailable(cfg)
    step("claude", "Claude answers", not why, "a Claude account is in use" if not why else why, "accounts")

    how = outbox.routes(cfg)
    missing = [NAMES.get(name, name) for name, way in how.items() if not way]
    step("accounts", "Every platform is connected", bool(how) and not missing,
         "all connected" if not missing else "not connected: " + ", ".join(missing), "accounts")

    made = [rec for rec in ledger.clips.values() if rec.get("file") and Path(rec["file"]).name]
    step("clip", "A first clip is made", bool(made), f"{len(made)} made so far" if made else "press Make clips now", "clips")

    posted = {name for rec in ledger.clips.values() for name, post in (rec.get("posts") or {}).items() if post.get("state") in ("posted", "draft")}
    waiting = [NAMES.get(name, name) for name in cfg.publish.platforms if name not in posted]
    step("post", "A first real post is out on every platform", bool(cfg.publish.platforms) and not waiting,
         "a post is out on each" if not waiting else "nothing out yet on: " + ", ".join(waiting), "clips")

    step("public", "You have looked at a post while signed out", bool(cfg.launch.get("seen_public")),
         "ticked" if cfg.launch.get("seen_public") else "open a post in a private browser window: an app that has not been reviewed may post "
         "where only you can see it (Facebook, Instagram, TikTok). Tick this when the public can see it", "business")

    auto = cfg.publish.mode == "live" and not cfg.publish.hold_for_review
    step("auto", "Clips post by themselves", auto, "on" if auto else "switch on \"Post automatically\" under Settings once the posts look right", "settings")
    step("daily", "The daily run is on", daily_on, "on" if daily_on else "switch it on under Accounts", "accounts")
    return out


def summary(found: list[dict[str, Any]]) -> dict[str, Any]:
    done = sum(1 for s in found if s["done"])
    nxt = next((s for s in found if not s["done"]), None)
    return {"done": done, "of": len(found), "live": done == len(found), "next": nxt["title"] if nxt else ""}
