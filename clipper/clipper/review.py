"""Automatic review: Claude looks at each planned clip before it is rendered.

For every clip it is given frames taken evenly across it, the transcript and the
title of the video it came from. It decides whether the clip should be posted at all,
files it under a section, and writes the title, the on-screen headline and a
one-line description. The clip is then rendered and captioned with that text,
so nobody has to edit it by hand.

It needs a Claude API key: ANTHROPIC_API_KEY, or the one saved in the app.
Without a key the clipper carries on with its own automatic titles.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import os
from pathlib import Path
from typing import Callable

from . import metadata, settings
from .config import Config

Log = Callable[[str], None]

# Models that take the server-side refusal fallback. On a policy decline the API
# re-runs the request on a fallback model inside the same call.
FALLBACK_MODELS = {"claude-fable-5-2", "claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}

SYSTEM = """You are the editor of a fan channel that posts short GTA 6 gameplay clips. Each request gives you one candidate clip: one image holding frames taken evenly across the clip (in reading order, left to right and top to bottom), everything said in it, and the title of the video it was cut from. Decide whether it should be posted, and if so describe it.

The channel only posts pure gameplay. Reject a clip (keep = false) when any of these is true:
- A streamer or commentator can be seen (a webcam box, a chat overlay, a person on camera) or heard (someone reacting to the game or talking about it from outside it, as opposed to characters in the game speaking).
- It is a story cutscene, an ending, or gives away a plot point.
- It shows a menu, a loading screen, a map screen or nothing happening for most of the clip.
- It is not GTA 6 gameplay.
- It contains slurs, gambling or political promotion, or someone's personal information.

When you keep a clip:
- section: "findings" for a secret, easter egg or newly discovered detail; "tricks" for a tip or how-to; "glitches" for a bug or broken physics; "moments" for anything else worth watching (a chase, a stunt, a crash, a funny or impressive moment).
- title: what happens in the clip, in plain words, at most 60 characters. No clickbait, no all-caps, no emoji, no hashtags. Do not name the game in it.
- hook: the same idea as a short headline shown on the video for the first seconds, at most 40 characters.
- summary: one sentence saying what the viewer sees.

Describe only what the frames and the transcript show. If you cannot tell what happens, say so in the reason and reject the clip rather than guess. Always give a short reason for your decision."""

SCHEMA = {
    "type": "object",
    "properties": {
        "keep": {"type": "boolean"},
        "reason": {"type": "string"},
        "section": {"type": "string", "enum": ["findings", "tricks", "glitches", "moments"]},
        "title": {"type": "string"},
        "hook": {"type": "string"},
        "summary": {"type": "string"},
    },
    "required": ["keep", "reason", "section", "title", "hook", "summary"],
    "additionalProperties": False,
}


class ReviewError(RuntimeError):
    """The review could not be done for a clip. `fatal` means there is no point trying the rest."""

    def __init__(self, message: str, fatal: bool = False):
        super().__init__(message)
        self.fatal = fatal


def api_key(cfg: Config) -> str:
    return os.environ.get("ANTHROPIC_API_KEY") or settings.get_secret(cfg.data_root, "anthropic_api_key")


def unavailable(cfg: Config) -> str:
    """Why the review cannot run, or empty when it can."""
    if not cfg.review.enabled:
        return "it is switched off in the settings"
    if not api_key(cfg):
        return "no Claude API key is saved (add one in the app under Accounts)"
    if importlib.util.find_spec("anthropic") is None:
        return "the anthropic package is not installed (run the installer again)"
    return ""


def available(cfg: Config) -> bool:
    return not unavailable(cfg)


def check_key(key: str, model: str) -> str:
    """Empty if the key works, otherwise why not. Costs nothing: it only looks the model up."""
    import anthropic

    try:
        anthropic.Anthropic(api_key=key).models.retrieve(model)
    except anthropic.AuthenticationError:
        return "Anthropic did not accept this key."
    except anthropic.PermissionDeniedError:
        return "This key is not allowed to use the review model."
    except anthropic.NotFoundError:
        return f"The model {model} is not available to this key."
    except anthropic.APIStatusError as exc:
        return f"Anthropic answered with an error ({exc.status_code})."
    except anthropic.APIConnectionError:
        return "Could not reach Anthropic. Check the internet connection."
    return ""


def ask(cfg: Config, clip: dict) -> dict:
    """One request to Claude about one clip. Returns the verdict as a dict matching SCHEMA."""
    import anthropic

    content: list[dict] = []
    sheet = Path(clip.get("sheet") or "")
    if sheet.is_file():
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg", "data": base64.standard_b64encode(sheet.read_bytes()).decode("ascii")}})
    said = clip.get("text", "").strip()
    content.append({"type": "text", "text": (
        f"Title of the video this clip was cut from: {clip.get('source_name', '') or 'unknown'}\n"
        f"Channel: {clip.get('creator', '') or 'unknown'}\n"
        f"Length: {clip['end'] - clip['start']:.0f} seconds\n"
        f"Everything said in the clip:\n{said if said else '(nothing is said)'}"
    )})

    request: dict = {
        "model": cfg.review.model,
        "max_tokens": 16000,
        "system": SYSTEM,
        "messages": [{"role": "user", "content": content}],
        "output_config": {"effort": cfg.review.effort, "format": {"type": "json_schema", "schema": SCHEMA}},
        "betas": [],
    }
    if cfg.review.model in FALLBACK_MODELS:
        request["betas"] = ["server-side-fallback-2026-07-01"]
        request["fallbacks"] = "default"

    client = anthropic.Anthropic(api_key=api_key(cfg))
    try:
        response = client.beta.messages.create(**request)
    except anthropic.AuthenticationError as exc:
        raise ReviewError("the Claude API key was not accepted", fatal=True) from exc
    except anthropic.PermissionDeniedError as exc:
        raise ReviewError("the Claude API key may not use the review model", fatal=True) from exc
    except anthropic.NotFoundError as exc:
        raise ReviewError(f"the review model {cfg.review.model} was not found", fatal=True) from exc
    except anthropic.RateLimitError as exc:
        raise ReviewError("Anthropic is rate-limiting this key; the rest of the clips keep their automatic titles", fatal=True) from exc
    except anthropic.APIStatusError as exc:
        raise ReviewError(f"Anthropic answered with an error ({exc.status_code})") from exc
    except anthropic.APIConnectionError as exc:
        raise ReviewError("could not reach Anthropic", fatal=True) from exc

    if response.stop_reason == "refusal":
        raise ReviewError("the review was declined for this clip")
    if response.stop_reason == "max_tokens":
        raise ReviewError("the review ran out of room before answering")
    text = next((block.text for block in response.content if block.type == "text"), "")
    try:
        verdict = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ReviewError("the review did not come back in the expected form") from exc
    if not isinstance(verdict, dict) or not isinstance(verdict.get("keep"), bool):
        raise ReviewError("the review did not come back in the expected form")
    return verdict


def apply(cfg: Config, plan_path: Path, log: Log = print) -> dict:
    """Review every clip in a plan and write the verdicts back into it."""
    data = json.loads(plan_path.read_text(encoding="utf-8"))
    kept = dropped = failed = 0
    for clip in data["clips"]:
        if clip.get("review") or clip.get("drop"):
            continue
        try:
            verdict = ask(cfg, clip)
        except ReviewError as exc:
            failed += 1
            data.setdefault("problems", []).append(f"review of {clip['id']}: {exc}")
            log(f"  review of {clip['id']} failed: {exc}")
            if exc.fatal:
                break
            continue
        reason = str(verdict.get("reason", "")).strip()[:300]
        clip["review"] = {"by": cfg.review.model, "keep": verdict["keep"], "reason": reason}
        if not verdict["keep"]:
            clip["drop"] = True
            dropped += 1
            log(f"  {clip['id']} dropped by the review: {reason}")
            continue
        section = cfg.section(str(verdict.get("section", "")))
        title = metadata.tidy(str(verdict.get("title", "")), 60) or clip["meta"]["title"]
        hook = metadata.tidy(str(verdict.get("hook", "")), 48) or title[:48]
        clip["section"] = section.name
        clip["meta"] = metadata.compose(cfg, clip["id"], clip.get("creator", ""), section, title, hook, clip.get("origin", ""),
                                        summary=str(verdict.get("summary", "")).strip()[:300])
        clip["review"]["summary"] = clip["meta"].get("summary", "")
        kept += 1
        log(f"  {clip['id']} kept [{section.name}]: {title}")
    plan_path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    log(f"review: {kept} kept, {dropped} dropped, {failed} could not be reviewed")
    return {"kept": kept, "dropped": dropped, "failed": failed}
