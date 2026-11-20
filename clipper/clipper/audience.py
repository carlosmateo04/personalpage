"""Comments: the newest ones on the channel's posts in one place, each with a reply drafted by Claude.

Nothing is answered by itself. The owner reads the draft, changes it or not, and sends it; or marks the
comment as needing no answer. Off unless switched on under Settings, because reading and answering
comments needs further permissions from YouTube and Facebook/Instagram at sign-in. TikTok gives an app
no way to read comments.

Commenters' names are neither kept nor sent to Claude: only what was written, and the clip it was on.

The one thing done unasked, when `audience.credit_comment` is on: after a clip goes out on YouTube the
channel comments once on it with the credit to the source. YouTube gives an app no way to pin a comment.
"""

from __future__ import annotations

from typing import Any

from . import claude, connectors, store
from .config import Config
from .connectors import ConnectorError
from .state import Ledger, now_iso

PLATFORMS = ("youtube", "instagram", "facebook")
LOOK_AT = 12  # the newest posts per platform that are asked about
SYSTEM = """You help the owner of a small fan channel that posts short gameplay clips answer comments on its posts. For each comment you get what was written and the title of the clip it is on. Write a reply the owner could send as it is: one or two short sentences, friendly and plain, in the language of the comment, no hashtags, no emoji unless the comment used them, no promises, no claims about the game you cannot know, and never asking for likes, follows or shares. If a comment is hostile, spam, or needs no answer ("first", a lone emoji), give an empty reply."""
SHAPE = {"type": "object", "properties": {"replies": {"type": "array", "items": {
    "type": "object", "properties": {"id": {"type": "string"}, "reply": {"type": "string"}}, "required": ["id", "reply"], "additionalProperties": False}}},
    "required": ["replies"], "additionalProperties": False}


def inbox(cfg: Config) -> dict[str, Any]:
    kept = store.read(cfg, "audience")
    return {"at": kept.get("at", ""), "items": kept.get("items", []), "problems": kept.get("problems", []), "enabled": cfg.audience.enabled}


def refresh(cfg: Config, ledger: Ledger, draft: bool = True) -> dict[str, Any]:
    """Read the newest unanswered comments and draft a reply to each new one. Returns the inbox."""
    if not cfg.audience.enabled:
        raise ConnectorError("Comments are switched off. Switch them on under Settings, then connect the accounts again.")
    before = {item["key"]: item for item in store.read(cfg, "audience").get("items", [])}
    items: list[dict[str, Any]] = []
    problems: list[str] = []
    for name in PLATFORMS:
        if name not in cfg.publish.platforms or not connectors.get(name, cfg).connected():
            continue
        conn = connectors.get(name, cfg)
        posts = [(rec, rec["posts"][name]) for rec in ledger.clips.values()
                 if (rec.get("posts") or {}).get(name, {}).get("state") == "posted" and rec["posts"][name].get("id") and rec["posts"][name].get("via") == "direct"]
        for rec, post in sorted(posts, key=lambda pair: pair[1].get("sent", ""), reverse=True)[:LOOK_AT]:
            try:
                found = conn.comments(post)
            except ConnectorError as exc:
                problems.append(f"{conn.label}: {exc}")
                break
            for comment in found:
                key = f"{name}:{comment['id']}"
                old = before.get(key, {})
                if comment["answered"] or old.get("state") in ("answered", "skipped"):
                    continue
                items.append({"key": key, "platform": name, "clip": rec["id"], "title": rec.get("title", ""), "comment": comment["id"],
                              "text": comment["text"], "at": comment["at"], "draft": old.get("draft", ""), "state": "open"})
    fresh = [item for item in items if not item["draft"]]
    if draft and fresh and not claude.unavailable(cfg):
        try:
            asked = "\n\n".join(f"id: {item['key']}\nclip: {item['title']}\ncomment: {item['text']}" for item in fresh[:30])
            answer = claude.ask(cfg, SYSTEM, [{"type": "text", "text": asked}], SHAPE, purpose="comments")
            drafts = {str(r.get("id", "")): str(r.get("reply", "")).strip()[:500] for r in answer.get("replies") or [] if isinstance(r, dict)}
            for item in fresh:
                item["draft"] = drafts.get(item["key"], "")
        except claude.ClaudeError as exc:
            problems.append(f"Claude could not draft replies: {exc}")
    kept_closed = [item for item in before.values() if item.get("state") in ("answered", "skipped")][-300:]
    store.write(cfg, "audience", {"at": now_iso(), "items": kept_closed + sorted(items, key=lambda i: i["at"], reverse=True), "problems": problems})
    return {**inbox(cfg), "items": [i for i in inbox(cfg)["items"] if i["state"] == "open"]}


def _close(cfg: Config, key: str, state: str) -> dict[str, Any]:
    kept = store.read(cfg, "audience")
    item = next((i for i in kept.get("items", []) if i["key"] == key), None)
    if item is None:
        raise KeyError(key)
    item["state"] = state
    store.write(cfg, "audience", kept)
    return item


def answer(cfg: Config, ledger: Ledger, key: str, text: str) -> None:
    """Send a reply to a comment, in the owner's words."""
    text = " ".join(text.split())
    if not text:
        raise ConnectorError("Write the reply first.")
    kept = store.read(cfg, "audience")
    item = next((i for i in kept.get("items", []) if i["key"] == key and i["state"] == "open"), None)
    if item is None:
        raise KeyError(key)
    post = ledger.clips[item["clip"]]["posts"][item["platform"]]
    connectors.get(item["platform"], cfg).reply(post, item["comment"], text)
    _close(cfg, key, "answered")


def skip(cfg: Config, key: str) -> None:
    _close(cfg, key, "skipped")


def credit(cfg: Config, rec: dict, log=print) -> bool:
    """Comment once on a clip's YouTube video with the credit to its source. Returns whether a comment was made."""
    post = (rec.get("posts") or {}).get("youtube") or {}
    line = str((rec.get("meta") or {}).get("credit", "")).strip()
    if not (cfg.audience.credit_comment and line and post.get("state") == "posted" and post.get("via") == "direct" and not post.get("credited")):
        return False
    origin = str(rec.get("origin", ""))
    try:
        connectors.get("youtube", cfg).comment(post, f"{line}. Original: {origin}" if origin.startswith("http") else line)
    except ConnectorError as exc:
        log(f"  {rec['id']} the credit comment could not be made on YouTube: {exc}")
        post["credited"] = "failed"  # tried once; not tried again on every look
        return False
    post["credited"] = now_iso()
    return True
