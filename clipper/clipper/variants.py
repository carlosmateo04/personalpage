"""The cut each platform gets.

A clip is made once, at the length the channel wants. Platforms differ in what
they take and what they pay for (`[edits]` in the config): one wants more than a
minute, another is safer under a minute, a third stops at ninety seconds. So each
platform gets its own cut of the clip: the whole clip where it fits, otherwise
the stretch of the allowed length around the moment the clip builds to. Cuts
that come out the same are rendered once and shared.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import Config

SAME = 0.75  # seconds: cuts closer than this to the clip's own are the clip itself


def calm_start(a: float, b: float, graphic: list[float], calm: float, low: float, high: float,
               longest: float = 0.0, shortest: float = 0.0) -> tuple[float, float] | None:
    """Move a cut so that no graphic moment falls in its first `calm` seconds; None when that cannot be done.

    First choice: start earlier (no earlier than `low`), so the moment stays in the clip and simply comes
    later. Second choice: start just after it. `high` is as far as the end may reach, `longest` and
    `shortest` the lengths allowed (0 = no limit).
    """
    early = [g for g in graphic if a <= g < a + calm]
    if calm <= 0 or not early:
        return a, b
    a2 = min(early) - calm - 0.5
    if a2 >= low:
        b2 = min(b, a2 + longest) if longest else b
        if b2 > max(early) + 2 and not any(a2 <= g < a2 + calm for g in graphic):
            return round(a2, 2), round(b2, 2)
    a2 = a
    for _ in range(6):
        hit = [g for g in graphic if a2 <= g < a2 + calm]
        if not hit:
            break
        a2 = max(hit) + 2.0
    else:
        return None
    b2 = min(high, a2 + longest) if longest else b
    if b2 - a2 >= max(shortest, 15.0):
        return round(a2, 2), round(b2, 2)
    return None


def cut_for(cfg: Config, clip: dict, platform: str) -> tuple[tuple[float, float] | None, str]:
    """(start, end) of this platform's cut in the source video, or (None, why it cannot take the clip)."""
    rule = cfg.edits.get(platform) or {}
    start, end = float(clip["start"]), float(clip["end"])
    longest, shortest = float(rule.get("max_len") or 0), float(rule.get("min_len") or 0)
    if longest and end - start > longest:
        # Keep the moment the clip builds to: a little under half of the cut before it, the rest after.
        peak = clip.get("peak")
        peak = float(peak) if isinstance(peak, (int, float)) and start <= peak <= end else (start + end) / 2
        a = min(max(start, peak - longest * 0.4), end - longest)
        b = a + longest
        for word in clip.get("words") or []:  # never through a spoken word: give up the word, not the limit
            if word["s"] < a < word["e"]:
                a = word["e"]
            if word["s"] < b < word["e"]:
                b = word["s"]
        # Every version opens calm: no graphic moment in its first seconds (see clipper/monetize.py).
        quiet = float(cfg.monetize.calm_open) if cfg.monetize.enabled else 0.0
        moved = calm_start(a, b, clip.get("graphic") or [], quiet, start, end, longest, shortest)
        start, end = moved or (a, b)
    if end - start < shortest - 0.5:
        return None, f"the clip is {end - start:.0f} seconds; {platform} is set to take {shortest:.0f} or more"
    return (round(start, 2), round(end, 2)), ""


def plan(cfg: Config, clip: dict, platforms: list[str] | None = None) -> dict[str, Any]:
    """Every platform's cut: {"cuts": {platform: [start, end]}, "unfit": {platform: why}}. Only cuts that differ from the clip's own are listed."""
    cuts: dict[str, list[float]] = {}
    unfit: dict[str, str] = {}
    for name in platforms or cfg.publish.platforms:
        cut, why = cut_for(cfg, clip, name)
        if cut is None:
            unfit[name] = why
        elif abs(cut[0] - clip["start"]) > SAME or abs(cut[1] - clip["end"]) > SAME:
            cuts[name] = list(cut)
    return {"cuts": cuts, "unfit": unfit}


def name_of(video: Path, cut: list[float]) -> Path:
    """The file a cut is rendered to: next to the clip, named after its length ("c1.59s.mp4")."""
    return video.with_name(f"{video.stem}.{round(cut[1] - cut[0])}s.mp4")


def view(rec: dict, platform: str) -> tuple[dict, Path]:
    """A clip as one platform sees it: its own start, end, cover and file where it has a cut of its own."""
    cut = (rec.get("cuts") or {}).get(platform)
    base = Path(rec.get("file", ""))
    if not cut:
        return rec, base
    shifted = dict(rec, start=cut["start"], end=cut["end"])
    cover = rec.get("cover")
    shifted["cover"] = cover - (cut["start"] - rec["start"]) if isinstance(cover, (int, float)) and cut["start"] <= rec["start"] + cover <= cut["end"] else None
    return shifted, Path(cut["file"])


def versions(rec: dict, platforms: list[str]) -> list[dict[str, Any]]:
    """The distinct renders of a clip and which platforms each is for, the clip's own first."""
    groups: dict[str, dict[str, Any]] = {}
    for name in platforms:
        if name in (rec.get("unfit") or {}):
            continue
        shown, video = view(rec, name)
        entry = groups.setdefault(str(video), {"file": str(video), "seconds": round(shown["end"] - shown["start"]), "platforms": []})
        entry["platforms"].append(name)
    return sorted(groups.values(), key=lambda entry: entry["file"] != rec.get("file"))
