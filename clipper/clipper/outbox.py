"""The outbox: posts waiting to go out, and their delivery.

This is the clipper's own version of what a posting service does. Every finished
clip carries one post per platform:

    rec["posts"] = {"youtube": {"state": "waiting", "via": "direct", "due": "...", ...}, ...}

    waiting     queued, not handed over yet
    sending     being uploaded right now
    scheduled   handed over; the platform publishes it at the due time
    processing  handed over; the platform is still working on it
    posted      out
    draft       handed over as a draft for the user to finish in the platform's own app
    failed      could not be sent; `error` says why

`deliver` sends what is due. A platform that can hold a post until a future time
(YouTube, Facebook) is handed the clip straight away with that time, so it goes
out even if this computer is off then. The others are sent when their time comes,
which needs this computer to be on: the app does it while it is open, and on
Windows a scheduled task does it every few minutes.

A platform is reached directly when its account is connected here, and through
the posting service (Upload-Post) when a key for that is saved and the account
is not connected directly, or the platform is listed in publish.prefer_service.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from . import connectors, monetize, publish, variants
from .config import Config
from .connectors import ConnectorError
from .state import Ledger, Lock, now_iso

Log = Callable[[str], None]

ACTIVE = ("waiting", "sending", "scheduled", "processing")
DONE = ("posted", "draft")
MAX_TRIES = 5
STUCK = 2 * 3600  # seconds after which a post still marked "sending" was interrupted
LEAD = 120  # a due time this close, in seconds, counts as now
FIRST_RETRY = 300  # seconds; doubles with every try


def routes(cfg: Config) -> dict[str, str]:
    """How each chosen platform would be reached right now: "direct", "service", or "" when no account is connected."""
    service = bool(publish.api_key(cfg) and cfg.publish.profile)
    out: dict[str, str] = {}
    for name in cfg.publish.platforms:
        direct = name in connectors.ALL and connectors.get(name, cfg).connected()
        if direct and not (service and name in cfg.publish.prefer_service):
            out[name] = "direct"
        else:
            out[name] = "service" if service else ""
    return out


def settle(rec: dict) -> None:
    """Bring a clip's own status, links and views in line with its posts."""
    posts = rec.get("posts") or {}
    if not posts:
        return
    states = [post["state"] for post in posts.values()]
    rec["status"] = "scheduled" if any(s in ACTIVE for s in states) else "posted" if any(s in DONE for s in states) else "failed"
    rec["links"] = {name: post["link"] for name, post in posts.items() if post.get("link")}
    rec["views"] = {name: post["views"] for name, post in posts.items() if post.get("views") is not None}
    errors = [f"{name}: {post['error']}" for name, post in posts.items() if post["state"] == "failed" and post.get("error")]
    if errors:
        rec["error"] = "; ".join(errors)[:500]
    else:
        rec.pop("error", None)
    rec["publish"] = {"mode": "live", "platforms": list(posts)}


def enqueue(cfg: Config, ledger: Ledger, rec: dict, when: datetime | None, platforms: list[str] | None = None,
            now: datetime | None = None) -> dict:
    """Queue a finished clip for its platforms. `when` is the posting time; None means as soon as possible.

    Returns {"queued": [...], "skipped": {platform: why}}. Raises when nothing could be queued.
    """
    now = now or datetime.now(timezone.utc)
    video = Path(rec.get("file", ""))
    if not video.exists():
        raise publish.PublishError("the clip file is no longer on this computer")
    how = routes(cfg)
    barred = monetize.blocked(cfg, rec)
    posts = rec.setdefault("posts", {})
    queued: list[str] = []
    skipped: dict[str, str] = {}
    for name in cfg.publish.platforms:
        if platforms is not None and name not in platforms:
            continue
        if posts.get(name, {}).get("state") in ACTIVE + DONE:
            skipped[name] = "already sent or queued"
        elif not how.get(name):
            skipped[name] = "no account is connected"
        elif name in (rec.get("unfit") or {}):
            skipped[name] = rec["unfit"][name]  # the clip does not meet the length set for this platform
        elif name in barred:
            skipped[name] = barred[name]  # it breaks one of the platform's rules for paying
        else:
            shown, cut = variants.view(rec, name)  # the platform's own cut of the clip, where it has one
            if not cut.exists():
                why = "this platform's version of the clip is no longer on this computer"
            else:
                why = connectors.get(name, cfg).unfit(shown, float(shown.get("end", 0)) - float(shown.get("start", 0)), cut.stat().st_size) if how[name] == "direct" else ""
            if why:
                skipped[name] = why
            else:
                posts[name] = {"state": "waiting", "via": how[name], "due": (when or now).isoformat(), "tries": 0, "queued": now_iso()}
                queued.append(name)
    if not queued:
        if not posts:
            rec.pop("posts", None)
        reasons = "; ".join(f"{name}: {why}" for name, why in skipped.items())
        raise publish.PublishError(f"nothing could be queued ({reasons})" if reasons else "no platform is chosen for posting")
    rec["slot"] = (when or now).isoformat()
    settle(rec)
    ledger.save()
    return {"queued": queued, "skipped": skipped}


def _failed(post: dict, error: str, counts: dict) -> None:
    post.update(state="failed", error=error[:300])
    counts["failed"] += 1


def _via_service(cfg: Config, rec: dict, now: datetime, log: Log, counts: dict) -> None:
    """Hand every post of this clip that goes through the posting service to it: one upload per version of the clip."""
    posts = rec["posts"]
    by_file: dict[Path, list[str]] = {}
    for name, post in posts.items():
        if post["state"] == "waiting" and post.get("via") == "service":
            by_file.setdefault(variants.view(rec, name)[1], []).append(name)
    for video, names in by_file.items():
        if video.exists():
            _hand_over(cfg, rec, video, names, now, log, counts)


def _hand_over(cfg: Config, rec: dict, video: Path, names: list[str], now: datetime, log: Log, counts: dict) -> None:
    posts = rec["posts"]
    due = max(datetime.fromisoformat(posts[name]["due"]) for name in names)
    try:
        sent = publish.UploadPost(cfg).publish({"id": rec["id"], "meta": rec["meta"]}, video, due if due > now + timedelta(seconds=LEAD) else None, names)
    except publish.PublishError as exc:
        for name in names:
            _failed(posts[name], str(exc), counts)
        log(f"  {rec['id']} could not be handed to the posting service: {exc}")
        return
    for name in names:
        posts[name].update(state="scheduled" if due > now + timedelta(seconds=LEAD) else "processing", id=sent.get("request_id") or "",
                           job=sent.get("job_id") or "", sent=now_iso())
        posts[name].pop("error", None)
        counts["sent"] += 1
    log(f"  {rec['id']} handed to the posting service for {', '.join(names)}")


def deliver(cfg: Config, ledger: Ledger, now: datetime | None = None, log: Log = print, only: str | None = None) -> dict:
    """Send every post that is due. Safe to call from several places: only one delivery runs at a time."""
    now = now or datetime.now(timezone.utc)
    counts = {"sent": 0, "failed": 0, "waiting": 0, "busy": False}
    with Lock(cfg.path("state") / "deliver.lock", wait=0, stale=STUCK) as held:
        if not held:
            counts["busy"] = True
            return counts
        for rec in list(ledger.clips.values()):
            posts = rec.get("posts")
            if not posts or (only and rec["id"] != only):
                continue
            before = {name: post["state"] for name, post in posts.items()}
            _via_service(cfg, rec, now, log, counts)
            for name, post in posts.items():
                shown, video = variants.view(rec, name)
                if post["state"] == "sending":
                    started = datetime.fromisoformat(post.get("started", now_iso()).replace("Z", "+00:00"))
                    if (now - started).total_seconds() > STUCK:
                        # It may or may not have gone out. Sending it again could post it twice.
                        _failed(post, "sending was interrupted; look at the account before sending this again", counts)
                    continue
                if post["state"] != "waiting":
                    continue
                if not video.exists():
                    _failed(post, "the clip file is no longer on this computer", counts)
                    continue
                if post.get("via") != "direct":
                    continue
                due = datetime.fromisoformat(post["due"])
                try:
                    conn = connectors.get(name, cfg)
                except ConnectorError as exc:
                    _failed(post, str(exc), counts)
                    continue
                if (not conn.native_schedule and due > now) or (post.get("next_try") and datetime.fromisoformat(post["next_try"]) > now):
                    counts["waiting"] += 1
                    continue
                post.update(state="sending", started=now_iso())
                ledger.save()  # on record before the upload starts, so a crash cannot lead to a second upload
                try:
                    sent = conn.send(shown, video, due if due > now + timedelta(seconds=LEAD) else None)
                except ConnectorError as exc:
                    post["tries"] = post.get("tries", 0) + 1
                    post["error"] = str(exc)[:300]
                    if exc.reconnect:
                        post.update(state="failed", reconnect=True)
                        counts["failed"] += 1
                    elif exc.retry and post["tries"] < MAX_TRIES:
                        wait = exc.wait or FIRST_RETRY * 2 ** (post["tries"] - 1)
                        post.update(state="waiting", next_try=(now + timedelta(seconds=wait)).isoformat())
                        counts["waiting"] += 1
                    else:
                        post["state"] = "failed"
                        counts["failed"] += 1
                    log(f"  {rec['id']} to {conn.label}: {exc}" + (" (will try again)" if post["state"] == "waiting" else ""))
                except Exception as exc:  # noqa: BLE001 - a fault in one connector must not leave the post marked as sending
                    _failed(post, f"{type(exc).__name__}: {exc}", counts)
                    log(f"  {rec['id']} to {conn.label} failed: {type(exc).__name__}: {exc}")
                else:
                    post.update(state=sent.state, id=sent.id, link=sent.link, note=sent.note, sent=now_iso())
                    for stale in ("error", "next_try", "started", "reconnect"):
                        post.pop(stale, None)
                    counts["sent"] += 1
                    what = {"scheduled": "scheduled on", "draft": "sent as a draft to", "posted": "posted to"}.get(sent.state, "uploaded to")
                    log(f"  {rec['id']} {what} {conn.label}" + (f" ({sent.note})" if sent.note else ""))
                settle(rec)
                ledger.save()
            if before != {name: post["state"] for name, post in posts.items()}:
                settle(rec)
                ledger.save()
    return counts


def refresh(cfg: Config, ledger: Ledger, now: datetime | None = None, log: Log = print) -> int:
    """Ask the platforms what became of the posts: whether they are out, their links and their views."""
    now = now or datetime.now(timezone.utc)
    changed = 0
    for rec in list(ledger.clips.values()):
        posts = rec.get("posts") or {}
        try:
            age = now - datetime.fromisoformat(rec.get("slot", ""))
        except ValueError:
            continue
        if not posts or age.days > 30:
            continue
        asked = False
        for name, post in posts.items():
            if post["state"] not in ("scheduled", "processing", "posted") or not post.get("id") or post.get("via") != "direct":
                continue
            if post["state"] == "posted" and (age.days > 7 or (post.get("link") and post.get("checked", "") > (now - timedelta(hours=3)).isoformat())):
                continue  # old posts are left alone, and fresh ones are not asked about more than every few hours
            try:
                got = connectors.get(name, cfg).status(post)
            except ConnectorError as exc:
                post["status_error"] = str(exc)[:200]
                continue
            post.pop("status_error", None)
            post["checked"] = now.isoformat()
            for field in ("state", "link", "views", "note", "error"):
                if got.get(field) not in (None, ""):
                    post[field] = got[field]
            asked = True
        service = [post for post in posts.values() if post.get("via") == "service" and post["state"] in ("scheduled", "processing", "posted") and not post.get("link")]
        if service and age.total_seconds() >= 0:
            try:
                got = publish.UploadPost(cfg).refresh({"publish": {"request_id": service[0].get("id"), "job_id": service[0].get("job")}})
            except publish.PublishError:
                got = {}
            for name, post in posts.items():
                if post.get("via") == "service" and name in got.get("links", {}):
                    post.update(state="posted", link=got["links"][name])
                if post.get("via") == "service" and name in got.get("views", {}):
                    post["views"] = got["views"][name]
            asked = asked or bool(got)
        if asked:
            settle(rec)
            changed += 1
    ledger.save()
    log(f"status refreshed for {changed} clip(s)")
    return changed


def by_hand(cfg: Config, ledger: Ledger, rec: dict, name: str, link: str = "") -> dict:
    """Record that the user posted this clip on a platform themselves, so it is not sent there as well."""
    if name not in publish.PLATFORMS:
        raise publish.PublishError(f"unknown platform: {name}")
    post = rec.setdefault("posts", {}).get(name, {})
    if post.get("state") in ("sending", "scheduled", "processing"):
        raise publish.PublishError(f"this clip is already on its way to {name}; take it back first")
    if post.get("state") == "posted":
        raise publish.PublishError(f"this clip is already posted on {name}")
    link = link.strip()
    if link and not link.lower().startswith(("https://", "http://")):
        raise publish.PublishError("the link has to start with https://")
    rec["posts"][name] = {"state": "posted", "via": "hand", "sent": now_iso(), "link": link[:300], "note": "posted by hand"}
    settle(rec)
    ledger.save()
    return rec["posts"][name]


def retry(cfg: Config, ledger: Ledger, rec: dict, now: datetime | None = None) -> list[str]:
    """Put a clip's failed posts back in the queue; returns the platforms."""
    now = now or datetime.now(timezone.utc)
    how = routes(cfg)
    again = []
    for name, post in (rec.get("posts") or {}).items():
        if post["state"] == "failed" and how.get(name):
            due = post.get("due", now.isoformat())
            post.clear()
            post.update(state="waiting", via=how[name], due=max(due, now.isoformat()), tries=0, queued=now_iso())
            again.append(name)
    if again:
        settle(rec)
        ledger.save()
    return again


def take_back(cfg: Config, ledger: Ledger, rec: dict) -> list[str]:
    """Remove a clip's posts that have not been handed over yet; returns the platforms.

    A post a platform already holds (scheduled there, or out) cannot be taken back from here.
    """
    posts = rec.get("posts") or {}
    back = [name for name, post in posts.items() if post["state"] in ("waiting", "failed")]
    for name in back:
        del posts[name]
    if back:
        if posts:
            settle(rec)
        else:
            rec.pop("posts", None)
            rec.pop("publish", None)
            rec.pop("error", None)
            rec["status"] = "held"
        ledger.save()
    return back
