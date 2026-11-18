"""Titles, descriptions and hashtags for each platform.

The title is the punchiest sentence spoken in the clip. Wording and hashtags
rotate from clip to clip so that posts are not carbon copies of each other.
"""

from __future__ import annotations

import hashlib
import re

from .captions import mask_profanity
from .config import Config
from .moments import Candidate, sentences

_FILLER = re.compile(r"^(?:(?:uh|um|so|like|okay|ok|alright|well|and|but|yeah|bro|dude|chat)[, ]+)+", re.I)


def _pick(seed: str, n: int) -> int:
    return int(hashlib.sha1(seed.encode()).hexdigest(), 16) % n


def _shorten(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0].rstrip(",;:- ")
    return cut + "…"


def best_quote(cfg: Config, cand: Candidate, limit: int = 60) -> str:
    """The sentence in the clip that best sells it."""
    hype = [h for h in cfg.moments.hype_words]
    best, best_score = "", float("-inf")
    for s in sentences(cand.words):
        words = cand.words[s.first : s.last + 1]
        text = " ".join(w.w for w in words)
        low = text.lower()
        score = sum(1.0 for h in hype if h in low)
        score += 1.0 if text.endswith(("!", "?")) else 0.0
        score += 1.5 if abs((s.s + s.e) / 2 - cand.peak) < 6 else 0.0
        n = len(words)
        score += 1.0 if 4 <= n <= 11 else (-1.0 if n < 3 else -0.5 * max(0, n - 14) / 4)
        if score > best_score:
            best, best_score = text, score
    quote = _FILLER.sub("", best).strip()
    quote = re.sub(r"[.,;:]+$", "", quote)
    quote = mask_profanity(quote)
    if quote:
        quote = quote[0].upper() + quote[1:]
    return _shorten(quote, limit)


def hashtags(cfg: Config, clip_id: str, count: int) -> list[str]:
    """Campaign-required tags first, then a rotating pick from the SEO list."""
    required = [h if h.startswith("#") else f"#{h}" for h in cfg.campaign.required_hashtags]
    pool = [h for h in cfg.seo.hashtags if h.lower() not in {r.lower() for r in required}]
    pool.sort(key=lambda h: hashlib.sha1((clip_id + h).encode()).hexdigest())
    return (required + pool)[: max(count, len(required))]


def build(cfg: Config, cand: Candidate, clip_id: str, creator: str) -> dict:
    """Everything the publisher needs to describe one clip."""
    quote = best_quote(cfg, cand) or f"{cfg.brand.game} moment"
    game = cfg.brand.game
    variant = _pick(clip_id, 3)
    if creator and variant == 1:
        title = f"{creator}: {quote}"
    elif variant == 2:
        title = f"{quote} ({game})"
    else:
        title = quote
    paid = cfg.campaign.paid
    tag = cfg.campaign.disclosure_tag if paid else ""
    credit = cfg.campaign.credit or (f"Clip from {creator}" if creator else "")

    def caption(n_tags: int) -> str:
        parts = [tag, title]
        if credit:
            parts.append(f"• {credit}")
        parts.append(" ".join(hashtags(cfg, clip_id, n_tags)))
        return " ".join(p for p in parts if p)

    yt_title = _shorten(f"{title} | {game}" if game.lower() not in title.lower() else title, 90) + " #shorts"
    yt_lines = []
    if paid:
        partner = f" with {cfg.campaign.name}" if cfg.campaign.name else ""
        yt_lines.append(f"Paid partnership{partner}. {cfg.campaign.disclosure_tag}")
    yt_lines.append(title)
    if credit:
        yt_lines.append(credit)
    if cfg.seo.fan_channel_note:
        yt_lines.append(cfg.seo.fan_channel_note)
    yt_lines.append(" ".join(hashtags(cfg, clip_id, 4)))
    return {
        "title": title,
        "hook": _shorten(quote, 48),
        "label": f"{cfg.campaign.disclosure_label} · {cfg.campaign.disclosure_tag}" if paid else "",
        "tiktok": {"title": caption(5)},
        "instagram": {"title": caption(8)},
        "facebook": {"title": caption(4)},
        "youtube": {
            "title": yt_title,
            "description": "\n\n".join(yt_lines),
            "tags": list(dict.fromkeys(cfg.seo.youtube_tags + ([creator.lower()] if creator else []))),
        },
    }
