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


def tidy(text: str, limit: int) -> str:
    """A title as it should be shown: one line, no trailing punctuation, profanity masked, not too long."""
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
    return tidy(_FILLER.sub("", best), limit)


def hashtags(cfg: Config, clip_id: str, count: int, section: Section | None = None) -> list[str]:
    """Campaign-required tags, then the section's, then the game's main tag, then a rotating pick from the SEO list.

    The first tag in `seo.hashtags` is the one people search by (#gta6), so it is on every clip.
    """
    fixed = [h if h.startswith("#") else f"#{h}" for h in
             [*cfg.campaign.required_hashtags, *(section.hashtags if section else []), *cfg.seo.hashtags[:1]]]
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
    given = tidy(clean_source_title(source_title, game), 60)
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
    return compose(cfg, clip_id, creator, section, title, _shorten(quote, 48), origin)


def named(cfg: Config, title: str, section: Section) -> str:
    """The title with the game and the section's search keyword after it, unless it names the game already."""
    game = cfg.brand.game
    return title if _names(title, game) else f"{title} | {game} {section.keyword or section.label.title()}"


def youtube_title(cfg: Config, title: str, section: Section) -> str:
    return _shorten(named(cfg, title, section), 90) + " #shorts"


def search_tags(cfg: Config, tags: list[str] | None) -> list[str]:
    """Clip-specific search phrases, tidied: lower case, no "#", none of the banned terms, five at most."""
    out: list[str] = []
    for tag in tags or []:
        tag = re.sub(r"\s+", " ", str(tag).replace("#", " ")).strip().lower()[:40].strip()
        if len(tag) >= 3 and tag not in out and not any(b in tag for b in cfg.gates.banned_terms):
            out.append(tag)
    return out[:5]


def compose(cfg: Config, clip_id: str, creator: str, section: Section, title: str, hook: str, origin: str = "",
            summary: str = "", tags: list[str] | None = None) -> dict:
    """The captions for every platform, built around a title that has already been decided.

    `build` decides the title by rule; the automatic review calls this with the
    title, headline, one-line summary and search phrases it wrote after looking
    at the clip. Every caption names the game and the section's search keyword
    ("... | GTA 6 Glitch"), because that is what people type to find such clips.
    """
    paid = cfg.campaign.paid
    tag = cfg.campaign.disclosure_tag if paid else ""
    credit = cfg.campaign.credit or (cfg.rights.credit_format.format(creator=creator) if creator else "")
    full = named(cfg, title, section)

    def caption(n_tags: int) -> str:
        parts = [tag, full]
        if credit:
            parts.append(f"• {credit}")
        parts.append(" ".join(hashtags(cfg, clip_id, n_tags, section)))
        return " ".join(p for p in parts if p)

    yt_lines = []
    if paid:
        partner = f" with {cfg.campaign.name}" if cfg.campaign.name else ""
        yt_lines.append(f"Paid partnership{partner}. {cfg.campaign.disclosure_tag}")
    yt_lines.append(full)
    if summary:
        yt_lines.append(summary)
    if credit:
        yt_lines.append(f"{credit}. Original: {origin}" if origin.startswith("http") else credit)
    if cfg.seo.fan_channel_note:
        yt_lines.append(cfg.seo.fan_channel_note)
    yt_lines.append(" ".join(hashtags(cfg, clip_id, 4, section)))
    yt_tags: list[str] = []
    for t in [*search_tags(cfg, tags), *cfg.seo.youtube_tags, *([creator.lower()] if creator else [])]:
        if t not in yt_tags and sum(len(x) + 1 for x in yt_tags) + len(t) <= YOUTUBE_TAGS_LIMIT:
            yt_tags.append(t)
    return {
        "title": title,
        "hook": hook,
        "summary": summary,
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
            "title": youtube_title(cfg, title, section),
            "description": "\n\n".join(yt_lines),
            "tags": yt_tags,
        },
    }


YOUTUBE_TAGS_LIMIT = 450  # YouTube allows 500 characters of tags in all
_HASHTAG = re.compile(r"(?<!\w)#(\w+)")
# Longest caption and most hashtags each platform takes; past YouTube's 15 it ignores every hashtag.
_LIMITS = {"tiktok": (2200, 10), "instagram": (2200, 30), "facebook": (2200, 10)}


def proof(cfg: Config, meta: dict) -> list[str]:
    """What is wrong with a clip's title, captions and tags for search; an empty list means it passes.

    Run on every clip before it can be posted, and again when its text is edited in the app.
    """
    game, problems = cfg.brand.game, []
    title = str(meta.get("title", "")).strip()
    letters = [c for c in title if c.isalpha()]
    if len(title) < 8:
        problems.append("the title is too short to say what happens")
    if len(title) > 70:
        problems.append("the title is longer than 70 characters and will be cut off in feeds")
    if title.endswith("…"):
        problems.append("the title was cut short")
    if len(letters) >= 10 and sum(c.isupper() for c in letters) / len(letters) > 0.6:
        problems.append("the title is mostly capital letters")
    if any(squash == re.sub(r"[^a-z0-9]", "", title.lower()) for squash in
           (re.sub(r"[^a-z0-9]", "", f"{game} {sec.label}".lower()) for sec in cfg.sections)):
        problems.append("the title only names the game and the section; it does not say what happens")

    yt = meta.get("youtube", {})
    yt_title, description, tags = str(yt.get("title", "")), str(yt.get("description", "")), list(yt.get("tags") or [])
    if len(yt_title) > 100:
        problems.append("the YouTube title is over 100 characters")
    if not _names(yt_title, game):
        problems.append(f"the YouTube title does not name {game}")
    if "#shorts" not in yt_title.lower():
        problems.append("the YouTube title does not end in #shorts")
    if len(description) > 5000:
        problems.append("the YouTube description is over 5,000 characters")
    if not 1 <= len(_HASHTAG.findall(description)) <= 15:
        problems.append("the YouTube description needs 1 to 15 hashtags")
    if meta.get("credit") and meta["credit"] not in description:
        problems.append("the YouTube description does not credit the source")
    if len(tags) < 3 or not any(_names(t, game) for t in tags):
        problems.append(f"YouTube needs at least 3 tags, one of them naming {game}")
    if sum(len(t) + 1 for t in tags) > 500:
        problems.append("the YouTube tags are over 500 characters in all")

    for platform, (longest, most) in _LIMITS.items():
        text = str(meta.get(platform, {}).get("title", ""))
        found = [h.lower() for h in _HASHTAG.findall(text)]
        name = platform.capitalize() if platform != "tiktok" else "TikTok"
        if not text:
            problems.append(f"there is no caption for {name}")
            continue
        if len(text) > longest:
            problems.append(f"the {name} caption is over {longest:,} characters")
        if not 3 <= len(found) <= most:
            problems.append(f"the {name} caption needs 3 to {most} hashtags")
        if len(set(found)) != len(found):
            problems.append(f"the {name} caption repeats a hashtag")
        if not _names(text, game):
            problems.append(f"the {name} caption does not name {game}")
        if meta.get("credit") and meta["credit"] not in text:
            problems.append(f"the {name} caption does not credit the source")
        if cfg.campaign.paid and not text.startswith(cfg.campaign.disclosure_tag):
            problems.append(f"the {name} caption does not start with {cfg.campaign.disclosure_tag}")
    everything = " ".join([title, yt_title, description, *(str(meta.get(p, {}).get("title", "")) for p in _LIMITS)]).lower()
    banned = sorted({b for b in cfg.gates.banned_terms if re.search(rf"(?<!\w){re.escape(b.lower())}(?!\w)", everything)})
    if banned:
        problems.append(f"the text uses a banned term: {', '.join(banned)}")
    return problems
