"""The scout: Claude picks the moments worth clipping from a whole video.

The loudness-based finder only hears. Claude is shown the video as sheets of small
frames with their times, what is said, where the sound stands out and what the
finder would pick, and chooses the stretches to clip, each with a title, a
headline, a section, a reason and a score. It also says when a video cannot be
used at all (a streamer on camera, not gameplay).

Its picks are fitted to the clip length limits here, kept apart from each other,
and then go through the same checks as any other candidate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import claude, learn, media, metadata, moments, sections
from .config import Config
from .transcribe import Word

Log = Callable[[str], None]

SYSTEM = """You choose the moments worth clipping from a gameplay video for a fan channel. The channel posts vertical clips of about a minute and a half on TikTok, Instagram, YouTube Shorts and Facebook. Each clip is one continuous stretch of the video, shown with a headline over its first seconds.

You are given the video as sheets of small frames in time order, each frame labelled with its time in the video (minutes:seconds). After the sheets come the video's title, what is said in it with times, the moments where the sound stands out, and the stretches a loudness detector would clip. The detector only hears; you can see. Treat its picks as hints, not as the answer.

What makes a good clip: something a viewer can follow without context and would watch to the end. A chase that escalates, a stunt, a crash, a fight that turns, a glitch, a secret or a detail people have not seen, a trick that is useful. The first seconds decide whether people keep watching, so start a clip just before the action begins, not in the lull before it, and end it once the moment has played out. A stretch where nothing changes is not a clip, however good it looks.

The channel posts pure gameplay only. Do not pick a stretch that is a story cutscene (the game shows these with black bars above and below the picture, usually with close-ups of characters talking), that gives away the plot, or that is a menu, a map or a loading screen. If the video as a whole cannot be used, set usable to false and say why in one sentence in problem; moments is then empty. That is the case when a person is on camera or a chat overlay is on screen, when someone outside the game talks over it, or when it is not gameplay of the game at all.

For each moment:
- start and end: times in the video as minutes:seconds. Choose the start with care. The length limits are given with the video; if the action is shorter, let the clip run on, and if it is longer, keep the best part.
- section: where it belongs (the sections are listed with the video).
- title: what happens, in plain words, at most 60 characters. No clickbait, no capitals for emphasis, no emoji, no hashtags, and do not name the game.
- hook: the same idea as a headline shown on the video, at most 40 characters.
- why: one sentence on what happens and why someone would watch it.
- score: 0 to 100, how strongly you would back this clip. 80 and up is something you would post without a second look; under 50 is filler.

Moments must not overlap. Fewer is fine, and none is fine when nothing in the video earns a clip. Describe only what the frames show or what is said: a wrong title is worse than a plain one."""


def schema(cfg: Config) -> dict:
    return {
        "type": "object",
        "properties": {
            "usable": {"type": "boolean"},
            "problem": {"type": "string"},
            "moments": {"type": "array", "items": {
                "type": "object",
                "properties": {
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "section": {"type": "string", "enum": [sec.name for sec in cfg.sections]},
                    "title": {"type": "string"},
                    "hook": {"type": "string"},
                    "why": {"type": "string"},
                    "score": {"type": "integer"},
                },
                "required": ["start", "end", "section", "title", "hook", "why", "score"],
                "additionalProperties": False,
            }},
        },
        "required": ["usable", "problem", "moments"],
        "additionalProperties": False,
    }


@dataclass
class Scouted:
    usable: bool = True
    problem: str = ""
    cands: list[moments.Candidate] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # picks that could not be used, and why


def said(words: list[Word]) -> str:
    """What is said, one line per sentence with the time it starts."""
    lines = []
    for sent in moments.sentences(words):
        lines.append(f"[{media.clock(sent.s)}] " + " ".join(w.w for w in words[sent.first:sent.last + 1]))
    return "\n".join(lines) if lines else "(nothing is said)"


def step_for(cfg: Config, duration: float) -> float:
    """Seconds between storyboard frames: as fine as the frame budget allows, never finer than 5 seconds."""
    return float(max(5, -(-int(duration) // cfg.ai.max_frames)))


def brief(cfg: Config, title: str, creator: str, duration: float, step: float, words: list[Word],
          spots: list[tuple[float, float]], detector: list[moments.Candidate], limit: int) -> str:
    m = cfg.moments
    loud = ", ".join(f"{media.clock(t)} ({strength:g})" for t, strength in sorted(spots)) or "nothing stands out"
    picks = ", ".join(f"{media.clock(c.start)}-{media.clock(c.end)}" for c in sorted(detector, key=lambda c: c.start)) or "nothing"
    return (
        f"Video title: {title or 'unknown'}\n"
        f"Channel: {creator or 'unknown'}\n"
        f"Game: {cfg.brand.game}\n"
        f"Length: {media.clock(duration)}. The sheets above show one frame every {step:g} seconds.\n\n"
        f"What is said, with times:\n{said(words)}\n\n"
        f"Where the sound stands out (time, strength): {loud}\n"
        f"What the loudness detector would clip: {picks}\n\n"
        f"Sections:\n{sections.guide(cfg)}\n\n"
        f"What the channel wants: {cfg.ai.direction or 'no particular direction'}\n\n"
        + (f"{learned}\n\n" if (learned := learn.brief(cfg)) else "")
        + f"Each clip must run between {m.min_len:g} and {m.max_len:g} seconds. Pick at most {limit} moments."
    )


def candidates(cfg: Config, answer: dict, words: list[Word], duration: float, limit: int) -> Scouted:
    """Claude's picks as clip candidates: fitted to the length limits, strongest first, none overlapping."""
    out = Scouted(usable=bool(answer.get("usable", True)), problem=str(answer.get("problem", "")).strip()[:300])
    if not out.usable:
        return out
    picks = [m for m in answer.get("moments") or [] if isinstance(m, dict)]
    for pick in sorted(picks, key=lambda m: -_score(m)):
        a, b = media.seconds(pick.get("start")), media.seconds(pick.get("end"))
        label = f"{pick.get('start')}-{pick.get('end')}"
        if a is None or b is None or b <= a or a >= duration:
            out.skipped.append(f"{label}: not a stretch of this video")
            continue
        window = moments.fit(cfg.moments, a, b, words, duration)
        if window is None:
            out.skipped.append(f"{label}: too close to the end to make a full clip")
            continue
        start, end = window
        if any(start < c.end + 1 and end > c.start - 1 for c in out.cands):
            out.skipped.append(f"{label}: overlaps a stronger pick")
            continue
        title = metadata.tidy(str(pick.get("title", "")), 60)
        if len(title) < 8:
            out.skipped.append(f"{label}: no usable title")
            continue
        score = _score(pick)
        out.cands.append(moments.Candidate(
            start, end, round((start + end) / 2, 2), round(1.0 + score / 20, 3),
            [w for w in words if w.s >= start - 0.01 and w.e <= end + 0.01],
            ai={"title": title, "hook": metadata.tidy(str(pick.get("hook", "")), 48) or title[:48],
                "section": cfg.section(str(pick.get("section", ""))).name, "why": str(pick.get("why", "")).strip()[:300], "score": score},
        ))
        if len(out.cands) >= limit:
            break
    return out


def _score(pick: dict) -> int:
    try:
        return max(0, min(100, int(pick.get("score") or 0)))
    except (TypeError, ValueError):
        return 0


def find(cfg: Config, video: Path, title: str, creator: str, duration: float, words: list[Word], wav: Path,
         detector: list[moments.Candidate], board_dir: Path, name: str, log: Log = print) -> Scouted:
    """Show Claude the whole video and return its picks. Raises claude.ClaudeError when it cannot be asked."""
    step = step_for(cfg, duration)
    boards = media.storyboard(video, duration, step, board_dir, name)
    spots = moments.loud_spots(cfg.moments, wav, words, cues=sections.all_cues(cfg))
    limit = cfg.moments.max_per_source + 2  # a couple to spare: some picks fail the later checks
    content: list[dict] = []
    for board in boards:
        content.append({"type": "text", "text": f"Frames from {media.clock(board['from'])} to {media.clock(board['to'])}:"})
        content.append(claude.image(Path(board["path"])))
    content.append({"type": "text", "text": brief(cfg, title, creator, duration, step, words, spots, detector, limit)})
    log(f"  Claude is looking at the whole video ({sum(b['frames'] for b in boards)} frames)")
    answer = claude.ask(cfg, SYSTEM, content, schema(cfg), effort=cfg.ai.scout_effort, purpose="scout")
    found = candidates(cfg, answer, words, duration, limit)
    for board in boards:  # the sheets are only for the request
        Path(board["path"]).unlink(missing_ok=True)
    return found
