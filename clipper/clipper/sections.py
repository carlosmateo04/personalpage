"""Sort clips into sections (new findings, tricks, glitches, wild moments).

A clip goes to the section whose cue words show up most in its title and in
what is said. With no clear signal it goes to the section its source is filed
under, and failing that to the catch-all section.
"""

from __future__ import annotations

import re

from .config import Config, Section


def _hits(cues: list[str], text: str) -> int:
    return sum(len(re.findall(r"\b" + re.escape(c.lower()) + r"\b", text)) for c in cues)


def classify(cfg: Config, spoken: str, title: str = "", hint: str = "") -> Section:
    spoken_low, title_low = spoken.lower(), title.lower()
    scores = {sec.name: _hits(sec.cues, spoken_low) + 2 * _hits(sec.cues, title_low) for sec in cfg.sections}
    best = max(scores, key=lambda name: scores[name])
    if scores[best] >= 2:
        return cfg.section(best)
    if hint:
        return cfg.section(hint)
    if scores[best] >= 1:
        return cfg.section(best)
    return cfg.section("")


def all_cues(cfg: Config) -> list[str]:
    return [c for sec in cfg.sections for c in sec.cues]


def guide(cfg: Config) -> str:
    """The sections in words, for Claude: one line each with what belongs there."""
    lines = []
    for sec in cfg.sections:
        examples = ", ".join(sec.cues[:6])
        what = f"things like: {examples}" if examples else "anything else worth watching: a chase, a stunt, a crash, a funny or impressive moment"
        lines.append(f'- "{sec.name}" ({sec.label.title()}): {what}')
    return "\n".join(lines)
