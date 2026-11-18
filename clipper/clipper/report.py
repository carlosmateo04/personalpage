"""The daily report: what is scheduled, what was posted, and the links to submit."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import Config
from .state import Ledger


def _cell(text: str) -> str:
    return str(text).replace("|", "/").replace("\n", " ")


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
        lines += ["| When | Clip | Title | Source | Status |", "|---|---|---|---|---|"]
        for c in upcoming:
            when = datetime.fromisoformat(c["slot"]).astimezone(tz).strftime("%a %d %b %H:%M")
            status = c["status"] + (" (dry run)" if c.get("publish", {}).get("mode") == "dry_run" else "")
            lines.append(f"| {when} | `{c['id']}` | {_cell(c.get('title', ''))} | {_cell(c.get('source_name', ''))} | {status} |")
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
                lines.append(f"  - {platform}: {url}{seen}")
    else:
        lines.append("No live post links yet.")
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
