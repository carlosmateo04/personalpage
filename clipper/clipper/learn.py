"""What has worked: the channel's own results steer what is made next.

From the views of the clips that went out, this works out which sections and posting
times do best. Three things use it:

  ranking   when clips are planned, each candidate's score is weighted by how its section has done
            (between 0.75 and 1.3, and only once a section has enough clips to say anything);
  Claude    the scout and the editor are told what has done best on this channel, so they pick
            and title with it in mind;
  the app   shows the figures and the posting times that have done best. Posting times are
            only suggested: the schedule stays the owner's.

Views are a clip's views on all platforms together, read back by the outbox. A clip counts
once it has been out for two days.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

from .config import Config
from .state import Ledger

SETTLED = 2  # days before a clip's views say something


def _results(cfg: Config, ledger: Ledger, now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    zone = ZoneInfo(cfg.schedule.timezone)
    out = []
    for rec in ledger.clips.values():
        views = [int(v) for v in (rec.get("views") or {}).values() if isinstance(v, (int, float))]
        if not views or not rec.get("slot"):
            continue
        try:
            when = datetime.fromisoformat(rec["slot"])
        except ValueError:
            continue
        if now - when < timedelta(days=SETTLED):
            continue
        out.append({"id": rec["id"], "title": str(rec.get("title", "")), "section": str(rec.get("section", "")), "views": sum(views),
                    "hour": when.astimezone(zone).strftime("%H:00"), "seconds": round(float(rec.get("end", 0)) - float(rec.get("start", 0)))})
    return out


def stats(cfg: Config, ledger: Ledger, now: datetime | None = None) -> dict[str, Any]:
    """The figures the app shows: per section and per posting hour, the typical (median) views, and the best clips."""
    found = _results(cfg, ledger, now)

    def grouped(field: str) -> list[dict[str, Any]]:
        groups: dict[str, list[int]] = {}
        for r in found:
            groups.setdefault(r[field], []).append(r["views"])
        return sorted(({"name": name, "clips": len(v), "typical": int(median(v)), "best": max(v)} for name, v in groups.items()),
                      key=lambda g: -g["typical"])

    return {"clips": len(found), "typical": int(median(r["views"] for r in found)) if found else 0, "sections": grouped("section"), "hours": grouped("hour"),
            "top": sorted(found, key=lambda r: -r["views"])[:5], "enough": len(found) >= cfg.learn.min_clips, "needed": cfg.learn.min_clips,
            "weights": weights(cfg, ledger, now)}


def weights(cfg: Config, ledger: Ledger, now: datetime | None = None) -> dict[str, float]:
    """How much each section's candidates are favoured: 1.0 means not at all. Empty until there is enough to go on."""
    if not cfg.learn.enabled:
        return {}
    found = _results(cfg, ledger, now)
    groups: dict[str, list[int]] = {}
    for r in found:
        groups.setdefault(r["section"], []).append(r["views"])
    known = {name: median(v) for name, v in groups.items() if len(v) >= cfg.learn.min_clips}
    overall = sum(known.values()) / len(known) if known else 0  # the sections that can be judged, each counting once
    if len(known) < 2 or overall <= 0:
        return {}
    return {name: round(min(1.3, max(0.75, (typical / overall) ** 0.5)), 2) for name, typical in known.items()}


def brief(cfg: Config, now: datetime | None = None) -> str:
    """What Claude is told about this channel's results; empty until there is enough to go on."""
    if not cfg.learn.enabled:
        return ""
    path = cfg.path("state") / "ledger.json"
    if not path.exists():
        return ""
    found = stats(cfg, Ledger(path), now)
    if not found["enough"]:
        return ""
    lines = ["What has done best on this channel so far (views on all platforms together):"]
    lines += [f"- {r['title']} [{r['section']}]: {r['views']:,}" for r in found["top"]]
    if len(found["sections"]) > 1:
        lines.append("Typical views per section: " + ", ".join(f"{s['name']} {s['typical']:,} ({s['clips']} clips)" for s in found["sections"]))
    lines.append("Use this as a guide to what this audience watches, not as a rule: a strong moment of another kind still beats a weak one of a favoured kind.")
    return "\n".join(lines)
