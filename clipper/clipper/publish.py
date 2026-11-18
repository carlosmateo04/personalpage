"""Hand finished clips to a posting service.

dry_run  - nothing leaves the machine; the would-be posts are written to out/outbox.json
live     - Upload-Post (https://upload-post.com) schedules the clip on every platform

The API key comes from UPLOAD_POST_API_KEY. In a Claude cloud session the key is
stored as a network secret instead and added to the request by the network proxy,
so a placeholder is sent here.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Config

PLATFORMS = ("tiktok", "instagram", "youtube", "facebook")
_URL_KEYS = ("post_url", "url", "permalink", "link", "share_url")
_VIEW_KEYS = ("views", "view_count", "video_views", "plays", "play_count", "impressions")


class PublishError(RuntimeError):
    pass


def upload_args(cfg: Config, clip: dict, when: datetime) -> dict[str, Any]:
    """Keyword arguments for UploadPostClient.upload_video."""
    meta, pub = clip["meta"], cfg.publish
    args: dict[str, Any] = {
        "title": meta["title"],
        "user": pub.profile,
        "platforms": list(pub.platforms),
        "scheduled_date": when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "async_upload": True,
        "idempotency_key": clip["id"],
    }
    if "tiktok" in pub.platforms:
        args["tiktok_title"] = meta["tiktok"]["title"]
        args["post_mode"] = pub.tiktok_post_mode
        if pub.tiktok_privacy_level:
            args["privacy_level"] = pub.tiktok_privacy_level
    if "instagram" in pub.platforms:
        args["instagram_title"] = meta["instagram"]["title"]
        args["media_type"] = "REELS"
        args["share_to_feed"] = True
    if "facebook" in pub.platforms:
        args["facebook_title"] = meta["facebook"]["title"]
        args["facebook_media_type"] = "REELS"
        if pub.facebook_page_id:
            args["facebook_page_id"] = pub.facebook_page_id
    if "youtube" in pub.platforms:
        args["youtube_title"] = meta["youtube"]["title"]
        args["youtube_description"] = meta["youtube"]["description"]
        args["tags"] = meta["youtube"]["tags"]
        args["categoryId"] = cfg.seo.youtube_category
        args["privacyStatus"] = pub.youtube_privacy
        args["selfDeclaredMadeForKids"] = False
    if cfg.campaign.paid:
        args["paid_partnership"] = True
    return args


def _walk(node: Any):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def extract_links(payload: Any) -> dict[str, str]:
    """Post links per platform from a status response, whatever its exact shape."""
    links: dict[str, str] = {}
    for node in _walk(payload):
        platform = node.get("platform")
        if isinstance(platform, str) and platform.lower() in PLATFORMS:
            url = next((node[k] for k in _URL_KEYS if isinstance(node.get(k), str) and node[k].startswith("http")), None)
            if url:
                links[platform.lower()] = url
        for name in PLATFORMS:
            sub = node.get(name)
            if isinstance(sub, dict):
                url = next((sub[k] for k in _URL_KEYS if isinstance(sub.get(k), str) and sub[k].startswith("http")), None)
                if url:
                    links.setdefault(name, url)
    return links


def extract_views(payload: Any) -> dict[str, int]:
    views: dict[str, int] = {}
    for node in _walk(payload):
        for name in PLATFORMS:
            sub = node.get(name)
            if not isinstance(sub, dict):
                continue
            for inner in _walk(sub):
                value = next((inner[k] for k in _VIEW_KEYS if isinstance(inner.get(k), (int, float))), None)
                if value is not None:
                    views[name] = max(views.get(name, 0), int(value))
    return views


class DryRun:
    name = "dry_run"

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.outbox = cfg.path("out") / "outbox.json"

    def check(self) -> str:
        return "dry run: nothing will be posted"

    def publish(self, clip: dict, video: Path, when: datetime) -> dict:
        entries = json.loads(self.outbox.read_text()) if self.outbox.exists() else []
        entries = [e for e in entries if e["clip"] != clip["id"]]
        entries.append({"clip": clip["id"], "video": str(video), "args": upload_args(self.cfg, clip, when)})
        self.outbox.parent.mkdir(parents=True, exist_ok=True)
        self.outbox.write_text(json.dumps(entries, indent=1, ensure_ascii=False))
        return {"mode": "dry_run"}

    def refresh(self, clip: dict) -> dict:
        return {}


class UploadPost:
    name = "live"

    def __init__(self, cfg: Config):
        from upload_post import UploadPostClient

        if not cfg.publish.profile:
            raise PublishError("publish.profile is empty: set it to the Upload-Post profile your accounts are connected to")
        self.cfg = cfg
        self.client = UploadPostClient(os.environ.get("UPLOAD_POST_API_KEY") or "set-by-network-proxy")

    def check(self) -> str:
        from upload_post import UploadPostError

        try:
            users = self.client.list_users()
        except UploadPostError as exc:
            raise PublishError(f"Upload-Post rejected the request: {exc}") from exc
        names = sorted({n["username"] for n in _walk(users) if isinstance(n.get("username"), str)})
        if self.cfg.publish.profile not in names:
            raise PublishError(f"profile {self.cfg.publish.profile!r} not found; Upload-Post has: {', '.join(names) or 'none'}")
        return f"Upload-Post reachable; profile {self.cfg.publish.profile!r} found"

    def publish(self, clip: dict, video: Path, when: datetime) -> dict:
        from upload_post import UploadPostError

        args = upload_args(self.cfg, clip, when)
        for attempt in range(1, 4):
            try:
                resp = self.client.upload_video(video, **args)
                break
            except UploadPostError as exc:
                # No HTTP status means the connection dropped. The idempotency key
                # makes a retry safe: the service will not post the clip twice.
                if exc.status_code is None and attempt < 3:
                    time.sleep(5 * attempt)
                    continue
                raise PublishError(f"upload failed: {exc}") from exc
        return {
            "mode": "live",
            "request_id": resp.get("request_id"),
            "job_id": resp.get("job_id"),
            "response": {k: v for k, v in resp.items() if isinstance(v, (str, int, float, bool, type(None)))},
        }

    def refresh(self, clip: dict) -> dict:
        from upload_post import UploadPostError

        pub = clip.get("publish", {})
        out: dict[str, Any] = {}
        try:
            if pub.get("request_id"):
                status = self.client.get_status(pub["request_id"])
            elif pub.get("job_id"):
                status = self.client.get_job_status(pub["job_id"])
            else:
                return out
            out["links"] = extract_links(status)
            if isinstance(status.get("status"), str):
                out["state"] = status["status"]
            if out["links"] and pub.get("request_id"):
                out["views"] = extract_views(self.client.get_post_analytics(pub["request_id"]))
        except UploadPostError as exc:
            out["error"] = str(exc)[:300]
        return out


def publisher(cfg: Config):
    return UploadPost(cfg) if cfg.publish.mode == "live" else DryRun(cfg)
