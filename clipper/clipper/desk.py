"""The campaign desk: which posts are owed to a paid clipping campaign, and what became of each.

A campaign pays per view of posts that follow its brief and are submitted to it. The brief's
rules (hashtags, the ad disclosure, the credit, the pay terms) are the `[campaign]` settings
and go into every caption. This keeps the other half: for each post that went out while a
campaign was on, whether its link has been submitted, approved, rejected or paid, and how much.
A payout marked as paid goes into the money record.
"""

from __future__ import annotations

from typing import Any

from . import money
from .config import Config
from .state import Ledger, now_iso

STATES = {"to_submit": "To submit", "submitted": "Submitted", "approved": "Approved", "rejected": "Rejected", "paid": "Paid"}


def active(cfg: Config) -> bool:
    return bool(cfg.campaign.name or cfg.campaign.rate_per_1k or cfg.campaign.paid)


def rows(cfg: Config, ledger: Ledger) -> list[dict[str, Any]]:
    """Every post that could be submitted: its link, its views, what it would earn by the brief, and where it stands."""
    if not active(cfg):
        return []
    camp, out = cfg.campaign, []
    for rec in sorted(ledger.clips.values(), key=lambda r: r.get("slot", ""), reverse=True):
        for platform, post in (rec.get("posts") or {}).items():
            if post.get("state") != "posted" or (camp.counted_platforms and platform not in camp.counted_platforms):
                continue
            mine = (rec.get("campaign") or {}).get(platform, {})
            views = int(post.get("views") or 0)
            out.append({"clip": rec["id"], "title": rec.get("title", ""), "platform": platform, "link": post.get("link", ""), "views": views,
                        "estimate": camp.payout(platform, views), "status": mine.get("status", "to_submit"), "amount": mine.get("amount", 0.0),
                        "at": mine.get("at", "")})
    return out[:200]


def mark(cfg: Config, ledger: Ledger, clip_id: str, platform: str, status: str, amount: float = 0.0) -> dict[str, Any]:
    """Set where a post stands with the campaign. "paid" with an amount puts the payout into the money record."""
    if status not in STATES:
        raise ValueError("unknown status")
    rec = ledger.clips[clip_id]
    if platform not in (rec.get("posts") or {}):
        raise KeyError(platform)
    try:
        amount = round(float(amount or 0), 2)
    except (TypeError, ValueError) as exc:
        raise ValueError("the amount has to be a number") from exc
    if status == "paid" and amount <= 0:
        raise ValueError("say how much was paid")
    entry = {"status": status, "amount": amount if status == "paid" else 0.0, "at": now_iso()}
    rec.setdefault("campaign", {})[platform] = entry
    ledger.save()
    name = cfg.campaign.name or "Campaign"
    money.add(cfg, "income", f"{name} (campaign)", entry["amount"], note=f"{rec.get('title', clip_id)} on {platform}", key=f"campaign:{clip_id}:{platform}")
    return entry


def totals(cfg: Config, ledger: Ledger) -> dict[str, Any]:
    found = rows(cfg, ledger)
    return {"posts": len(found), "to_submit": sum(1 for r in found if r["status"] == "to_submit"),
            "estimate": round(sum(r["estimate"] for r in found if r["status"] not in ("rejected", "paid")), 2),
            "paid": round(sum(r["amount"] for r in found if r["status"] == "paid"), 2)}
