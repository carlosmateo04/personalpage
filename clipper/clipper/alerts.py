"""Messages to the owner's phone: clips are ready, a post failed, a sign-in is running out, a claim came in.

Sent through ntfy (ntfy.sh, or a server of one's own): the phone's ntfy app subscribes to a topic, and
whoever knows the topic's name can send to it and read it. The topic is therefore kept like a password,
with the secrets, and a message says only what happened ("3 clips are ready"): no names of people, no
links into the app, no keys. Nothing is sent unless alerts are switched on and a topic is saved.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import settings, store
from .config import Config

EVENTS = {"ready": "Clips are ready", "failed": "A post or a run failed", "signin": "A sign-in needs renewing", "claim": "A claim, strike or takedown"}
TAGS = {"ready": "clapper", "failed": "warning", "signin": "key", "claim": "rotating_light"}


def topic(cfg: Config) -> str:
    return settings.get_secret(cfg.data_root, "alerts_topic")


def ready(cfg: Config) -> bool:
    return bool(cfg.alerts.enabled and topic(cfg))


def send(cfg: Config, event: str, text: str, once: str = "", post=None) -> bool:
    """Send one message. With `once`, the same message is not sent a second time. Returns whether it went out."""
    if not ready(cfg) or event not in cfg.alerts.events:
        return False
    sent = store.read(cfg, "alerts")
    if once and once in sent:
        return False
    try:
        if post is None:
            import requests

            post = requests.post
        response = post(f"{cfg.alerts.server.rstrip('/')}/{topic(cfg)}", data=text[:500].encode("utf-8"), timeout=15,
                        headers={"Title": f"Clipper: {EVENTS[event]}", "Tags": TAGS[event], "Priority": "high" if event in ("failed", "claim") else "default"})
        ok = 200 <= response.status_code < 300
    except Exception:  # noqa: BLE001 - an alert that cannot be sent must never stop the work it reports on
        return False
    if ok and once:
        recent = {k: v for k, v in sent.items() if v > (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()}
        store.write(cfg, "alerts", {**recent, once: datetime.now(timezone.utc).isoformat()})
    return ok


def after_run(cfg: Config, summary: dict) -> None:
    """What a finished run is worth a message for."""
    made, failed, run = summary.get("clips", 0), summary.get("failed", 0), summary.get("run", "")
    if failed or summary.get("problems"):
        send(cfg, "failed", f"The run made {made} clip(s) and {failed} failed. Open Clipper to see why.", once=f"run-failed:{run}")
    elif made:
        waiting = "They are waiting for you in Clipper." if cfg.publish.hold_for_review or cfg.publish.mode != "live" else "They are queued for posting."
        send(cfg, "ready", f"{made} new clip(s) are ready. {waiting}", once=f"run-ready:{run}")


def signins(cfg: Config, now: datetime | None = None) -> None:
    """A message when a posting sign-in has stopped working or will run out within a week."""
    from . import connectors

    now = now or datetime.now(timezone.utc)
    for name in connectors.names():
        account = connectors.get(name, cfg).account()
        if not account:
            continue
        label = connectors.get(name, cfg).label
        if account.get("problem"):
            send(cfg, "signin", f"{label} needs connecting again: {account['problem']}", once=f"signin-problem:{name}:{now:%Y-%m-%d}")
        elif account.get("expires"):
            try:
                left = datetime.fromisoformat(str(account["expires"]).replace("Z", "+00:00")) - now
            except ValueError:
                continue
            if left < timedelta(days=7):
                send(cfg, "signin", f"The {label} sign-in runs out in {max(0, left.days)} day(s). Connect it again in Clipper under Accounts.",
                     once=f"signin-expiring:{name}:{account['expires']}")
