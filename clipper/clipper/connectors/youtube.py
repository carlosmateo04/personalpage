"""YouTube, through the YouTube Data API v3.

Sign-in is Google's OAuth 2.0 flow for desktop apps: PKCE, with Google sending
the browser back to this computer (http://127.0.0.1:<port>). The user registers
their own Google Cloud project and a "Desktop app" OAuth client; nothing has to
be registered for the redirect.

Upload is `videos.insert` with the resumable protocol: one request opens an
upload session, the file is sent to the session address, and an interrupted
upload is picked up where it stopped. A future publishing time is handed to
YouTube itself (the video is uploaded private with `status.publishAt`), so the
clip goes out even if this computer is off at that moment.

Written from the documentation as it read on 20 November 2026:
developers.google.com/youtube/v3/guides/using_resumable_upload_protocol,
/youtube/v3/docs/videos/insert, /identity/protocols/oauth2/native-app.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from ..state import now_iso
from .base import UPLOAD_TIMEOUT, Connector, ConnectorError, Sent, pkce

UPLOAD = "https://www.googleapis.com/auth/youtube.upload"
READ = "https://www.googleapis.com/auth/youtube.readonly"
MANAGE = "https://www.googleapis.com/auth/youtube"  # only asked for when a playlist is set: adding to one needs it
COMMENTS = "https://www.googleapis.com/auth/youtube.force-ssl"  # only asked for when comments are switched on
EARNINGS = "https://www.googleapis.com/auth/yt-analytics-monetary.readonly"  # only asked for when the earnings figure is wanted

# What YouTube's refusals mean, in the app's words: reason -> (message, retry, reconnect, seconds to wait)
REFUSALS: dict[str, tuple[str, bool, bool, int]] = {
    "quotaExceeded": ("the Google project has used up its YouTube quota for today; it resets at midnight Pacific Time", True, False, 4 * 3600),
    "uploadLimitExceeded": ("the channel has reached YouTube's daily upload limit", True, False, 6 * 3600),
    "rateLimitExceeded": ("YouTube asked to slow down", True, False, 900),
    "insufficientPermissions": ("the sign-in does not allow uploading: connect YouTube again and allow everything it asks for", False, True, 0),
    "youtubeSignupRequired": ("this Google account has no YouTube channel yet", False, False, 0),
    "invalidTitle": ("YouTube did not accept the title", False, False, 0),
    "invalidDescription": ("YouTube did not accept the description", False, False, 0),
    "invalidTags": ("YouTube did not accept the tags", False, False, 0),
    "invalidCategoryId": ("YouTube did not accept the category (seo.youtube_category)", False, False, 0),
    "invalidPublishAt": ("YouTube did not accept the publishing time", False, False, 0),
}
REJECTED = {"claim": "blocked by a content owner's claim", "copyright": "removed for copyright", "duplicate": "a duplicate of a video already on the channel",
            "inappropriate": "judged inappropriate", "legal": "blocked for a legal reason"}


class YouTube(Connector):
    name, label = "youtube", "YouTube"
    native_schedule = True
    needs_secret = False  # Google marks a desktop client's secret as optional

    AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN = "https://oauth2.googleapis.com/token"
    REVOKE = "https://oauth2.googleapis.com/revoke"
    API = "https://www.googleapis.com/youtube/v3"
    UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos"
    ANALYTICS = "https://youtubeanalytics.googleapis.com/v2/reports"
    BACKOFF = 2.0  # seconds before the first resume of an interrupted upload; doubles each time
    RESUMES = 6

    def __init__(self, cfg):
        super().__init__(cfg)
        self._token: tuple[str, float] | None = None

    # --- sign-in -------------------------------------------------------------------

    def scopes(self) -> list[str]:
        wants_playlists = any(sec.youtube_playlist_id for sec in self.cfg.sections)
        more = [MANAGE] if wants_playlists else []
        if self.cfg.audience.enabled or self.cfg.audience.credit_comment:
            more.append(COMMENTS)  # reading and answering comments
        if self.cfg.progress.earnings:
            more.append(EARNINGS)  # the channel's earnings figure
        return [UPLOAD, READ, *more]

    def allowed(self, scope: str) -> bool:
        """Whether the sign-in on file was given this permission."""
        return scope in (self.remembered().get("scopes") or [])

    def auth_url(self, redirect_uri: str, state: str) -> tuple[str, dict[str, Any]]:
        app = self.app()
        if not app["ready"]:
            raise ConnectorError("Save the Google OAuth client ID first.")
        verifier, challenge = pkce()
        query = {"client_id": app["client_id"], "redirect_uri": redirect_uri, "response_type": "code", "scope": " ".join(self.scopes()),
                 "code_challenge": challenge, "code_challenge_method": "S256", "state": state}
        return f"{self.AUTH}?{urlencode(query)}", {"verifier": verifier}

    def _token_request(self, fields: dict[str, str]) -> dict[str, Any]:
        fields = {"client_id": self.app()["client_id"], **fields}
        if self.secret("client_secret"):
            fields["client_secret"] = self.secret("client_secret")
        response = self.call("POST", self.TOKEN, data=fields)
        data = self.body(response)
        if response.status_code != 200 or "access_token" not in data:
            problem = str(data.get("error", "")) or f"status {response.status_code}"
            detail = str(data.get("error_description", ""))[:160]
            if problem == "invalid_grant" and fields.get("grant_type") == "refresh_token":
                raise ConnectorError("the YouTube sign-in has expired or was withdrawn: connect YouTube again", reconnect=True)
            if response.status_code >= 500:
                raise ConnectorError("Google's sign-in service is not answering", retry=True)
            raise ConnectorError(f"Google refused the sign-in ({problem}{': ' + detail if detail else ''})", reconnect=fields.get("grant_type") == "refresh_token")
        return data

    def finish(self, redirect_uri: str, query: dict[str, str], kept: dict[str, Any]) -> dict[str, Any]:
        if query.get("error"):
            declined = query["error"] == "access_denied"
            raise ConnectorError("The sign-in was cancelled." if declined else f"Google reported: {query['error']}")
        if not query.get("code"):
            raise ConnectorError("Google sent no authorization code back.")
        data = self._token_request({"code": query["code"], "code_verifier": kept.get("verifier", ""), "grant_type": "authorization_code",
                                    "redirect_uri": redirect_uri})
        granted = str(data.get("scope", "")).split()
        if UPLOAD not in granted:
            raise ConnectorError("The permission to upload videos was not given. Connect again and leave every box ticked.")
        if not data.get("refresh_token"):
            raise ConnectorError('Google gave no long-lived sign-in. The OAuth client must be of the type "Desktop app".')
        self.set_secret("refresh_token", data["refresh_token"])
        self._token = (data["access_token"], time.time() + float(data.get("expires_in") or 3000) - 120)
        try:
            channel = self.channel()
        except ConnectorError:
            self.set_secret("refresh_token", "")
            raise
        self.remember(connected=now_iso(), name=channel["name"], id=channel["id"], handle=channel["handle"],
                      can_read=READ in granted, playlists=MANAGE in granted, scopes=granted, problem="")
        return self.account()

    def access(self, fresh: bool = False) -> str:
        """A working access token: the one in hand, or a new one from the long-lived sign-in."""
        if self._token and not fresh and self._token[1] > time.time():
            return self._token[0]
        refresh = self.secret("refresh_token")
        if not refresh:
            raise ConnectorError("YouTube is not connected", reconnect=True)
        try:
            data = self._token_request({"grant_type": "refresh_token", "refresh_token": refresh})
        except ConnectorError as exc:
            if exc.reconnect:
                self.remember(problem=str(exc))
            raise
        self._token = (data["access_token"], time.time() + float(data.get("expires_in") or 3000) - 120)
        return self._token[0]

    def _headers(self, fresh: bool = False) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access(fresh)}"}

    def _reason(self, response) -> str:
        error = self.body(response).get("error")
        errors = error.get("errors") if isinstance(error, dict) else None
        return str(errors[0].get("reason", "")) if isinstance(errors, list) and errors and isinstance(errors[0], dict) else ""

    def _refused(self, response, doing: str) -> ConnectorError:
        """YouTube's error answer as a ConnectorError that says what to do about it."""
        error = self.body(response).get("error")
        error = error if isinstance(error, dict) else {}
        reason, message = self._reason(response), str(error.get("message", ""))[:200]
        if reason in REFUSALS:
            text, retry, reconnect, wait = REFUSALS[reason]
            return ConnectorError(f"{text}", retry=retry, reconnect=reconnect, wait=wait)
        if response.status_code == 401:
            return ConnectorError("the YouTube sign-in was not accepted: connect YouTube again", reconnect=True)
        if response.status_code >= 500:
            return ConnectorError(f"YouTube had a problem while {doing} ({response.status_code})", retry=True)
        return ConnectorError(f"YouTube refused while {doing} ({response.status_code} {reason}: {message})".replace(" : ", ": "))

    def channel(self) -> dict[str, str]:
        response = self.call("GET", f"{self.API}/channels", params={"part": "snippet,statistics", "mine": "true"}, headers=self._headers())
        if response.status_code != 200:
            raise self._refused(response, "reading the channel")
        items = self.body(response).get("items") or []
        if not items:
            raise ConnectorError("This Google account has no YouTube channel yet. Create the channel on youtube.com, then connect again.")
        snippet, stats = items[0].get("snippet", {}), items[0].get("statistics", {})
        found = {"id": str(items[0].get("id", "")), "name": str(snippet.get("title", "")), "handle": str(snippet.get("customUrl", ""))}
        if not stats.get("hiddenSubscriberCount") and str(stats.get("subscriberCount", "")).isdigit():
            found["subscribers"] = int(stats["subscriberCount"])
        return found

    def followers(self) -> int | None:
        return self.channel().get("subscribers")

    def earnings(self, start: str, end: str) -> dict[str, float]:
        """What YouTube estimates the channel earned, per month ("2026-11": 12.34), between two dates. Needs the earnings permission."""
        if not self.allowed(EARNINGS):
            raise ConnectorError("the YouTube sign-in was not given the permission to read earnings: switch it on and connect YouTube again", reconnect=True)
        response = self.call("GET", self.ANALYTICS, headers=self._headers(), params={
            "ids": "channel==MINE", "startDate": start, "endDate": end, "metrics": "estimatedRevenue", "dimensions": "month"})
        if response.status_code != 200:
            raise self._refused(response, "reading the earnings")
        out: dict[str, float] = {}
        for row in self.body(response).get("rows") or []:
            try:
                out[str(row[0])[:7]] = round(float(row[1]), 2)
            except (IndexError, TypeError, ValueError):
                continue
        return out

    # --- comments ------------------------------------------------------------------

    def comments(self, post: dict) -> list[dict[str, Any]]:
        if not self.allowed(COMMENTS):
            raise ConnectorError("the YouTube sign-in was not given the permission for comments: connect YouTube again", reconnect=True)
        response = self.call("GET", f"{self.API}/commentThreads", headers=self._headers(), params={
            "part": "snippet,replies", "videoId": post["id"], "maxResults": "20", "order": "time", "textFormat": "plainText"})
        if response.status_code == 403 and "commentsDisabled" in self._reason(response):
            return []
        if response.status_code != 200:
            raise self._refused(response, "reading the comments")
        mine = self.remembered().get("id", "")
        out = []
        for item in self.body(response).get("items") or []:
            top = (item.get("snippet") or {}).get("topLevelComment") or {}
            said = top.get("snippet") or {}
            if (said.get("authorChannelId") or {}).get("value") == mine:
                continue  # the channel's own comment (the credit, say)
            replies = (item.get("replies") or {}).get("comments") or []
            answered = any(((r.get("snippet") or {}).get("authorChannelId") or {}).get("value") == mine for r in replies)
            out.append({"id": str(top.get("id", "")), "text": str(said.get("textDisplay") or said.get("textOriginal") or "")[:600],
                        "at": str(said.get("publishedAt", "")), "answered": answered})
        return [c for c in out if c["id"] and c["text"]]

    def reply(self, post: dict, comment_id: str, text: str) -> None:
        self.say({"snippet": {"parentId": comment_id, "textOriginal": text[:2000]}}, f"{self.API}/comments", "answering the comment")

    def comment(self, post: dict, text: str) -> None:
        """A comment of the channel's own on its video."""
        self.say({"snippet": {"videoId": post["id"], "topLevelComment": {"snippet": {"textOriginal": text[:2000]}}}},
                 f"{self.API}/commentThreads", "commenting")

    def say(self, body: dict, url: str, doing: str) -> None:
        if not self.allowed(COMMENTS):
            raise ConnectorError("the YouTube sign-in was not given the permission for comments: connect YouTube again", reconnect=True)
        response = self.call("POST", url, params={"part": "snippet"}, json=body, headers=self._headers())
        if response.status_code not in (200, 201):
            raise self._refused(response, doing)

    def disconnect(self) -> None:
        token = self.secret("refresh_token")
        if token:
            try:  # YouTube's policy: an app must withdraw the access itself when the user disconnects
                self.call("POST", self.REVOKE, params={"token": token}, headers={"Content-Type": "application/x-www-form-urlencoded"})
            except ConnectorError:
                pass  # it can still be withdrawn at myaccount.google.com/permissions
        self.set_secret("refresh_token", "")
        self._token = None
        self.forget()

    # --- upload --------------------------------------------------------------------

    def resource(self, clip: dict, when: datetime | None) -> dict[str, Any]:
        """The video resource sent with the upload. The text goes as it is: nothing is cut to fit."""
        meta, pub = clip["meta"]["youtube"], self.cfg.publish
        title, description = str(meta["title"]), str(meta["description"])
        if not title.strip() or len(title) > 100:
            raise ConnectorError("the YouTube title must be between 1 and 100 characters")
        if len(description.encode("utf-8")) > 5000:
            raise ConnectorError("the YouTube description is over 5,000 bytes")
        if any(mark in title + description for mark in "<>"):
            raise ConnectorError("YouTube does not allow < or > in a title or description")
        status: dict[str, Any] = {"privacyStatus": pub.youtube_privacy, "selfDeclaredMadeForKids": False, "containsSyntheticMedia": False}
        if when is not None and pub.youtube_privacy == "public":
            # Scheduled: uploaded private, and YouTube makes it public at the time given.
            status.update(privacyStatus="private", publishAt=when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        return {
            "snippet": {"title": title, "description": description, "tags": [str(t) for t in meta.get("tags") or []],
                        "categoryId": str(self.cfg.seo.youtube_category)},
            "status": status,
            "paidProductPlacementDetails": {"hasPaidProductPlacement": bool(self.cfg.campaign.paid)},
        }

    def _open(self, resource: dict, size: int) -> str:
        """Start an upload session; returns its address."""
        params = {"uploadType": "resumable", "part": ",".join(resource), "notifySubscribers": "true" if self.cfg.publish.youtube_notify else "false"}
        for attempt in (0, 1):
            headers = {**self._headers(fresh=attempt == 1), "Content-Type": "application/json; charset=UTF-8",
                       "X-Upload-Content-Length": str(size), "X-Upload-Content-Type": "video/mp4"}
            response = self.call("POST", self.UPLOAD, params=params, json=resource, headers=headers)
            if response.status_code == 401 and attempt == 0 and self._reason(response) not in REFUSALS:
                continue  # the access token went stale: one more go with a new one
            break
        session = response.headers.get("Location", "")
        if response.status_code != 200 or not session:
            raise self._refused(response, "starting the upload")
        return session

    def _stored(self, session: str, size: int) -> tuple[int, Any]:
        """How much of the file YouTube has: (bytes stored, None), or (size, the finished answer)."""
        response = self.call("PUT", session, headers={**self._headers(), "Content-Length": "0", "Content-Range": f"bytes */{size}"})
        if response.status_code in (200, 201):
            return size, response
        if response.status_code == 308:
            span = response.headers.get("Range", "")  # "bytes=0-999999", or absent when nothing has arrived
            return (int(span.rsplit("-", 1)[1]) + 1 if "-" in span else 0), None
        raise self._refused(response, "resuming the upload")

    def _transfer(self, session: str, video: Path, size: int) -> dict[str, Any]:
        """Send the file to the session, resuming after an interruption until it is all there."""
        start, last = 0, None
        for attempt in range(self.RESUMES):
            if attempt:
                time.sleep(self.BACKOFF * 2 ** (attempt - 1))
                try:
                    start, done = self._stored(session, size)  # never assume what arrived: ask
                except ConnectorError as exc:
                    if not exc.retry:
                        raise
                    last = exc
                    continue
                if done is not None:
                    return self.body(done)
            headers = {**self._headers(), "Content-Type": "video/mp4", "Content-Length": str(size - start)}
            if start:
                headers["Content-Range"] = f"bytes {start}-{size - 1}/{size}"
            try:
                with video.open("rb") as fh:
                    fh.seek(start)
                    response = self.call("PUT", session, data=fh, headers=headers, timeout=UPLOAD_TIMEOUT)
            except ConnectorError as exc:  # the connection dropped part-way
                last = exc
                continue
            if response.status_code in (200, 201):
                return self.body(response)
            if response.status_code in (500, 502, 503, 504):
                last = self._refused(response, "uploading")
                continue
            if response.status_code == 404:
                raise ConnectorError("the upload session expired before the file was through", retry=True)
            raise self._refused(response, "uploading")
        raise ConnectorError(f"the upload kept being interrupted ({last})", retry=True)

    def send(self, clip: dict, video: Path, when: datetime | None) -> Sent:
        resource = self.resource(clip, when)
        size = video.stat().st_size
        made = self._transfer(self._open(resource, size), video, size)
        video_id = str(made.get("id", ""))
        if not video_id:
            raise ConnectorError("YouTube took the file but returned no video", retry=True)
        notes = []
        wanted = resource["status"]["privacyStatus"]
        got = str((made.get("status") or {}).get("privacyStatus", wanted))
        if got != wanted:  # an account setting, or a project YouTube restricts, can force a video to private
            notes.append(f"YouTube made this video {got} although {wanted} was asked for; check it in YouTube Studio")
        playlist = clip["meta"].get("playlist")
        if playlist:
            added = self.call("POST", f"{self.API}/playlistItems", params={"part": "snippet"}, headers=self._headers(),
                              json={"snippet": {"playlistId": playlist, "resourceId": {"kind": "youtube#video", "videoId": video_id}}})
            if added.status_code != 200:
                notes.append(f"not added to the playlist ({self._refused(added, 'adding to the playlist')})")
        scheduled = "publishAt" in resource["status"]
        return Sent(video_id, "scheduled" if scheduled else "processing", f"https://www.youtube.com/watch?v={video_id}", "; ".join(notes))

    # --- afterwards ------------------------------------------------------------------

    def status(self, post: dict) -> dict[str, Any]:
        response = self.call("GET", f"{self.API}/videos", params={"part": "status,statistics", "id": post["id"]}, headers=self._headers())
        if response.status_code != 200:
            raise self._refused(response, "reading the video")
        items = self.body(response).get("items") or []
        if not items:
            return {"state": "failed", "error": "the video is no longer on YouTube"}
        status, stats = items[0].get("status", {}), items[0].get("statistics", {})
        upload = status.get("uploadStatus")
        if upload == "rejected":
            return {"state": "failed", "error": f"YouTube rejected the video: {REJECTED.get(status.get('rejectionReason'), 'no reason given')}"}
        if upload in ("failed", "deleted"):
            return {"state": "failed", "error": f"YouTube could not process the video ({status.get('failureReason') or upload})"}
        out: dict[str, Any] = {}
        try:
            out["views"] = int(stats.get("viewCount", 0))
        except (TypeError, ValueError):
            pass
        if status.get("privacyStatus") == "private" and status.get("publishAt"):
            out["state"] = "scheduled"
        elif upload == "processed":
            out["state"] = "posted"
            if status.get("privacyStatus") == "private" and self.cfg.publish.youtube_privacy != "private":
                out["note"] = "the video is private on YouTube; check it in YouTube Studio"
        else:
            out["state"] = "processing"
        return out
