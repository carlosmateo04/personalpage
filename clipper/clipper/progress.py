"""How far the channel is from being paid by each platform.

Each programme has a bar to clear before it pays anything. This puts the channel's own numbers
against those bars: followers, read from each platform where it lets an app read them (typed in
under Settings where it does not), and views, counted from the posts Clipper sent in the period
the programme looks at. Views of anything posted another way are not in the count, so the real
figure can only be higher.

The bars, as the programmes' pages and guides had them in November 2026:

  YouTube Partner Program, ads on Shorts   1,000 subscribers and 10 million public Shorts views in 90 days
  YouTube, the earlier tier (fan funding)  500 subscribers and 3 million public Shorts views in 90 days
  TikTok Creator Rewards Program           10,000 followers and 100,000 views in 30 days, videos over a minute
  Facebook Content Monetization            by invitation; no public bar
  Instagram                                no open programme that pays for views
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

from . import connectors, store
from .config import Config
from .connectors import ConnectorError
from .state import Ledger

PROGRAMMES = [
    {"platform": "youtube", "name": "YouTube Partner Program (ads on Shorts)", "followers": 1000, "views": 10_000_000, "days": 90, "word": "subscribers",
     "note": "Views of reposted clips without something of your own added are not counted by YouTube."},
    {"platform": "youtube", "name": "YouTube, earlier tier (fan funding)", "followers": 500, "views": 3_000_000, "days": 90, "word": "subscribers", "note": ""},
    {"platform": "tiktok", "name": "TikTok Creator Rewards Program", "followers": 10_000, "views": 100_000, "days": 30, "word": "followers",
     "note": "Only original videos longer than a minute count, and paid posts earn nothing from it."},
    {"platform": "facebook", "name": "Facebook Content Monetization", "followers": 0, "views": 0, "days": 28, "word": "followers",
     "note": "By invitation: Facebook names no numbers. Original videos and steady posting are what it says it looks at."},
    {"platform": "instagram", "name": "Instagram", "followers": 0, "views": 0, "days": 28, "word": "followers",
     "note": "No open programme pays for reel views. What counts here is reach for the other platforms."},
]


def views(ledger: Ledger, platform: str, days: int, now: datetime | None = None) -> int:
    """Views of the posts Clipper sent to a platform within the last `days` days."""
    now = now or datetime.now(timezone.utc)
    total = 0
    for rec in ledger.clips.values():
        post = (rec.get("posts") or {}).get(platform) or {}
        if not isinstance(post.get("views"), (int, float)):
            continue
        try:
            when = datetime.fromisoformat(str(post.get("sent") or rec.get("slot", "")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if now - when <= timedelta(days=days):
            total += int(post["views"])
    return total


def followers(cfg: Config, fresh: bool = False, ask: bool = True) -> dict[str, dict[str, Any]]:
    """Followers per platform: {"count", "from": "platform"|"typed in"|""}. Asked from the platforms at most once an hour;
    with `ask` off nothing is asked and the last answers are used."""
    seen = store.read(cfg, "progress")
    counts: dict[str, Any] = dict(seen.get("followers", {}))
    if ask and (fresh or time.time() - float(seen.get("at", 0)) > 3600):
        for name in cfg.publish.platforms:
            if name not in connectors.ALL:
                continue
            conn = connectors.get(name, cfg)
            if not conn.connected():
                continue
            try:
                found = conn.followers()
            except (ConnectorError, KeyError):
                found = None
            if found is not None:
                counts[name] = found
        store.write(cfg, "progress", {"at": time.time(), "followers": counts})
    out = {}
    for name in cfg.publish.platforms:
        typed = cfg.progress.followers.get(name)
        if isinstance(counts.get(name), int):
            out[name] = {"count": counts[name], "from": "platform"}
        elif isinstance(typed, (int, float)) and not isinstance(typed, bool):
            out[name] = {"count": int(typed), "from": "typed in"}
        else:
            out[name] = {"count": None, "from": ""}
    return out


def report(cfg: Config, ledger: Ledger, fresh: bool = False, now: datetime | None = None, ask: bool = True) -> list[dict[str, Any]]:
    """Every programme of a chosen platform, with what the channel has against what it needs."""
    have = followers(cfg, fresh, ask)
    out = []
    for prog in PROGRAMMES:
        if prog["platform"] not in cfg.publish.platforms:
            continue
        count = have.get(prog["platform"], {}).get("count")
        seen = views(ledger, prog["platform"], prog["days"], now)
        goals = []
        if prog["followers"]:
            goals.append({"label": prog["word"], "have": count, "need": prog["followers"],
                          "share": None if count is None else min(1.0, count / prog["followers"])})
        if prog["views"]:
            goals.append({"label": f"views in {prog['days']} days", "have": seen, "need": prog["views"], "share": min(1.0, seen / prog["views"])})
        out.append({"platform": prog["platform"], "name": prog["name"], "goals": goals, "note": prog["note"],
                    "followers": count, "followers_from": have.get(prog["platform"], {}).get("from", ""), "word": prog["word"], "views": seen, "days": prog["days"],
                    "reached": bool(goals) and all(g["share"] == 1.0 for g in goals)})
    return out


def earnings(cfg: Config, now: datetime | None = None) -> dict[str, float]:
    """Ask YouTube what the channel earned this year and put each month into the money record. Returns the months read."""
    from . import money

    if not cfg.progress.earnings:
        return {}
    conn = connectors.get("youtube", cfg)
    if not conn.connected():
        return {}
    now = now or datetime.now(timezone.utc)
    found = conn.earnings(f"{now.year}-01-01", now.strftime("%Y-%m-%d"))
    for month, amount in found.items():
        money.add(cfg, "income", "YouTube (its estimate)", amount, when=f"{month}-28", note="read from YouTube", key=f"youtube:{month}")
    return found
