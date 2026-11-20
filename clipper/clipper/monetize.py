"""What each platform asks of a video before it pays for it, beyond its length, and what the clipper does about each.

The rules were read from the platforms' own pages and from press reports of their
updates, as they stood on 20 November 2026. Each rule below names what the clipper
does with it: some are built into every clip when it is cut and rendered, some
can only be checked and reported, and one (originality) no edit can meet.

  rule                platforms            what the clipper does
  ------------------  -------------------  -----------------------------------------------------------------
  calm opening        YouTube              Graphic game violence in the first 7 seconds earns no ads, in
                                           seconds 8 to 15 limited ads. Claude marks such moments; every
                                           version of a clip is cut so that none falls in its first 15
                                           seconds, and the cover is never taken from one.
  never paid          all                  Sexual content, torture, hate-motivated attacks, violence against
                                           children, attacks on real named people: Claude drops the clip.
  strong language     YouTube, Meta        Masked in the captions and titles; silenced in the sound.
  other apps' marks   TikTok, Instagram,   A clip showing another platform's or channel's watermark, logo or
                      Facebook             QR code is dropped (Claude looks for them).
  music               all                  Music that plays in the clip can be claimed by its owner (the ad
                                           money then goes to them, or the clip is muted or blocked). It is
                                           detected and reported; a setting holds such clips back.
  moving picture      Facebook, Instagram  Static or frozen video is not paid. A clip that is mostly a
                                           still picture is not sent.
  sharp picture       TikTok, Instagram    Blurry, low-resolution video is recommended less. Reported.
  hashtags            Instagram, Facebook  Five at most (Instagram's cap since December 2025).
  no begging          Facebook, Instagram  "Like and share", "follow for more" and the like are demoted.
  paid posts          TikTok               Sponsored videos earn nothing from the Creator Rewards Program.
  originality         all                  Reposted footage with captions, a headline or a new shape is not
                                           paid on any of the four. Only the channel's own footage or its
                                           own commentary meets this. Reported on every clip, never hidden.

`check` turns these into a list per platform for one finished clip. With
`monetize.strict` a platform whose rule a clip breaks is not sent that clip.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from . import variants
from .captions import _PROFANITY
from .config import Config

OK, RISK, BROKEN = "ok", "warn", "fail"
NAMES = {"tiktok": "TikTok", "instagram": "Instagram", "youtube": "YouTube", "facebook": "Facebook"}
HASHTAG_CAP = {"instagram": 5, "facebook": 5, "tiktok": 10, "youtube": 15}
TAGGER_DIR = "sherpa-onnx-zipformer-small-audio-tagging-2024-04-15"
MUSIC_STEP = 5.0  # seconds of sound listened to at a time
MUSIC_SURE = 0.5  # how sure the listener has to be that a stretch has music in it
_HASHTAG = re.compile(r"(?<!\w)#(\w+)")
_BAIT = re.compile(
    r"\b(like (and|&) share|share (this|if)|tag (a|your) friend|follow (me |us )?for more|comment (below|down|your)|smash (the|that) like|"
    r"double tap|link in (my |the )?bio|like if you|subscribe for more|don'?t forget to (like|follow|subscribe))\b", re.I)
_LINK = re.compile(r"https?://|www\.", re.I)


# --- built into the clip -------------------------------------------------------------

def mute_spans(cfg: Config, words: list, pad: float = 0.06) -> list[tuple[float, float]]:
    """The stretches of a clip's sound to silence: every strongly profane word, as (start, end) seconds in the clip."""
    if not (cfg.monetize.enabled and cfg.monetize.mute_profanity):
        return []
    spans: list[tuple[float, float]] = []
    for word in words:
        text, begin, finish = (word.w, word.s, word.e) if hasattr(word, "w") else (word["w"], word["s"], word["e"])
        if _PROFANITY.search(str(text)):
            a, b = max(0.0, begin - pad), finish + pad
            if spans and a <= spans[-1][1]:
                spans[-1] = (spans[-1][0], b)
            else:
                spans.append((round(a, 3), round(b, 3)))
    return spans


def calm(cfg: Config) -> float:
    """How many seconds at the start of every version are kept free of graphic violence."""
    return float(cfg.monetize.calm_open) if cfg.monetize.enabled else 0.0


def safe_cover(length: float, at: float | None, graphic: list[float], clear: float = 2.5) -> float:
    """The moment the cover frame is taken from: the chosen one, or a third of the way in, moved off any graphic moment."""
    want = length / 3 if at is None else float(at)
    if not any(abs(want - g) < clear for g in graphic):
        return want
    steps = [want + sign * n * 0.5 for n in range(1, int(length * 2) + 1) for sign in (1, -1)]
    return next((t for t in steps if 0.5 <= t <= length - 0.5 and not any(abs(t - g) < clear for g in graphic)), want)


# --- listened to and looked at ---------------------------------------------------------

_tagger = None


def tagger_ready(cfg: Config) -> bool:
    return (cfg.models_dir / TAGGER_DIR / "model.int8.onnx").is_file()


def _listener(cfg: Config):
    global _tagger
    if _tagger is None:
        import sherpa_onnx

        folder = cfg.models_dir / TAGGER_DIR
        _tagger = sherpa_onnx.AudioTagging(sherpa_onnx.AudioTaggingConfig(
            model=sherpa_onnx.AudioTaggingModelConfig(
                zipformer=sherpa_onnx.OfflineZipformerAudioTaggingModelConfig(model=str(folder / "model.int8.onnx")),
                num_threads=2, provider="cpu"),
            labels=str(folder / "class_labels_indices.csv"), top_k=600))
    return _tagger


def music(cfg: Config, src: Path, start: float, end: float) -> list[float] | None:
    """How sure the listener is that music plays, for each five seconds of a clip (0 to 1). None when it cannot listen.

    The listener is a small sound-recognition model on this computer; nothing is sent anywhere. It hears
    music of any kind: a radio station in the game as much as the game's own score. It cannot tell which
    is licensed.
    """
    if not (cfg.monetize.enabled and tagger_ready(cfg)):
        return None
    try:
        import numpy as np

        got = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(src),
                              "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"], capture_output=True, timeout=300)
        sound = np.frombuffer(got.stdout, dtype=np.float32)
        if got.returncode != 0 or len(sound) < 16000:
            return None
        listener, size, out = _listener(cfg), int(MUSIC_STEP * 16000), []
        for at in range(0, len(sound), size):
            piece = sound[at:at + size]
            if len(piece) < 16000:  # under a second left over
                break
            stream = listener.create_stream()
            stream.accept_waveform(16000, piece)
            out.append(round(next((e.prob for e in listener.compute(stream) if e.name == "Music"), 0.0), 2))
        return out
    except Exception:  # noqa: BLE001 - a listener that cannot run is a check that was not made, not a failed clip
        return None


def _keyframes(src: Path, start: float, end: float) -> list | None:
    """The clip's keyframes as small grey pictures. Reading only those is quick even for a 4K video."""
    try:
        import numpy as np

        got = subprocess.run(["ffmpeg", "-loglevel", "error", "-skip_frame", "nokey", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(src),
                              "-an", "-vsync", "passthrough", "-vf", "scale=64:36,format=gray", "-f", "rawvideo", "-"], capture_output=True, timeout=300)
    except (OSError, subprocess.SubprocessError, ImportError):
        return None
    if got.returncode != 0:
        return None
    count = len(got.stdout) // (64 * 36)
    return [np.frombuffer(got.stdout, dtype=np.uint8, count=64 * 36, offset=n * 64 * 36).astype(np.int16) for n in range(count)]


def frozen(src: Path, start: float, end: float) -> float | None:
    """The share of a clip (0 to 1) during which the picture does not move. None when it could not be looked at.

    Where the clip has enough keyframes (every few seconds in most videos) they are compared with each
    other; otherwise the whole clip is watched, which takes longer.
    """
    frames = _keyframes(src, start, end)
    if frames is not None and len(frames) >= 6:
        same = sum(1 for one, other in zip(frames, frames[1:]) if float(abs(one - other).mean()) < 1.0)
        return round(same / (len(frames) - 1), 2)
    try:
        got = subprocess.run(["ffmpeg", "-hide_banner", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(src), "-an",
                              "-vf", "fps=5,scale=160:-2,freezedetect=n=0.002:d=2", "-f", "null", "-"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    except (OSError, subprocess.SubprocessError):
        return None
    if got.returncode != 0:
        return None
    still = sum(float(found) for found in re.findall(r"freeze_duration: ([0-9.]+)", got.stderr))
    begun, ended = len(re.findall(r"freeze_start:", got.stderr)), len(re.findall(r"freeze_end:", got.stderr))
    if begun > ended:  # still frozen when the clip ends
        last = float(re.findall(r"freeze_start: ([0-9.]+)", got.stderr)[-1])
        still += max(0.0, (end - start) - last)
    return round(min(1.0, still / max(0.1, end - start)), 2)


def look(cfg: Config, clip: dict) -> dict[str, Any]:
    """What is measured on a clip's source for the checks: its music, how much of it is a still picture, how sharp it is."""
    from . import media

    src, start, end = Path(clip["source_path"]), float(clip["start"]), float(clip["end"])
    found: dict[str, Any] = {"music": music(cfg, src, start, end), "frozen": frozen(src, start, end) if cfg.monetize.enabled else None}
    try:
        found["height"] = media.probe(src).height
    except media.MediaError:
        found["height"] = None
    return found


# --- the check -------------------------------------------------------------------------

def _music_seconds(probs: list[float] | None, offset: float, length: float) -> float | None:
    if probs is None:
        return None
    total = 0.0
    for n, prob in enumerate(probs):
        a, b = n * MUSIC_STEP, (n + 1) * MUSIC_STEP
        if prob >= MUSIC_SURE:
            total += max(0.0, min(b, offset + length) - max(a, offset))
    return total


def strong_words(words: list[dict]) -> list[float]:
    """When strong language is spoken, in seconds of the source video."""
    return [round(float(w["s"]), 2) for w in words if _PROFANITY.search(str(w["w"]))]


def own_footage(rec: dict) -> bool:
    return "own" in str(rec.get("permission", "")).lower()


def check(cfg: Config, rec: dict) -> dict[str, list[dict[str, str]]]:
    """Every rule, for every chosen platform, for one finished clip: [{"rule", "level", "text"}] per platform.

    `rec` is the clip's record: its cut, its text, what Claude saw (`graphic`, `review`), where strong language
    is spoken (`strong`), what was measured (`seen`: music, frozen, height), and the versions the platforms
    get (`cuts`).
    """
    m = cfg.monetize
    if not m.enabled:
        return {}
    meta = rec.get("meta") or {}
    seen = rec.get("seen") or {}
    reviewed = bool(rec.get("review"))
    out: dict[str, list[dict[str, str]]] = {}
    for name in cfg.publish.platforms:
        if name not in NAMES:
            continue
        lines: list[dict[str, str]] = []

        def add(rule: str, level: str, text: str) -> None:
            lines.append({"rule": rule, "level": level, "text": text})

        shown, _ = variants.view(rec, name)
        start, length = float(shown["start"]), float(shown["end"]) - float(shown["start"])
        offset = start - float(rec["start"])

        # Graphic violence at the start (YouTube's ad rules name the seconds).
        if name == "youtube":
            early = sorted(g - start for g in rec.get("graphic") or [] if 0 <= g - start < max(15.0, m.calm_open))
            if not reviewed:
                add("opening", RISK, "not checked for graphic violence in the first 15 seconds (Claude did not look at this clip)")
            elif early and early[0] < 7:
                add("opening", BROKEN, f"graphic violence {early[0]:.0f} seconds in: in the first 7 seconds it earns no ads")
            elif early:
                add("opening", RISK, f"graphic violence {early[0]:.0f} seconds in: in seconds 8 to 15 it earns limited ads")
            else:
                add("opening", OK, "no graphic violence in the first 15 seconds")

        # Strong language.
        strong = sum(1 for at in rec.get("strong") or [] if start <= at <= start + length)
        text_now = " ".join(str(meta.get(key, "")) for key in ("title", "hook")) + " " + str((meta.get(name) or {}).get("title", ""))
        if _PROFANITY.search(text_now):
            add("language", BROKEN, "strong language in the title, headline or caption")
        elif strong and not m.mute_profanity:
            add("language", RISK, f"strong language is heard {strong} time(s) and is not silenced")
        elif strong:
            add("language", OK, f"strong language silenced {strong} time(s) and masked in the captions")
        else:
            add("language", OK, "no strong language")

        # Other platforms' watermarks.
        if reviewed:
            add("marks", OK, "no other platform's watermark or logo seen")
        else:
            add("marks", RISK, "not checked for another platform's watermark (Claude did not look at this clip)")

        # Music.
        heard = _music_seconds(seen.get("music"), offset, length)
        if heard is None:
            add("music", RISK, "not checked for music (the listening model is not installed: run the installer again)")
        elif heard >= 5:
            what = f"music plays for about {heard:.0f} of {length:.0f} seconds; if its owner claims it, "
            if name == "youtube":
                over = length > 60
                add("music", BROKEN if over else RISK, what + ("a Short over a minute is blocked" if over else "the ad money goes to them"))
            else:
                add("music", RISK, what + "the clip can be muted or blocked")
        else:
            add("music", OK, "no music heard")

        # A moving, sharp picture.
        still = seen.get("frozen")
        if still is not None and still > 0.5:
            add("picture", BROKEN, f"the picture stands still for {still:.0%} of the clip; static video is not paid")
        elif seen.get("height") and seen["height"] < m.min_height:
            add("picture", RISK, f"the source is {seen['height']} lines high; under {m.min_height} it looks blurry and is recommended less")
        elif still is not None:
            add("picture", OK, "moving picture, sharp enough")

        # The words that go with it.
        caption = str((meta.get(name) or {}).get("title", "")) if name != "youtube" else str((meta.get("youtube") or {}).get("description", ""))
        tags = len(_HASHTAG.findall(caption))
        if tags > HASHTAG_CAP[name]:
            add("hashtags", BROKEN, f"{tags} hashtags; {NAMES[name]} takes {HASHTAG_CAP[name]} at most")
        else:
            add("hashtags", OK, f"{tags} hashtags")
        everything = f"{meta.get('title', '')} {caption} {(meta.get('youtube') or {}).get('title', '') if name == 'youtube' else ''}"
        bait = _BAIT.search(everything)
        if bait:
            add("bait", BROKEN if name in ("facebook", "instagram") else RISK, f"\"{bait.group(0)}\" asks for likes, shares or follows; such posts are shown less")
        if name != "youtube" and _LINK.search(caption):
            add("bait", RISK, "a web address in the caption; captions with links are shown less")

        # Paid posts.
        if name == "tiktok" and cfg.campaign.paid:
            add("paid", RISK, "a paid post earns nothing from TikTok's Creator Rewards Program")

        # Originality: the rule no edit meets.
        if own_footage(rec):
            add("original", OK, "your own footage")
        elif rec.get("voiceover"):
            add("original", OK, "your own commentary is on it")
        else:
            add("original", RISK, "another channel's footage with captions and a headline: not paid as original without your own commentary or footage")
        out[name] = lines
    return out


def blocked(cfg: Config, rec: dict) -> dict[str, str]:
    """The platforms a clip is not sent to because it breaks one of their rules: {platform: why}."""
    if not (cfg.monetize.enabled and cfg.monetize.strict):
        return {}
    return {name: next(line["text"] for line in lines if line["level"] == BROKEN)
            for name, lines in (rec.get("money") or {}).items() if any(line["level"] == BROKEN for line in lines)}


def holds(cfg: Config, rec: dict) -> str:
    """Why a clip waits for its owner instead of being posted by itself, or empty."""
    m = cfg.monetize
    if not m.enabled or m.music != "hold":
        return ""
    length = float(rec["end"]) - float(rec["start"])
    heard = _music_seconds((rec.get("seen") or {}).get("music"), 0.0, length)
    if heard is not None and heard / max(1.0, length) >= m.music_share:
        return f"music plays for about {heard:.0f} of {length:.0f} seconds"
    return ""


def summary(found: dict[str, list[dict[str, str]]]) -> str:
    """One line for the run's log."""
    broken = [NAMES[name] for name, lines in found.items() if any(line["level"] == BROKEN for line in lines)]
    risks = sorted({line["rule"] for lines in found.values() for line in lines if line["level"] == RISK})
    parts = []
    if broken:
        parts.append("breaks a rule of " + ", ".join(broken))
    if risks:
        parts.append("to know: " + ", ".join(risks))
    return "; ".join(parts) or "every rule met"
