"""The daily report: what is scheduled, what was posted, and the links to submit."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import Config
from .state import Ledger


def _cell(text: str) -> str:
    return str(text).replace("|", "/").replace("\n", " ")


def post_kit(cfg: Config, clip: dict, slot: str) -> str:
    """Everything needed to post one clip by hand: the text for each platform, ready to paste."""
    meta = clip["meta"]
    when = datetime.fromisoformat(slot).astimezone(ZoneInfo(cfg.schedule.timezone)).strftime("%a %d %b %Y, %H:%M %Z")
    yt = meta["youtube"]
    lines = [
        f"CLIP      {clip['id']}.mp4  ({clip['end'] - clip['start']:.0f} s, section: {clip.get('section') or meta.get('section', '')})",
        f"POST AT   {when}",
        f"FOOTAGE   {clip.get('creator') or 'unknown'}  {clip.get('origin', '')}",
        f"BASIS     {clip.get('permission') or 'no permission on file'}",
        "",
        "=== TikTok ===", meta["tiktok"]["title"], "",
        "=== Instagram Reels ===", meta["instagram"]["title"], "",
        "=== Facebook Reels ===", meta["facebook"]["title"], "",
        "=== YouTube Shorts ===",
        f"Title: {yt['title']}",
        "Description:", yt["description"],
        f"Tags: {', '.join(yt['tags'])}",
        "",
    ]
    if meta.get("label"):
        lines += [f"Paid partnership: switch on each platform's paid-content label ({meta['label']}).", ""]
    return "\n".join(lines)


def build(cfg: Config, ledger: Ledger, last: dict | None = None, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    tz = ZoneInfo(cfg.schedule.timezone)
    clips = sorted(ledger.clips.values(), key=lambda c: c.get("slot", ""))
    lines = [f"# Clip report, {now.astimezone(tz).strftime('%Y-%m-%d %H:%M %Z')}", ""]
    mode = "LIVE" if cfg.publish.mode == "live" else "DRY RUN (nothing is posted)"
    lines += [f"Publishing mode: **{mode}**", ""]
    if last:
        lines += [
            f"Last run `{last.get('run')}`: {last.get('clips', 0)} clip(s) rendered, {last.get('failed', 0)} failed, {last.get('rejected', 0)} candidate(s) rejected.",
            "",
        ]
        if last.get("problems"):
            lines += ["## Problems", ""] + [f"- {p}" for p in last["problems"]] + [""]

    upcoming = [c for c in clips if c.get("status") in ("scheduled", "held") and datetime.fromisoformat(c["slot"]) >= now]
    lines += ["## Scheduled", ""]
    if upcoming:
        lines += ["| When | Section | Title | Credit | Status |", "|---|---|---|---|---|"]
        for c in upcoming:
            when = datetime.fromisoformat(c["slot"]).astimezone(tz).strftime("%a %d %b %H:%M")
            status = c["status"] + (" (dry run)" if c.get("publish", {}).get("mode") == "dry_run" else "")
            lines.append(f"| {when} | {c.get('section', '')} | {_cell(c.get('title', ''))} | {_cell(c.get('creator', '') or '-')} | {status} |")
    else:
        lines.append("Nothing scheduled.")
    lines.append("")

    recent = [c for c in clips if c.get("links") and now - datetime.fromisoformat(c["slot"]) < timedelta(days=7)]
    lines += ["## Links to submit to the campaign (last 7 days)", ""]
    if recent:
        for c in recent:
            views = c.get("views", {})
            lines.append(f"- **{_cell(c.get('title', c['id']))}** (`{c['id']}`)")
            for platform, url in sorted(c["links"].items()):
                seen = f", {views[platform]:,} views" if platform in views else ""
                pay = cfg.campaign.payout(platform, views.get(platform, 0))
                seen += f", about ${pay:.2f}" if pay else ""
                lines.append(f"  - {platform}: {url}{seen}")
    else:
        lines.append("No live post links yet.")
    lines.append("")

    camp = cfg.campaign
    if camp.rate_per_1k > 0:
        def earned(group: list[dict]) -> tuple[int, float]:
            counted = [(p, v) for c in group for p, v in c.get("views", {}).items() if not camp.counted_platforms or p in camp.counted_platforms]
            return sum(v for _, v in counted), sum(camp.payout(p, v) for p, v in counted)

        week_views, week_pay = earned(recent)
        all_views, all_pay = earned(clips)
        terms = f"${camp.rate_per_1k:.2f} per 1,000 views"
        terms += f", ${camp.min_payout:.2f} minimum" if camp.min_payout else ""
        terms += f", ${camp.max_payout:.2f} cap per post" if camp.max_payout else ""
        terms += f", {camp.fee:.0%} fee" if camp.fee else ""
        terms += f", counting {', '.join(camp.counted_platforms)}" if camp.counted_platforms else ""
        lines += [
            f"## Estimated campaign earnings ({camp.name or 'no campaign named'})",
            "",
            f"Terms: {terms}.",
            "",
            "| Period | Counted views | Estimated payout |",
            "|---|---|---|",
            f"| Last 7 days | {week_views:,} | ${week_pay:,.2f} |",
            f"| All time | {all_views:,} | ${all_pay:,.2f} |",
            "",
            "An estimate from view counts. The campaign pays only for posts it approves.",
            "",
        ]

    def fresh(src: dict) -> bool:
        stamp = src.get("processed")
        return bool(stamp) and now - datetime.fromisoformat(stamp.replace("Z", "+00:00")) < timedelta(hours=24)

    used = [src for src in ledger.sources.values() if src.get("status") in ("done", "planned") and fresh(src)]
    if used:
        lines += ["## Footage used in the last 24 hours", "", "| Video | From | Basis for use |", "|---|---|---|"]
        for src in used:
            lines.append(f"| {_cell(src.get('name', ''))} | {_cell(src.get('creator') or '-')} | {_cell(src.get('permission') or 'none on file')} |")
        lines.append("")
    missed = [src for src in ledger.sources.values() if src.get("status") in ("blocked", "failed") and fresh(src)]
    if missed:
        lines += ["## Not fetched", ""]
        lines += [f"- {_cell(src.get('name', ''))}: {_cell(src.get('reason', src['status']))}" for src in missed[:15]]
        lines.append("")
    presented = [src for src in ledger.sources.values() if src.get("status") == "skipped" and fresh(src)
                 and str(src.get("reason", "")).startswith(("streamer facecam", "someone is talking"))]
    if presented:
        lines += ["## Left out: not gameplay-only", ""]
        lines += [f"- {_cell(src.get('name', ''))} ({_cell(src.get('creator') or '-')}): {_cell(src['reason'])}" for src in presented[:20]]
        lines.append("")

    failed = [c for c in clips if c.get("status") == "failed"]
    if failed:
        lines += ["## Failed clips", ""] + [f"- `{c['id']}` {_cell(c.get('title', ''))}: {_cell(c.get('error', ''))}" for c in failed] + [""]

    counts: dict[str, int] = {}
    for c in clips:
        counts[c.get("status", "?")] = counts.get(c.get("status", "?"), 0) + 1
    totals = ", ".join(f"{n} {s}" for s, n in sorted(counts.items())) or "none"
    lines += ["## Totals", "", f"Clips: {totals}. Videos processed: {sum(1 for s in ledger.sources.values() if s.get('status') == 'done')}.", ""]
    return "\n".join(lines)


def write(cfg: Config, ledger: Ledger, last: dict | None = None, now: datetime | None = None) -> Path:
    now = now or datetime.now(timezone.utc)
    text = build(cfg, ledger, last, now)
    folder = cfg.path("reports")
    folder.mkdir(parents=True, exist_ok=True)
    day = now.astimezone(ZoneInfo(cfg.schedule.timezone)).strftime("%Y-%m-%d")
    path = folder / f"{day}.md"
    path.write_text(text)
    (folder / "latest.md").write_text(text)
    return path
