"""Karaoke captions as an ASS subtitle file.

Words are shown two or three at a time. In `highlight` mode the word being
spoken turns the active colour and grows slightly; in `sweep` mode the colour
fills across the line as it is spoken. ffmpeg burns the file into the video.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .config import Captions, Config
from .transcribe import Word

_PROFANITY = re.compile(r"\b(f+u+c+k+\w*|s+h+i+t+\w*|b+i+t+c+h+\w*|a+s+s+h+o+l+e+\w*|c+u+n+t+\w*|d+i+c+k+\w*|p+u+s+s+y+\w*|n+i+g+g+\w*)\b", re.I)
_SENTENCE_END = re.compile(r"[.!?]$")


CALLOUT_SECONDS = 2.8  # how long an on-screen note stays up


def mask_profanity(text: str) -> str:
    return _PROFANITY.sub(lambda m: m.group(0)[0] + "*" * (len(m.group(0)) - 1), text)


def ass_color(hex_color: str, alpha: int = 0) -> str:
    """#RRGGBB -> &HAABBGGRR (ASS byte order)."""
    h = hex_color.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def ass_time(t: float) -> str:
    cs = max(0, int(round(t * 100)))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def display(word: str, cfg: Captions) -> str:
    text = re.sub(r"[{}\\]", "", word)
    text = re.sub(r"[.,;:]+$", "", text)  # keep ? and !, drop the rest
    if cfg.mask_profanity:
        text = mask_profanity(text)
    return text.upper() if cfg.uppercase else text


@dataclass
class Chunk:
    words: list[Word]
    start: float
    end: float


def chunks(words: list[Word], cfg: Captions, clip_len: float) -> list[Chunk]:
    """Group words into short on-screen lines. Times are relative to the clip start."""
    groups: list[list[Word]] = []
    cur: list[Word] = []
    for w in words:
        if not display(w.w, cfg):
            continue
        if cur:
            length = sum(len(display(x.w, cfg)) for x in cur) + len(cur) + len(display(w.w, cfg))
            if len(cur) >= cfg.max_words or length > cfg.max_chars or w.s - cur[-1].e > 0.6 or _SENTENCE_END.search(cur[-1].w):
                groups.append(cur)
                cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    out: list[Chunk] = []
    for i, g in enumerate(groups):
        start = max(0.0, g[0].s)
        nxt = groups[i + 1][0].s if i + 1 < len(groups) else clip_len
        end = min(max(g[-1].e + 0.3, start + 0.25), nxt, clip_len)
        if end > start:
            out.append(Chunk(g, start, end))
    return out


def _wrap(text: str, width: int, max_lines: int = 3) -> str:
    lines: list[str] = []
    cur = ""
    for word in text.split():
        if cur and len(cur) + 1 + len(word) > width:
            lines.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    if cur:
        lines.append(cur)
    return r"\N".join(lines[:max_lines])


def _inline(hex_color: str) -> str:
    """#RRGGBB as an inline ASS colour (&HBBGGRR&)."""
    return "&H" + ass_color(hex_color)[4:] + "&"


def _readable_on(hex_color: str) -> str:
    """Black or white, whichever reads better on the given background."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return "#000000" if 0.299 * r + 0.587 * g + 0.114 * b > 150 else "#FFFFFF"


def build(cfg: Config, words: list[Word], clip_len: float, hook: str = "", label: str = "",
          badge: str = "", badge_color: str = "#FF2E93", credit: str = "", callouts: list[dict] | None = None) -> str:
    """The complete ASS file for one clip. `words` must be timed relative to the clip start.

    badge: the section name shown top-left; label: the paid-partnership notice shown
    top-right; hook: a headline shown for the first seconds; credit: who the footage
    is from, shown under the captions for the whole clip; callouts: short notes
    ({"at": seconds into the clip, "text": ...}) shown where the headline was, each
    for a few seconds.
    """
    c, r = cfg.captions, cfg.render
    W, H = r.width, r.height
    base, active, outline = ass_color(c.color), ass_color(c.active_color), ass_color(c.outline_color)
    styles = [
        # Name, Font, Size, Primary, Secondary, Outline, Back, Bold, Italic, Underline, Strike, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
        f"Style: Karaoke,{c.font},{c.size},{base},{base},{outline},&H80000000,0,0,0,0,100,100,0,0,1,{c.outline},{c.shadow},5,40,40,0,1",
        f"Style: Sweep,{c.font},{c.size},{active},{base},{outline},&H80000000,0,0,0,0,100,100,0,0,1,{c.outline},{c.shadow},5,40,40,0,1",
        f"Style: Hook,{c.font},{int(c.size * 0.62)},&H00000000,&H00000000,&H00FFFFFF,&H00FFFFFF,0,0,0,0,100,100,0,0,3,14,0,5,40,40,0,1",
        f"Style: Label,{c.font},{int(c.size * 0.36)},&H00FFFFFF,&H00FFFFFF,&H60000000,&H60000000,0,0,0,0,100,100,0,0,3,10,0,7,40,40,0,1",
        f"Style: Badge,{c.font},{int(c.size * 0.42)},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,3,12,0,7,40,40,0,1",
        f"Style: Handle,{c.font},{int(c.size * 0.4)},&H50FFFFFF,&H50FFFFFF,&H50000000,&H80000000,0,0,0,0,100,100,0,0,1,3,0,5,40,40,0,1",
        # The headline's twin in reverse: white on black, so a note is not mistaken for the headline.
        f"Style: Callout,{c.font},{int(c.size * 0.56)},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,3,14,0,5,40,40,0,1",
    ]
    x, y = W // 2, int(H * c.y)
    active_tag = "&H" + active[4:] + "&"  # inline colour tags take &HBBGGRR&
    events: list[str] = []

    def event(start: float, end: float, style: str, text: str, layer: int = 0) -> None:
        if end - start >= 0.03:
            events.append(f"Dialogue: {layer},{ass_time(start)},{ass_time(end)},{style},,0,0,0,,{text}")

    top = int(H * 0.078)
    if badge:
        fill, ink = _inline(badge_color), _inline(_readable_on(badge_color))
        event(0, clip_len, "Badge", rf"{{\an7\pos({int(W * 0.045)},{top})\3c{fill}\4c{fill}\c{ink}}}{badge}", 2)
    if label:
        side = rf"\an9\pos({int(W * 0.955)},{top})" if badge else rf"\an7\pos({int(W * 0.045)},{top})"
        event(0, clip_len, "Label", rf"{{{side}}}{label}", 2)
    if hook:
        text = hook.upper() if c.uppercase else hook
        event(0, min(r.hook_seconds, clip_len), "Hook", rf"{{\an5\pos({x},{int(H * 0.138)})\fad(120,200)}}{_wrap(text, 24)}", 2)
    shown_until = min(r.hook_seconds, clip_len) if hook else 0.0
    for note in sorted(callouts or [], key=lambda n: n["at"]):
        text = re.sub(r"[{}\\]", "", str(note["text"])).strip()
        begin = max(float(note["at"]), shown_until + 0.2)  # never over the headline or the note before
        finish = min(begin + CALLOUT_SECONDS, clip_len)
        if text and finish - begin >= 1.0:
            event(begin, finish, "Callout", rf"{{\an5\pos({x},{int(H * 0.138)})\fad(120,200)}}{_wrap(text.upper() if c.uppercase else text, 24)}", 2)
            shown_until = finish
    footer = "  \u2022  ".join(t for t in (re.sub(r"[{}\\]", "", credit), cfg.brand.handle) if t)
    if footer:
        event(0, clip_len, "Handle", rf"{{\an5\pos({x},{min(H - 60, y + int(c.size * 1.9))})}}{footer}", 1)

    for ch in chunks(words, c, clip_len):
        shown = [display(w.w, c) for w in ch.words]
        if c.mode == "sweep":
            parts = []
            cursor = ch.start
            for w, text in zip(ch.words, shown):
                gap = int(round((w.s - cursor) * 100))
                if gap > 0:
                    parts.append(rf"{{\k{gap}}}")
                dur = max(1, int(round((w.e - max(w.s, cursor)) * 100)))
                parts.append(rf"{{\kf{dur}}}{text} ")
                cursor = max(w.e, cursor)
            event(ch.start, ch.end, "Sweep", rf"{{\an5\pos({x},{y})}}" + "".join(parts).rstrip())
            continue
        for k, w in enumerate(ch.words):
            start = ch.start if k == 0 else w.s
            end = ch.words[k + 1].s if k + 1 < len(ch.words) else ch.end
            pieces = []
            for j, text in enumerate(shown):
                if j == k:
                    pieces.append(rf"{{\c{active_tag}\fscx{c.active_scale}\fscy{c.active_scale}}}{text}{{\r}}")
                else:
                    pieces.append(text)
            event(start, end, "Karaoke", rf"{{\an5\pos({x},{y})}}" + " ".join(pieces))

    return "\n".join(
        [
            "[Script Info]",
            "ScriptType: v4.00+",
            f"PlayResX: {W}",
            f"PlayResY: {H}",
            "WrapStyle: 2",
            "ScaledBorderAndShadow: yes",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
            *styles,
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
            *events,
            "",
        ]
    )
