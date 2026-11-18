"""Automatic rejects: clips that should never be posted."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from .config import Config
from .moments import Candidate
from .state import Ledger


def normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower())[:600]


def check(cfg: Config, cand: Candidate, source_id: str, ledger: Ledger, whole: bool = False) -> list[str]:
    """Reasons to reject the candidate; an empty list means it passes.

    `whole` marks a short video used in full (a Twitch clip, say): someone already
    chose it as a highlight, so it only needs a little speech and a sane length.
    """
    g, m = cfg.gates, cfg.moments
    reasons: list[str] = []
    shortest, longest = (5.0, m.whole_clip_max) if whole else (m.min_len - 0.5, m.max_len + 1)
    if cand.duration < shortest:
        reasons.append(f"too short ({cand.duration:.0f}s)")
    if cand.duration > longest:
        reasons.append(f"too long ({cand.duration:.0f}s)")
    need = g.min_words_whole if whole else g.min_words
    if len(cand.words) < need:
        reasons.append(f"too little speech ({len(cand.words)} words)")
    spoken = sum(w.e - w.s for w in cand.words)
    if not whole and cand.duration > 0 and spoken / cand.duration < g.min_speech_share:
        reasons.append(f"mostly silence ({spoken / cand.duration:.0%} speech)")
    low = cand.text.lower()
    hit = next((t for t in g.banned_terms if re.search(r"\b" + re.escape(t.lower()) + r"\b", low)), None)
    if hit:
        reasons.append(f"mentions banned term '{hit}'")
    for old in ledger.clips_of(source_id):
        if old.get("status") in ("planned", "failed"):
            continue  # never went out; the moment is still free to use
        overlap = min(cand.end, old["end"]) - max(cand.start, old["start"])
        if overlap > 0 and overlap / min(cand.duration, old["end"] - old["start"]) > g.max_overlap:
            reasons.append(f"overlaps clip {old['id']} already made from this footage")
            break
    text = normalise(cand.text)
    recent = sorted(ledger.clips.values(), key=lambda c: c.get("created", ""))[-300:]
    for old in recent:
        if old.get("source_id") == source_id or not old.get("text") or old.get("status") == "failed":
            continue
        if SequenceMatcher(None, text, old["text"]).ratio() > g.max_text_similarity:
            reasons.append(f"same moment as clip {old['id']}")
            break
    return reasons
