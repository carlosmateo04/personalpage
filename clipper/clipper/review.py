"""The editor: Claude checks each planned clip and edits it before it is rendered.

For every clip Claude is shown frames from a little before it to a little after
it, each with its time, and what is said. It decides whether the clip is pure
gameplay worth posting, moves the cut to where the action starts, files the clip
under a section, and writes the title, the on-screen headline, a one-line
summary, the search tags and, where nothing is said, short on-screen notes. It
also picks the cover frame and gives the clip a score. The clip is then rendered
with all of that, so nobody has to edit it by hand.

The same step re-edits a finished clip when the app is asked to ("start later",
"a calmer title").

It runs when Claude can be reached (see claude.py). Without it the clipper keeps
its rule-based cut and titles.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable

from . import claude, media, metadata, moments, monetize, sections, variants
from .captions import mask_profanity
from .claude import ClaudeError
from .config import Config
from .transcribe import Word

Log = Callable[[str], None]
ReviewError = ClaudeError  # the earlier name

SYSTEM = """You are the editor of a fan channel that posts short vertical gameplay clips on TikTok, Instagram, YouTube Shorts and Facebook. Each request gives you one clip as it is cut now: a sheet of frames in time order, each labelled with its time in the source video (minutes:seconds), then the video's title, where the clip starts and ends, and what is said with times. The frames reach a little before and after the clip so that you can move the cut.

First decide whether the clip should be posted at all (keep). The channel posts pure gameplay only. Do not keep a clip when:
- a streamer or commentator can be seen (a webcam box, a chat overlay, a person on camera) or heard (someone outside the game reacting to it or talking about it, as opposed to characters in the game speaking);
- it is a story cutscene, an ending, or gives away the plot;
- most of it is a menu, a map, a loading screen, or nothing happening;
- it is not gameplay of the game named with the clip;
- it contains slurs, promotes gambling or politics, or shows someone's personal information;
- the picture carries the watermark, logo or QR code of another platform or channel (a TikTok, Instagram, CapCut or Kwai mark, a channel's logo in a corner, a "subscribe" overlay). Say which in marks. The game's own on-screen display does not count;
- it shows something no platform pays for at any point in a video: sexual content or nudity, sexual violence, torture, an attack motivated by hate, violence against children, or an attack on a real named person. Say which in unsuitable.
Leave marks and unsuitable empty when there is nothing of the kind.
Give a short reason for the decision either way. If you cannot tell what happens in the clip, say so and do not keep it rather than guess.

For a clip you keep, edit it:
- start and end: the cut, as times in the source video (minutes:seconds). Leave it as it is unless moving it makes a better clip: open just before the action begins rather than in a lull, and end once the moment has played out. How far each end may move and how long the clip may be are given with the clip. Never cut into a sentence.
- section: where it belongs (the sections are listed with the clip).
- title: what happens, in plain words, at most 60 characters. No clickbait, no capitals for emphasis, no emoji, no hashtags, and do not name the game.
- hook: the same idea as a headline shown on the video for its first seconds, at most 40 characters.
- summary: one sentence saying what the viewer sees.
- tags: three to five short phrases someone would type into search to find this exact clip, lower case, each starting with the game's name as given with the clip ("gta 6 police chase"). Only things that are in the clip.
- callouts: up to three short notes shown on screen for a few seconds, each with the time it applies (minutes:seconds in the source video) and at most 28 characters of text. Use them where a viewer could miss what is happening or is about to happen ("Six stars", "Watch the helicopter"), and only where nobody is speaking. An empty list is right for most clips.
- graphic: every moment of graphic violence in the clip, each with its time (minutes:seconds in the source video) and two or three words on what it is. Graphic means a brutal killing or severe injury shown close, with blood, dismemberment or torn bodies as the focus. Shooting, crashes, explosions and fights in the ordinary course of the game are not graphic. An empty list is right for most clips. Platforms pay less for a video that opens on such a moment, so where you can, set the start so that the first one comes at least 15 seconds in.
- cover: the time of the frame that would make the best cover image. Never a graphic moment.
- score: 0 to 100, how strongly you would back this clip. 80 and up is something you would post without a second look; under 50 is filler.

For a clip you do not keep, leave the other text fields empty and give a score of 0.

Describe only what the frames and the words show: a wrong title is worse than a plain one."""

CALLOUT_CHARS = 32
CALLOUT_SECONDS = 2.8


def schema(cfg: Config) -> dict:
    return {
        "type": "object",
        "properties": {
            "keep": {"type": "boolean"},
            "reason": {"type": "string"},
            "start": {"type": "string"},
            "end": {"type": "string"},
            "section": {"type": "string", "enum": [sec.name for sec in cfg.sections]},
            "title": {"type": "string"},
            "hook": {"type": "string"},
            "summary": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "callouts": {"type": "array", "items": {
                "type": "object",
                "properties": {"at": {"type": "string"}, "text": {"type": "string"}},
                "required": ["at", "text"],
                "additionalProperties": False,
            }},
            "cover": {"type": "string"},
            "score": {"type": "integer"},
            "marks": {"type": "string"},
            "unsuitable": {"type": "string"},
            "graphic": {"type": "array", "items": {
                "type": "object",
                "properties": {"at": {"type": "string"}, "what": {"type": "string"}},
                "required": ["at", "what"],
                "additionalProperties": False,
            }},
        },
        "required": ["keep", "reason", "start", "end", "section", "title", "hook", "summary", "tags", "callouts", "cover", "score",
                     "marks", "unsuitable", "graphic"],
        "additionalProperties": False,
    }


# --- what the rest of the clipper calls -------------------------------------------

def api_key(cfg: Config) -> str:
    return claude.api_key(cfg)


def unavailable(cfg: Config) -> str:
    """Why the editor cannot run, or empty when it can."""
    return claude.unavailable(cfg) or ("" if cfg.ai.edit else "editing by Claude is switched off in the settings")


def available(cfg: Config) -> bool:
    return not unavailable(cfg)


def _words(clip: dict) -> list[Word]:
    """The words around the clip (it carries its surroundings when it was planned), else the clip's own."""
    source = (clip.get("context") or {}).get("words") or clip.get("words") or []
    return [Word(w["w"], w["s"], w["e"]) for w in source]


def _said(words: list[Word]) -> str:
    lines = [f"[{media.clock(s.s)}] " + " ".join(w.w for w in words[s.first:s.last + 1]) for s in moments.sentences(words)]
    return "\n".join(lines) if lines else "(nothing is said)"


def brief(cfg: Config, clip: dict, instruction: str = "") -> str:
    m, start, end = cfg.moments, clip["start"], clip["end"]
    if clip.get("whole"):
        cut = "This is a short video used whole: leave start and end as they are."
    else:
        cut = (f"Each end may move by up to {cfg.ai.margin:g} seconds, and the clip must stay between "
               f"{m.min_len:g} and {m.max_len:g} seconds.")
    text = (
        f"Game: {cfg.brand.game}\n"
        f"Title of the source video: {clip.get('source_name', '') or 'unknown'}\n"
        f"Channel: {clip.get('creator', '') or 'unknown'}\n"
        f"The clip as cut now: {media.clock(start)} to {media.clock(end)} ({end - start:.0f} seconds).\n"
        f"{cut}\n\n"
        f"Sections:\n{sections.guide(cfg)}\n\n"
        f"What the channel wants: {cfg.ai.direction or 'no particular direction'}\n\n"
        f"What is said (times in the source video):\n{_said(_words(clip))}"
    )
    scouted = clip.get("scout") or {}
    if scouted.get("why"):
        text += f"\n\nWhy this stretch was picked: {scouted['why']}"
    if instruction.strip():
        text += f"\n\nThe channel owner asks for this change to the clip: {instruction.strip()[:500]}"
    return text


def ask(cfg: Config, clip: dict, instruction: str = "") -> dict:
    """One request to Claude about one clip. Returns its verdict and edit, in the shape of `schema`."""
    content: list[dict] = []
    sheet = Path(clip.get("sheet") or "")
    if sheet.is_file():
        content.append(claude.image(sheet))
    content.append({"type": "text", "text": brief(cfg, clip, instruction)})
    verdict = claude.ask(cfg, SYSTEM, content, schema(cfg), effort=cfg.ai.effort, purpose="edit")
    if not isinstance(verdict.get("keep"), bool):
        raise ClaudeError("the answer did not come back in the expected form")
    return verdict


# --- applying an edit ---------------------------------------------------------------

def _cut(cfg: Config, clip: dict, verdict: dict) -> tuple[float, float] | None:
    """The cut Claude asks for, when it differs from the current one and stays within the limits."""
    if clip.get("whole"):
        return None
    a, b = media.seconds(verdict.get("start")), media.seconds(verdict.get("end"))
    if a is None or b is None or b <= a:
        return None
    start, end, margin = clip["start"], clip["end"], cfg.ai.margin
    if abs(a - start) < 1.5 and abs(b - end) < 1.5:  # the times on the frames are whole seconds
        return None
    context = clip.get("context") or {}
    low, high = context.get("from", start), context.get("to", end)
    a = min(max(a, start - margin, low), start + margin)
    b = max(min(b, end + margin, high), end - margin)
    window = moments.fit(cfg.moments, a, b, _words(clip), high)
    if window is None or window[0] < low - 0.5:
        return None
    return window


def _named(value) -> str:
    """What Claude named in a field that is empty when there is nothing: "none", "no" and the like count as nothing."""
    text = re.sub(r"\s+", " ", str(value or "")).strip().strip(".")
    return "" if text.lower() in ("", "none", "no", "nothing", "n/a", "na", "none seen", "none visible") else text[:120]


def _callouts(cfg: Config, verdict: dict, start: float, end: float, words: list[Word]) -> list[dict]:
    """On-screen notes that fit: short, inside the clip, clear of speech and of each other."""
    if not cfg.ai.callouts:
        return []
    out: list[dict] = []
    for note in verdict.get("callouts") or []:
        if not isinstance(note, dict):
            continue
        at = media.seconds(note.get("at"))
        text = mask_profanity(re.sub(r"\s+", " ", str(note.get("text", ""))).strip().strip('"'))
        if at is None or not 3 <= len(text) <= CALLOUT_CHARS:
            continue
        rel = at - start
        if not 0 <= rel <= (end - start) - CALLOUT_SECONDS:
            continue
        if any(b in text.lower() for b in cfg.gates.banned_terms):
            continue
        if any(w.s < at + CALLOUT_SECONDS and w.e > at for w in words):  # someone is speaking then
            continue
        if any(abs(rel - other["at"]) < CALLOUT_SECONDS + 1 for other in out):
            continue
        out.append({"at": round(rel, 1), "text": text})
        if len(out) == 3:  # three at most, in the order Claude gave them
            break
    return sorted(out, key=lambda note: note["at"])


def edit(cfg: Config, clip: dict, verdict: dict) -> str:
    """Write Claude's verdict and edit into a planned clip. Returns what was done, in a few words."""
    reason = str(verdict.get("reason", "")).strip()[:300]
    try:
        score = max(0, min(100, int(verdict.get("score") or 0)))
    except (TypeError, ValueError):
        score = 0
    clip["review"] = {"by": cfg.ai.model, "keep": verdict["keep"], "reason": reason, "score": score}
    if verdict["keep"] and cfg.monetize.enabled:
        # What no platform pays for, or shows to new viewers, is not posted whatever else the clip has going for it.
        marks, unsuitable = _named(verdict.get("marks")), _named(verdict.get("unsuitable"))
        if marks or unsuitable:
            reason = (f"it shows {unsuitable}, which no platform pays for" if unsuitable
                      else f"another platform's or channel's mark is on the picture ({marks})")
            clip["review"].update(keep=False, reason=reason)
            verdict = {**verdict, "keep": False}
    if not verdict["keep"]:
        clip["drop"] = True
        return f"dropped: {reason}"

    moved = _cut(cfg, clip, verdict)
    if moved:
        clip["review"]["cut"] = [clip["start"], clip["end"]]  # where it was
        clip["start"], clip["end"] = moved
        kept = [w for w in _words(clip) if w.s >= moved[0] - 0.01 and w.e <= moved[1] + 0.01]
        clip["words"] = [{"w": w.w, "s": w.s, "e": w.e} for w in kept]
        clip["text"] = " ".join(w.w for w in kept)
    # Graphic moments, in seconds of the source video, and an opening that is clear of them.
    context = clip.get("context") or {}
    times = [media.seconds(note.get("at")) for note in verdict.get("graphic") or [] if isinstance(note, dict)]
    clip["graphic"] = sorted({round(t, 1) for t in times if t is not None and context.get("from", clip["start"]) <= t <= context.get("to", clip["end"])})
    calmed = None
    quiet = monetize.calm(cfg)
    if quiet and clip["graphic"] and not clip.get("whole"):
        low = max(0.0, context.get("from", clip["start"]), clip["start"] - cfg.ai.margin)
        calmed = variants.calm_start(clip["start"], clip["end"], clip["graphic"], quiet, low, clip["end"], cfg.moments.max_len, cfg.moments.min_len)
        if calmed and calmed != (clip["start"], clip["end"]):
            clip["review"].setdefault("cut", [clip["start"], clip["end"]])
            clip["review"]["calm"] = True
            clip["start"], clip["end"] = calmed
            kept = [w for w in _words(clip) if w.s >= calmed[0] - 0.01 and w.e <= calmed[1] + 0.01]
            clip["words"] = [{"w": w.w, "s": w.s, "e": w.e} for w in kept]
            clip["text"] = " ".join(w.w for w in kept)
            moved = calmed
        else:
            calmed = None
    start, end = clip["start"], clip["end"]
    own = [Word(w["w"], w["s"], w["e"]) for w in clip.get("words") or []]
    clip["callouts"] = _callouts(cfg, verdict, start, end, own)
    cover = media.seconds(verdict.get("cover"))
    clip["cover"] = round(cover - start, 1) if cover is not None and 0 <= cover - start <= end - start else None

    section = cfg.section(str(verdict.get("section", "")))
    title = metadata.tidy(str(verdict.get("title", "")), 60) or clip["meta"]["title"]
    hook = metadata.tidy(str(verdict.get("hook", "")), 48) or title[:48]
    clip["section"] = section.name
    clip["meta"] = metadata.compose(cfg, clip["id"], clip.get("creator", ""), section, title, hook, clip.get("origin", ""),
                                    summary=str(verdict.get("summary", "")).strip()[:300],
                                    tags=verdict.get("tags") if isinstance(verdict.get("tags"), list) else None)
    clip["review"]["summary"] = clip["meta"].get("summary", "")
    extras = []
    if moved:
        extras.append(f"cut moved to {media.clock(start)}-{media.clock(end)}")
    if calmed:
        extras.append("opening kept clear of a graphic moment")
    if clip["callouts"]:
        extras.append(f"{len(clip['callouts'])} on-screen note(s)")
    return f"kept [{section.name}, {score}/100]: {title}" + (f" ({', '.join(extras)})" if extras else "")


def apply(cfg: Config, plan_path: Path, log: Log = print) -> dict:
    """Have Claude check and edit every clip in a plan, and write the result back into it."""
    data = json.loads(plan_path.read_text(encoding="utf-8"))
    kept = dropped = failed = 0
    for clip in data["clips"]:
        if clip.get("review") or clip.get("drop"):
            continue
        try:
            verdict = ask(cfg, clip)
        except ClaudeError as exc:
            failed += 1
            data.setdefault("notes", []).append(f"Claude could not edit {clip['id']}: {exc}")
            log(f"  editing {clip['id']} failed: {exc}")
            if exc.fatal:
                break
            continue
        done = edit(cfg, clip, verdict)
        kept += clip["review"]["keep"]
        dropped += not clip["review"]["keep"]
        log(f"  {clip['id']} {done}")
    plan_path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    log(f"review: {kept} kept, {dropped} dropped, {failed} could not be reviewed")
    return {"kept": kept, "dropped": dropped, "failed": failed}
