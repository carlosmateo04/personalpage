"""TikTok, through its Content Posting API.

Sign-in is Login Kit for desktop apps: PKCE (TikTok wants the challenge as the
hex SHA-256 of the verifier, not the usual base64url), with the browser sent
back to this computer. The redirect address has to be registered in the TikTok
developer app exactly as it is used here.

Three ways to post, chosen under Settings (`publish.tiktok_direct`):

  draft    the video is delivered to the account's TikTok inbox; the user opens the
           notification in the TikTok app, adds the caption and posts it. A caption
           cannot be sent along, and at most five drafts may be waiting at a time.
  private  the video is posted for the account owner's eyes only (SELF_ONLY), and
           only while the account itself is set to private.
  public   the video is posted with the caption, for the audience chosen on the clip
           (everyone, friends, only me) and with comments, duets and stitches allowed
           or not. TikTok accepts this only from an app it has audited; from any
           other app it refuses, and the clip then says so.

What an app registered by its own user can do without the audit is the first two.
This connector does what TikTok's Content Posting API offers and nothing else: it
does not imitate the TikTok app or drive a signed-in browser to get around the
audit. TikTok has no scheduling, so a clip is sent when its time comes.

Before every post TikTok's rules require asking the account what it allows
(creator_info): the audiences it offers, whether it has comments, duets or
stitches switched off, and how long a video it may post. A post never asks for
more than that.

Written from developers.tiktok.com/doc as it read on 20 November 2026 (Login Kit
for Desktop, Content Posting API reference).
"""

from __future__ import annotations

import hashlib
import secrets
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from ..state import now_iso
from .base import UPLOAD_TIMEOUT, Connector, ConnectorError, Sent

# TikTok's refusals in the app's words: error.code -> (message, retry, reconnect, seconds to wait)
REFUSALS: dict[str, tuple[str, bool, bool, int]] = {
    "access_token_invalid": ("the TikTok sign-in was not accepted: connect TikTok again", False, True, 0),
    "scope_not_authorized": ("the TikTok sign-in does not allow this: connect TikTok again and allow what it asks for", False, True, 0),
    "scope_permission_missed": ("the TikTok app is missing a scope it needs (add it in the TikTok developer site, then connect again)", False, True, 0),
    "rate_limit_exceeded": ("TikTok asked to slow down", True, False, 180),
    "spam_risk_too_many_posts": ("TikTok's daily limit of posts from an app is reached for this account", True, False, 6 * 3600),
    "spam_risk_user_banned_from_posting": ("TikTok is not letting this account post at the moment", False, False, 0),
    "reached_active_user_cap": ("the TikTok app has reached its daily limit of accounts", True, False, 6 * 3600),
    "unaudited_client_can_only_post_to_private_accounts": (
        "TikTok has not audited this app, so it only lets it post privately, and only to an account that is set to private. "
        "Choose draft under Settings, post this clip by hand, or use the posting service for TikTok",
        False, False, 0),
    "spam_risk_too_many_pending_share": ("five drafts are already waiting in the TikTok inbox; post or delete one in the TikTok app", True, False, 3600),
    "privacy_level_option_mismatch": ("TikTok does not offer the chosen privacy level for this account", False, False, 0),
    "invalid_file_upload": ("TikTok did not accept the video file", False, False, 0),
    "internal_error": ("TikTok had a problem of its own", True, False, 0),
}
FAILED = {"file_format_check_failed": "the file format is not supported", "duration_check_failed": "the video is too long or too short for TikTok",
          "frame_rate_check_failed": "the frame rate is not supported", "picture_size_check_failed": "the picture size is not supported",
          "internal": "TikTok had a problem of its own", "video_pull_failed": "TikTok could not fetch the video",
          "publish_cancelled": "the post was cancelled in TikTok", "auth_removed": "the access was withdrawn before the post finished",
          "spam_risk_too_many_posts": "TikTok's daily limit of posts is reached", "spam_risk_user_banned_from_posting": "the account may not post right now",
          "spam_risk_text": "TikTok flagged the caption", "spam_risk": "TikTok flagged the post"}


AUDIENCES = {"PUBLIC_TO_EVERYONE": "Everyone", "MUTUAL_FOLLOW_FRIENDS": "Friends", "FOLLOWER_OF_CREATOR": "Followers", "SELF_ONLY": "Only me"}
UPLOAD_PAGE = "https://www.tiktok.com/tiktokstudio/upload"  # where a clip is posted by hand


def plan_chunks(size: int, chunk: int) -> tuple[int, int, list[tuple[int, int]]]:
    """How TikTok wants a file cut up: (chunk_size, total_chunk_count, [(first byte, last byte), ...]).

    A file of up to 64 MB goes in one piece. A larger one goes in equal chunks, the count
    being the size divided by the chunk size rounded down, and the last chunk takes
    whatever is left over.
    """
    if size <= 64_000_000:
        return size, 1, [(0, size - 1)]
    count = size // chunk
    spans = [(n * chunk, (n + 1) * chunk - 1) for n in range(count - 1)]
    spans.append(((count - 1) * chunk, size - 1))
    return chunk, count, spans


class TikTok(Connector):
    name, label = "tiktok", "TikTok"
    native_schedule = False
    redirect_path = "/oauth/tiktok/"

    AUTH = "https://www.tiktok.com/v2/auth/authorize/"
    API = "https://open.tiktokapis.com"
    CHUNK = 10_000_000
    TRIES = 3  # per chunk
    PAUSE = 2.0

    def __init__(self, cfg):
        super().__init__(cfg)
        self._token: tuple[str, float] | None = None

    @property
    def mode(self) -> str:
        return self.cfg.publish.tiktok_direct

    def scopes(self) -> list[str]:
        return ["user.info.basic", "video.upload" if self.mode == "draft" else "video.publish"]

    def options(self, clip: dict | None = None) -> dict[str, Any]:
        """What a post asks for: the clip's own choices where it has them, else the settings."""
        pub, own = self.cfg.publish, (clip or {}).get("tiktok") or {}
        audience = "SELF_ONLY" if self.mode == "private" else str(own.get("audience") or pub.tiktok_privacy_level or "PUBLIC_TO_EVERYONE")
        return {"audience": audience, "comments": bool(own.get("comments", pub.tiktok_comments)), "duet": bool(own.get("duet", pub.tiktok_duet)),
                "stitch": bool(own.get("stitch", pub.tiktok_stitch)), "own_brand": bool(own.get("own_brand", False)),
                "paid": bool(own.get("paid", self.cfg.campaign.paid))}

    def creator(self) -> dict[str, Any]:
        """What the account allows right now; remembered for the app to offer the same choices."""
        found = self._post("/v2/post/publish/creator_info/query/", None, "reading the account")
        self.remember(audiences=list(found.get("privacy_level_options") or []), name=str(found.get("creator_nickname") or self.account().get("name", "")),
                      longest=int(found.get("max_video_post_duration_sec") or 0), checked=now_iso())
        return found

    # --- sign-in -------------------------------------------------------------------

    def auth_url(self, redirect_uri: str, state: str) -> tuple[str, dict[str, Any]]:
        app = self.app()
        if not app["ready"]:
            raise ConnectorError("Save the TikTok app's client key and secret first.")
        verifier = secrets.token_urlsafe(64)[:96]
        query = {"client_key": app["client_id"], "response_type": "code", "scope": ",".join(self.scopes()), "redirect_uri": redirect_uri,
                 "state": state, "code_challenge": hashlib.sha256(verifier.encode("ascii")).hexdigest(), "code_challenge_method": "S256"}
        return f"{self.AUTH}?{urlencode(query)}", {"verifier": verifier, "mode": self.mode}

    def _token_request(self, fields: dict[str, str]) -> dict[str, Any]:
        fields = {"client_key": self.app()["client_id"], "client_secret": self.secret("client_secret"), **fields}
        response = self.call("POST", f"{self.API}/v2/oauth/token/", data=fields, headers={"Cache-Control": "no-cache"})
        data = self.body(response)
        if response.status_code >= 500:
            raise ConnectorError("TikTok's sign-in service is not answering", retry=True)
        if not data.get("access_token"):
            problem, detail = str(data.get("error") or f"status {response.status_code}"), str(data.get("error_description", ""))[:160]
            if fields.get("grant_type") == "refresh_token":
                raise ConnectorError("the TikTok sign-in has expired or was withdrawn: connect TikTok again", reconnect=True)
            raise ConnectorError(f"TikTok refused the sign-in ({problem}{': ' + detail if detail else ''})")
        return data

    def _keep(self, data: dict[str, Any]) -> None:
        """Keep what a token answer carries. Refresh tokens are replaced on every use, so the newest is saved at once."""
        self.set_secret("refresh_token", str(data.get("refresh_token", "")))
        self._token = (data["access_token"], time.time() + float(data.get("expires_in") or 3600) - 120)

    def finish(self, redirect_uri: str, query: dict[str, str], kept: dict[str, Any]) -> dict[str, Any]:
        if query.get("error"):
            declined = query["error"] == "access_denied"
            raise ConnectorError("The sign-in was cancelled." if declined else f"TikTok reported: {query.get('error_description') or query['error']}")
        if not query.get("code"):
            raise ConnectorError("TikTok sent no authorization code back.")
        data = self._token_request({"code": query["code"], "grant_type": "authorization_code", "redirect_uri": redirect_uri,
                                    "code_verifier": kept.get("verifier", "")})
        granted = str(data.get("scope", "")).split(",")
        needed = self.scopes()[1]
        if needed not in granted:
            raise ConnectorError(f"The permission to send videos ({needed}) was not given. Connect again and leave it switched on.")
        if not data.get("refresh_token"):
            raise ConnectorError("TikTok gave no long-lived sign-in.")
        self._keep(data)
        name = ""
        info = self.call("GET", f"{self.API}/v2/user/info/", params={"fields": "open_id,display_name"}, headers=self._headers())
        if info.status_code == 200:
            name = str((self.body(info).get("data") or {}).get("user", {}).get("display_name", ""))
        self.remember(connected=now_iso(), name=name or "TikTok account", id=str(data.get("open_id", "")), mode=self.mode, problem="")
        return self.account()

    def access(self, fresh: bool = False) -> str:
        if self._token and not fresh and self._token[1] > time.time():
            return self._token[0]
        refresh = self.secret("refresh_token")
        if not refresh:
            raise ConnectorError("TikTok is not connected", reconnect=True)
        try:
            self._keep(self._token_request({"grant_type": "refresh_token", "refresh_token": refresh}))
        except ConnectorError as exc:
            if exc.reconnect:
                self.remember(problem=str(exc))
            raise
        return self._token[0]

    def _headers(self, fresh: bool = False) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access(fresh)}"}

    def disconnect(self) -> None:
        try:
            token = self.access()
            self.call("POST", f"{self.API}/v2/oauth/revoke/", data={"client_key": self.app()["client_id"], "client_secret": self.secret("client_secret"), "token": token})
        except ConnectorError:
            pass  # it can still be removed in the TikTok app under Security and permissions
        self.set_secret("refresh_token", "")
        self._token = None
        self.forget()

    # --- posting -------------------------------------------------------------------

    def _post(self, path: str, payload: dict | None, doing: str) -> dict[str, Any]:
        """A JSON call to the posting API; returns its `data`. A stale access token is renewed once."""
        for attempt in (0, 1):
            headers = {**self._headers(fresh=attempt == 1), "Content-Type": "application/json; charset=UTF-8"}
            response = self.call("POST", f"{self.API}{path}", json=payload if payload is not None else {}, headers=headers)
            answer = self.body(response)
            error = answer.get("error") if isinstance(answer.get("error"), dict) else {}
            code = str(error.get("code", "") or ("ok" if response.status_code == 200 else ""))
            if code == "access_token_invalid" and attempt == 0:
                continue
            break
        if code == "ok":
            return answer.get("data") if isinstance(answer.get("data"), dict) else {}
        if code in REFUSALS:
            text, retry, reconnect, wait = REFUSALS[code]
            raise ConnectorError(text, retry=retry, reconnect=reconnect, wait=wait)
        if response.status_code >= 500:
            raise ConnectorError(f"TikTok had a problem while {doing} ({response.status_code})", retry=True)
        raise ConnectorError(f"TikTok refused while {doing} ({code or response.status_code}: {str(error.get('message', ''))[:160]})")

    def send(self, clip: dict, video: Path, when: datetime | None) -> Sent:
        size = video.stat().st_size
        chunk, count, spans = plan_chunks(size, self.CHUNK)
        source = {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": count}
        connected_for = self.account().get("mode") or self.mode
        if (connected_for == "draft") != (self.mode == "draft"):
            raise ConnectorError("TikTok was connected for " + ("drafts" if connected_for == "draft" else "posting")
                                 + ", and the setting has changed since: connect TikTok again", reconnect=True)
        if self.mode == "draft":
            made = self._post("/v2/post/publish/inbox/video/init/", {"source_info": source}, "starting the upload")
        else:
            creator, want = self.creator(), self.options(clip)
            offered = creator.get("privacy_level_options") or []
            level = want["audience"]
            if level not in offered:
                names = ", ".join(AUDIENCES.get(o, o) for o in offered) or "nothing"
                raise ConnectorError(f"TikTok does not offer \"{AUDIENCES.get(level, level)}\" for this account (it offers: {names})")
            longest = int(creator.get("max_video_post_duration_sec") or 0)
            seconds = float(clip.get("end", 0)) - float(clip.get("start", 0))
            if longest and seconds > longest:
                raise ConnectorError(f"the clip is {seconds:.0f} seconds; this TikTok account may post up to {longest}")
            if want["paid"] and level == "SELF_ONLY":
                raise ConnectorError("a paid partnership cannot be posted as \"Only me\" on TikTok")
            cover = clip.get("cover")
            # What the account has switched off stays off, whatever the clip asks for.
            info = {"title": str(clip["meta"]["tiktok"]["title"]), "privacy_level": level,
                    "disable_comment": not want["comments"] or bool(creator.get("comment_disabled")),
                    "disable_duet": not want["duet"] or bool(creator.get("duet_disabled")),
                    "disable_stitch": not want["stitch"] or bool(creator.get("stitch_disabled")),
                    "brand_content_toggle": want["paid"], "brand_organic_toggle": want["own_brand"], "is_aigc": False}
            if len(info["title"].encode("utf-16-le")) // 2 > 2200:
                raise ConnectorError("the TikTok caption is over 2,200 characters")
            if cover is not None:
                info["video_cover_timestamp_ms"] = int(float(cover) * 1000)
            made = self._post("/v2/post/publish/video/init/", {"post_info": info, "source_info": source}, "starting the post")
        publish_id, upload_url = str(made.get("publish_id", "")), str(made.get("upload_url", ""))
        if not publish_id or not upload_url:
            raise ConnectorError("TikTok started the upload but gave no address to send the file to", retry=True)
        with video.open("rb") as fh:
            for first, last in spans:  # in order, as TikTok requires
                fh.seek(first)
                block = fh.read(last - first + 1)
                for attempt in range(self.TRIES):
                    try:
                        response = self.call("PUT", upload_url, data=block, timeout=UPLOAD_TIMEOUT, headers={
                            "Content-Type": "video/mp4", "Content-Length": str(len(block)), "Content-Range": f"bytes {first}-{last}/{size}"})
                    except ConnectorError:
                        response = None
                    if response is not None and response.status_code in (201, 206):
                        break
                    if response is not None and response.status_code < 500:
                        raise ConnectorError(f"TikTok refused the video file ({response.status_code})")
                    time.sleep(self.PAUSE * (attempt + 1))
                else:
                    raise ConnectorError("the upload to TikTok kept failing", retry=True)
        if self.mode == "draft":
            return Sent(publish_id, "draft", note="in the TikTok inbox: open the notification in the TikTok app, paste the caption and post it")
        if level == "SELF_ONLY":
            return Sent(publish_id, "processing", note="posted as private: only the account owner can see it until it is changed in TikTok")
        return Sent(publish_id, "processing", note=f"posted for: {AUDIENCES.get(level, level).lower()}")

    def status(self, post: dict) -> dict[str, Any]:
        data = self._post("/v2/post/publish/status/fetch/", {"publish_id": post["id"]}, "asking about the post")
        state = str(data.get("status", ""))
        if state == "FAILED":
            reason = str(data.get("fail_reason", ""))
            return {"state": "failed", "error": f"TikTok could not take the video: {FAILED.get(reason, reason or 'no reason given')}"}
        if state == "PUBLISH_COMPLETE":
            ids = data.get("publicaly_available_post_id") or []  # TikTok's own spelling; filled only for public posts
            return {"state": "posted", **({"post_id": str(ids[0])} if ids else {})}
        if state == "SEND_TO_USER_INBOX":
            return {"state": "draft"}
        return {"state": "processing"}
