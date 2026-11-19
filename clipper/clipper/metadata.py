"""Titles, descriptions and hashtags for each platform.

The title is the clip's own title when it came with one (a Twitch clip, say),
otherwise the punchiest sentence spoken in it. Every caption credits the source.
Wording and hashtags rotate from clip to clip so posts are not carbon copies.
"""

from __future__ import annotations

import hashlib
import re

from .captions import mask_profanity
from .config import Config, Section
from .moments import Candidate, sentences

_FILLER = re.compile(r"^(?:(?:uh|um|so|like|okay|ok|alright|well|and|but|yeah|bro|dude|chat)[, ]+)+", re.I)


def _pick(seed: str, n: int) -> int:
    return int(hashlib.sha1(seed.encode()).hexdigest(), 16) % n


def _shorten(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0].rstrip(",;:- ")
    return cut + "…"


def _tidy(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text).strip().strip('"').strip()
    text = re.sub(r"[.,;:]+$", "", text)
    text = mask_profanity(text)
    first = text.split(" ", 1)[0]
    if first.islower():  # "kai plays" -> "Kai plays", but "xQc" and "GTA" stay as written
        text = text[0].upper() + text[1:]
    return _shorten(text, limit)


def _names(text: str, game: str) -> bool:
    """Whether the text already names the game ("gta6" and "GTA 6" count the same)."""
    squash = lambda t: re.sub(r"[^a-z0-9]", "", t.lower())
    return squash(game) in squash(text)


_NOISE = re.compile(
    r"\[[^\]]*\]|\([^)]*\)|no commentary|full game|walkthrough|gameplay|play ?through|part \d+|episode \d+|#\d+|"
    r"\b(4k|8k|60 ?fps|ultra hd|uhd|hd|hdr|ps5 pro|ps5|xbox series x\|?s?|100%)\b",
    re.I,
)


def clean_source_title(title: str, game: str) -> str:
    """A video's title without the upload boilerplate ("[4K PS5 PRO] - No Commentary", "Part 3", ...)."""
    text = _NOISE.sub(" ", title)
    text = re.sub(r"\s*[-|:\u2013\u2014/]+\s*(?=[-|:\u2013\u2014/]|$)", " ", text)  # separators left dangling
    text = re.sub(r"^\s*[-|:\u2013\u2014/]+\s*|\s*[-|:\u2013\u2014/]+\s*$", "", re.sub(r"\s+", " ", text)).strip()
    squash = lambda t: re.sub(r"[^a-z0-9]", "", t.lower())
    rest = squash(text).replace(squash(game), "").replace("grandtheftautovi", "").replace("grandtheftauto6", "")
    return text if len(rest) >= 6 else ""  # a title that only names the game says nothing


def best_quote(cfg: Config, cand: Candidate, section: Section | None = None, limit: int = 60) -> str:
    """The sentence in the clip that best sells it."""
    cues = [*cfg.moments.hype_words, *(section.cues if section else [])]
    best, best_score = "", float("-inf")
    for s in sentences(cand.words):
        words = cand.words[s.first : s.last + 1]
        text = " ".join(w.w for w in words)
        low = text.lower()
        score = sum(1.0 for c in cues if c in low)
        score += 1.0 if text.endswith(("!", "?")) else 0.0
        score += 1.5 if abs((s.s + s.e) / 2 - cand.peak) < 6 else 0.0
        n = len(words)
        score += 1.0 if 4 <= n <= 11 else (-1.0 if n < 3 else -0.5 * max(0, n - 14) / 4)
        if score > best_score:
            best, best_score = text, score
    return _tidy(_FILLER.sub("", best), limit)


def hashtags(cfg: Config, clip_id: str, count: int, section: Section | None = None) -> list[str]:
    """Campaign-required tags, then the section's, then a rotating pick from the SEO list."""
    fixed = [h if h.startswith("#") else f"#{h}" for h in [*cfg.campaign.required_hashtags, *(section.hashtags if section else [])]]
    fixed = list(dict.fromkeys(fixed))
    pool = [h for h in cfg.seo.hashtags if h.lower() not in {f.lower() for f in fixed}]
    pool.sort(key=lambda h: hashlib.sha1((clip_id + h).encode()).hexdigest())
    return (fixed + pool)[: max(count, len(fixed))]


def build(cfg: Config, cand: Candidate, clip_id: str, creator: str, section: Section | None = None,
          source_title: str = "", origin: str = "", whole: bool = True) -> dict:
    """Everything the renderer and the publisher need to describe one clip.

    The title is, in order of preference: the video's own title when the whole
    video is the clip; a line spoken in the clip; the cleaned-up title of the
    video it was cut from; and failing all that, the game and the section.
    """
    section = section or cfg.section("")
    game = cfg.brand.game
    given = _tidy(clean_source_title(source_title, game), 60)
    spoken = best_quote(cfg, cand, section)
    said = False  # whether the title is something a person in the clip says
    if whole and len(given.split()) >= 2:
        quote = given
    elif len(spoken.split()) >= 3:
        quote, said = spoken, True
    else:
        quote = given if len(given.split()) >= 2 else f"{game} {section.label.title()}"
    variant = _pick(clip_id, 3)
    if creator and said and variant == 1 and creator.lower() not in quote.lower():
        title = f"{creator}: {quote}"
    elif variant == 2 and not _names(quote, game):
        title = f"{quote} ({game})"
    else:
        title = quote
    paid = cfg.campaign.paid
    tag = cfg.campaign.disclosure_tag if paid else ""
    credit = cfg.campaign.credit or (cfg.rights.credit_format.format(creator=creator) if creator else "")

    def caption(n_tags: int) -> str:
        parts = [tag, title]
        if credit:
            parts.append(f"• {credit}")
        parts.append(" ".join(hashtags(cfg, clip_id, n_tags, section)))
        return " ".join(p for p in parts if p)

    kind = section.label.title()
    yt_title = title if _names(title, game) else f"{title} | {game} {kind}"
    yt_lines = []
    if paid:
        partner = f" with {cfg.campaign.name}" if cfg.campaign.name else ""
        yt_lines.append(f"Paid partnership{partner}. {cfg.campaign.disclosure_tag}")
    yt_lines.append(title)
    if credit:
        yt_lines.append(f"{credit}. Original: {origin}" if origin.startswith("http") else credit)
    if cfg.seo.fan_channel_note:
        yt_lines.append(cfg.seo.fan_channel_note)
    yt_lines.append(" ".join(hashtags(cfg, clip_id, 4, section)))
    return {
        "title": title,
        "hook": _shorten(quote, 48),
        "label": f"{cfg.campaign.disclosure_label} · {cfg.campaign.disclosure_tag}" if paid else "",
        "section": section.name,
        "badge": section.label,
        "badge_color": section.color,
        "credit": credit,
        "playlist": section.youtube_playlist_id,
        "tiktok": {"title": caption(5)},
        "instagram": {"title": caption(8)},
        "facebook": {"title": caption(4)},
        "youtube": {
            "title": _shorten(yt_title, 90) + " #shorts",
            "description": "\n\n".join(yt_lines),
            "tags": list(dict.fromkeys(cfg.seo.youtube_tags + ([creator.lower()] if creator else []))),
        },
    }
