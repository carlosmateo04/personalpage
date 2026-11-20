"""Instagram and Facebook, through Meta's Graph API.

One sign-in covers both: the user logs in with Facebook, picks the Page, and the
Instagram professional account linked to that Page comes with it. The user
registers their own Meta app. There are two ways in:

  sign in     Facebook's login dialog, which sends the browser back to this
              computer (http://localhost:<port>/oauth/meta). Meta documents that
              for apps in development mode.
  paste       a user access token made in Meta's Graph API Explorer for the same
              app, pasted into the app here. It is exchanged for a long-lived one
              the same way.

Facebook: a clip of up to 90 seconds is published as a Page reel (start, upload
to rupload.facebook.com, finish); a longer one as a Page video (upload session,
then /videos), because Page reels stop at 90 seconds. A future time is handed to
Facebook itself. Instagram: a reel from a local file (a resumable container, the
upload, then publish once Instagram has processed it). Instagram has no
scheduling, so a clip is sent when its time comes.

What Meta's documentation does not settle, and a first real post has to show:
whether posts from an app that has not been through App Review are visible to
everyone. Its one statement on it is that content made by an app in development
mode is seen only by people with a role on the app.

Written from developers.facebook.com as it read on 20 November 2026 (Graph API v27.0).
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from ..state import now_iso
from .base import UPLOAD_TIMEOUT, Connector, ConnectorError, Sent

SCOPES = ["pages_show_list", "pages_read_engagement", "pages_manage_posts", "instagram_basic", "instagram_content_publish"]
COMMENT_SCOPES = ["pages_read_user_content", "pages_manage_engagement", "instagram_manage_comments"]  # only asked for when comments are switched on
RATE_LIMITS = {4, 17, 32, 341, 613, 80001, 80002}
REEL_SECONDS = 90  # Facebook Page reels stop here; longer clips go up as Page videos


class Meta(Connector):
    """The shared part: Facebook Login, the Page and its tokens, and how Graph errors read."""

    account_key = "meta"
    redirect_host = "localhost"  # the only loopback name Meta speaks of
    redirect_path = "/oauth/meta"

    DIALOG = "https://www.facebook.com/v27.0/dialog/oauth"
    GRAPH = "https://graph.facebook.com/v27.0"
    POLL = 60.0  # seconds between looks at a video being processed (Meta: once a minute, five minutes at most)
    POLLS = 5

    # --- sign-in -------------------------------------------------------------------

    def auth_url(self, redirect_uri: str, state: str) -> tuple[str, dict[str, Any]]:
        app = self.app()
        if not app["ready"]:
            raise ConnectorError("Save the Meta app's ID and secret first.")
        query = {"client_id": app["client_id"], "redirect_uri": redirect_uri, "state": state, "response_type": "code"}
        config = str(self.remembered().get("config_id", ""))
        if config:  # Facebook Login for Business: the permissions live in a configuration made in the app dashboard
            query["config_id"] = config
        else:
            query["scope"] = ",".join(SCOPES + (COMMENT_SCOPES if self.cfg.audience.enabled else []))
        return f"{self.DIALOG}?{urlencode(query)}", {}

    def graph(self, method: str, path: str, doing: str, token: str | None = None, **kwargs: Any) -> dict[str, Any]:
        """One Graph API call; returns the answer, or raises saying what to do about the refusal."""
        if token:
            kwargs.setdefault("params", {})["access_token"] = token
        response = self.call(method, path if path.startswith("http") else f"{self.GRAPH}{path}", **kwargs)
        data = self.body(response)
        error = data.get("error")
        if response.status_code < 400 and not error:
            return data
        raise self.refusal(error if isinstance(error, dict) else {}, response.status_code, doing)

    def refusal(self, error: dict, status: int, doing: str) -> ConnectorError:
        code, message = error.get("code"), str(error.get("error_user_msg") or error.get("message") or "")[:200]
        if code in (190, 102):
            self.remember(problem="the sign-in has expired or was withdrawn")
            return ConnectorError("the Facebook sign-in has expired or was withdrawn: connect Facebook and Instagram again", reconnect=True)
        if code in RATE_LIMITS:
            return ConnectorError(f"Meta asked to slow down ({message or code})", retry=True, wait=3600)
        if code == 10 or (isinstance(code, int) and 200 <= code <= 299):
            return ConnectorError(f"a permission is missing ({message}): connect again and allow everything it asks for", reconnect=True)
        if code == 368:
            return ConnectorError(f"Meta has blocked this action for now ({message})", retry=True, wait=6 * 3600)
        if code in (1, 2) or status >= 500:
            return ConnectorError(f"Meta had a problem while {doing} ({message or status})", retry=True)
        return ConnectorError(f"Meta refused while {doing} ({code or status}: {message})")

    def finish(self, redirect_uri: str, query: dict[str, str], kept: dict[str, Any]) -> dict[str, Any]:
        app_id, secret = self.app()["client_id"], self.secret("client_secret")
        if query.get("token"):  # pasted from the Graph API Explorer
            short = query["token"].strip()
        else:
            if query.get("error"):
                declined = query.get("error_reason") == "user_denied" or query["error"] == "access_denied"
                raise ConnectorError("The sign-in was cancelled." if declined else f"Facebook reported: {query.get('error_description') or query['error']}")
            if not query.get("code"):
                raise ConnectorError("Facebook sent no authorization code back.")
            short = str(self.graph("GET", "/oauth/access_token", "signing in", params={
                "client_id": app_id, "redirect_uri": redirect_uri, "client_secret": secret, "code": query["code"]}).get("access_token", ""))
        long = self.graph("GET", "/oauth/access_token", "signing in", params={
            "grant_type": "fb_exchange_token", "client_id": app_id, "client_secret": secret, "fb_exchange_token": short})
        token = str(long.get("access_token", ""))
        if not token:
            raise ConnectorError("Facebook gave no sign-in token.")
        lasts = int(long.get("expires_in") or 0)
        expires = (datetime.now(timezone.utc) + timedelta(seconds=lasts)).strftime("%Y-%m-%dT%H:%M:%SZ") if lasts else ""
        self.set_secret("user_token", token)
        try:
            return self.choose(self.cfg.publish.facebook_page_id, expires=expires)
        except ConnectorError:
            self.set_secret("user_token", "")
            raise

    def pages(self) -> list[dict[str, Any]]:
        """The Pages this person manages, each with its token and the Instagram account linked to it."""
        token = self.secret("user_token")
        if not token:
            raise ConnectorError("Facebook is not connected", reconnect=True)
        found: list[dict[str, Any]] = []
        url, params = "/me/accounts", {"fields": "id,name,access_token,instagram_business_account"}
        for _ in range(10):
            data = self.graph("GET", url, "reading the Pages", token=token, params=dict(params))
            found += [page for page in data.get("data") or [] if isinstance(page, dict)]
            url = str((data.get("paging") or {}).get("next") or "")
            params = {}
            if not url:
                break
        return found

    def choose(self, page_id: str = "", expires: str | None = None) -> dict[str, Any]:
        """Settle on a Page (the one asked for, else the first with an Instagram account, else the first) and keep its token."""
        found = self.pages()
        if not found:
            raise ConnectorError("This Facebook account manages no Page. Create a Page (and link the Instagram account to it), then connect again.")
        page = (next((p for p in found if str(p.get("id")) == str(page_id)), None)
                or next((p for p in found if p.get("instagram_business_account")), None) or found[0])
        instagram = str((page.get("instagram_business_account") or {}).get("id", ""))
        handle = ""
        if instagram:
            handle = str(self.graph("GET", f"/{instagram}", "reading the Instagram account", token=self.secret("user_token"),
                                    params={"fields": "username"}).get("username", ""))
        self.set_secret("page_token", str(page.get("access_token", "")))
        values = {"connected": now_iso(), "name": str(page.get("name", "")), "page_id": str(page.get("id", "")), "instagram_id": instagram,
                  "instagram_name": handle, "problem": "",
                  "pages": [{"id": str(p.get("id", "")), "name": str(p.get("name", "")), "instagram": bool(p.get("instagram_business_account"))} for p in found]}
        if expires is not None:
            values["expires"] = expires
        self.remember(**values)
        return self.account()

    def disconnect(self) -> None:
        token = self.secret("user_token")
        if token:
            try:  # withdraws every permission this person gave the app
                self.graph("DELETE", "/me/permissions", "withdrawing the access", token=token)
            except ConnectorError:
                pass
        self.set_secret("user_token", "")
        self.set_secret("page_token", "")
        self.forget()

    def tokens(self) -> tuple[str, str]:
        """(user token, Page token), or an error saying to connect."""
        kept = self.remembered()
        user, page = self.secret("user_token"), self.secret("page_token")
        if not kept.get("connected") or not user or not page:
            raise ConnectorError(f"{self.label} is not connected", reconnect=True)
        if kept.get("expires") and kept["expires"] < now_iso():
            self.remember(problem="the sign-in has run out")
            raise ConnectorError("the Facebook sign-in has run out (it lasts about 60 days): connect Facebook and Instagram again", reconnect=True)
        return user, page

    def push(self, url: str, token: str, video: Path, doing: str, offset_header: str = "offset", stored=None) -> dict[str, Any]:
        """Send the file to one of Meta's upload addresses, picking up after an interruption where `stored` says it stopped."""
        size, start, last = video.stat().st_size, 0, None
        for attempt in range(4):
            if attempt:
                time.sleep(min(self.POLL, 5.0) * attempt)
                try:
                    start = int(stored()) if stored else 0
                except ConnectorError as exc:
                    last = exc
                    continue
            headers = {"Authorization": f"OAuth {token}", offset_header: str(start), "Content-Type": "application/octet-stream"}
            if offset_header == "offset":
                headers["file_size"] = str(size)
            try:
                with video.open("rb") as fh:
                    fh.seek(start)
                    response = self.call("POST", url, data=fh, headers=headers, timeout=UPLOAD_TIMEOUT)
            except ConnectorError as exc:
                last = exc
                continue
            data = self.body(response)
            if response.status_code < 400 and not data.get("error"):
                return data
            refusal = self.refusal(data.get("error") if isinstance(data.get("error"), dict) else {}, response.status_code, doing)
            if not refusal.retry or refusal.wait:
                raise refusal
            last = refusal
        raise ConnectorError(f"the upload kept being interrupted ({last})", retry=True)


class Facebook(Meta):
    name, label = "facebook", "Facebook"
    native_schedule = True

    def account(self) -> dict[str, Any]:
        kept = self.remembered()
        if not kept.get("connected") or not kept.get("page_id"):
            return {}
        return {"name": kept.get("name", ""), "id": kept["page_id"], "connected": kept["connected"], "expires": kept.get("expires", ""),
                "problem": kept.get("problem", ""), "pages": kept.get("pages", [])}

    def followers(self) -> int | None:
        _, page = self.tokens()
        found = self.graph("GET", f"/{self.remembered()['page_id']}", "reading the Page", token=page, params={"fields": "followers_count"})
        return int(found["followers_count"]) if str(found.get("followers_count", "")).isdigit() else None

    def comments(self, post: dict) -> list[dict[str, Any]]:
        _, page = self.tokens()
        found = self.graph("GET", f"/{post['id']}/comments", "reading the comments", token=page,
                           params={"fields": "id,message,created_time,from{id},comments.limit(5){from{id}}", "order": "reverse_chronological", "limit": "20"})
        mine = str(self.remembered().get("page_id", ""))
        out = []
        for item in found.get("data") or []:
            if str((item.get("from") or {}).get("id", "")) == mine:
                continue
            answered = any(str((r.get("from") or {}).get("id", "")) == mine for r in (item.get("comments") or {}).get("data") or [])
            out.append({"id": str(item.get("id", "")), "text": str(item.get("message", ""))[:600], "at": str(item.get("created_time", "")), "answered": answered})
        return [c for c in out if c["id"] and c["text"]]

    def reply(self, post: dict, comment_id: str, text: str) -> None:
        _, page = self.tokens()
        self.graph("POST", f"/{comment_id}/comments", "answering the comment", token=page, params={"message": text[:2000]})

    def send(self, clip: dict, video: Path, when: datetime | None) -> Sent:
        user, page_token = self.tokens()
        page = self.remembered()["page_id"]
        caption, title = str(clip["meta"]["facebook"]["title"]), str(clip["meta"]["title"])
        now = datetime.now(timezone.utc)
        # Facebook schedules from ten minutes ahead up to 29 days; anything sooner goes out now.
        later = when if when is not None and timedelta(minutes=11) < when - now < timedelta(days=29) else None
        seconds = float(clip.get("end", 0)) - float(clip.get("start", 0))
        if seconds <= REEL_SECONDS:
            started = self.graph("POST", f"/{page}/video_reels", "starting the reel", json={"upload_phase": "start", "access_token": page_token})
            video_id, upload_url = str(started.get("video_id", "")), str(started.get("upload_url", ""))
            if not video_id or not upload_url:
                raise ConnectorError("Facebook started the reel but gave no address to send the file to", retry=True)

            def stored() -> int:
                phase = (self.graph("GET", f"/{video_id}", "resuming the upload", token=page_token, params={"fields": "status"}).get("status") or {}).get("uploading_phase") or {}
                return int(phase.get("bytes_transfered") or phase.get("bytes_transferred") or 0)  # Facebook spells it with one r

            self.push(upload_url, page_token, video, "uploading the reel", stored=stored)
            fields = {"access_token": page_token, "video_id": video_id, "upload_phase": "finish", "description": caption, "title": title,
                      "video_state": "SCHEDULED" if later else "PUBLISHED"}
            if later:
                fields["scheduled_publish_time"] = str(int(later.timestamp()))
            self.graph("POST", f"/{page}/video_reels", "publishing the reel", params=fields)
            return Sent(video_id, "scheduled" if later else "processing", f"https://www.facebook.com/reel/{video_id}")

        # Longer than a reel may be: a Page video, uploaded through an upload session of the app.
        app_id = self.app()["client_id"]
        session = str(self.graph("POST", f"/{app_id}/uploads", "starting the upload", token=user, params={
            "file_name": video.name, "file_length": str(video.stat().st_size), "file_type": "video/mp4"}).get("id", ""))
        if not session:
            raise ConnectorError("Facebook started no upload session", retry=True)
        handle = str(self.push(f"{self.GRAPH}/{session}", user, video, "uploading the video", offset_header="file_offset",
                               stored=lambda: self.graph("GET", f"/{session}", "resuming the upload", headers={"Authorization": f"OAuth {user}"}).get("file_offset") or 0).get("h", ""))
        if not handle:
            raise ConnectorError("Facebook took the file but returned no handle for it", retry=True)
        fields = {"access_token": page_token, "title": title, "description": caption, "fbuploader_video_file_chunk": handle}
        if later:
            fields.update(published="false", scheduled_publish_time=str(int(later.timestamp())))
        video_id = str(self.graph("POST", f"/{page}/videos", "publishing the video", data=fields).get("id", ""))
        if not video_id:
            raise ConnectorError("Facebook published the video but returned no id", retry=True)
        return Sent(video_id, "scheduled" if later else "processing", f"https://www.facebook.com/{page}/videos/{video_id}",
                    f"posted as a Page video: Page reels stop at {REEL_SECONDS} seconds")

    def status(self, post: dict) -> dict[str, Any]:
        _, page_token = self.tokens()
        data = self.graph("GET", f"/{post['id']}", "reading the video", token=page_token, params={"fields": "status,permalink_url"})
        status = data.get("status") or {}
        out: dict[str, Any] = {}
        link = str(data.get("permalink_url") or "")
        if link:
            out["link"] = link if link.startswith("http") else f"https://www.facebook.com{link}"
        publishing = status.get("publishing_phase") or {}
        if status.get("video_status") in ("error", "upload_failed", "expired"):
            problem = ((status.get("processing_phase") or {}).get("error") or publishing.get("error") or {})
            return {**out, "state": "failed", "error": f"Facebook could not publish the video: {problem.get('message') or status.get('video_status')}"}
        if publishing.get("publish_status") == "published" or (status.get("video_status") == "ready" and not publishing):
            out["state"] = "posted"
        elif publishing.get("publish_status") == "scheduled":
            out["state"] = "scheduled"
        else:
            out["state"] = "processing"
        return out


class Instagram(Meta):
    name, label = "instagram", "Instagram"
    native_schedule = False

    def account(self) -> dict[str, Any]:
        kept = self.remembered()
        if not kept.get("connected") or not kept.get("instagram_id"):
            return {}
        return {"name": f"@{kept['instagram_name']}" if kept.get("instagram_name") else "Instagram account", "id": kept["instagram_id"],
                "connected": kept["connected"], "expires": kept.get("expires", ""), "problem": kept.get("problem", "")}

    def followers(self) -> int | None:
        user, _ = self.tokens()
        found = self.graph("GET", f"/{self.remembered()['instagram_id']}", "reading the Instagram account", token=user, params={"fields": "followers_count"})
        return int(found["followers_count"]) if str(found.get("followers_count", "")).isdigit() else None

    def comments(self, post: dict) -> list[dict[str, Any]]:
        if str(post.get("id", "")).startswith("container:"):
            return []  # not published yet
        user, _ = self.tokens()
        found = self.graph("GET", f"/{post['id']}/comments", "reading the comments", token=user,
                           params={"fields": "id,text,timestamp,from{id},replies{from{id}}", "limit": "20"})
        mine = str(self.remembered().get("instagram_id", ""))
        out = []
        for item in found.get("data") or []:
            if str((item.get("from") or {}).get("id", "")) == mine:
                continue
            answered = any(str((r.get("from") or {}).get("id", "")) == mine for r in (item.get("replies") or {}).get("data") or [])
            out.append({"id": str(item.get("id", "")), "text": str(item.get("text", ""))[:600], "at": str(item.get("timestamp", "")), "answered": answered})
        return [c for c in out if c["id"] and c["text"]]

    def reply(self, post: dict, comment_id: str, text: str) -> None:
        user, _ = self.tokens()
        self.graph("POST", f"/{comment_id}/replies", "answering the comment", token=user, params={"message": text[:2000]})

    def unfit(self, clip: dict, seconds: float, size: int) -> str:
        if not 3 <= seconds <= 15 * 60:
            return "Instagram reels run from 3 seconds to 15 minutes"
        return ""

    def _publish(self, container: str, user: str) -> dict[str, Any]:
        account = self.remembered()["instagram_id"]
        made = self.graph("POST", f"/{account}/media_publish", "publishing the reel", token=user, params={"creation_id": container})
        media = str(made.get("id", ""))
        if not media:
            raise ConnectorError("Instagram published the reel but returned no id", retry=True)
        out: dict[str, Any] = {"state": "posted", "id": media}
        try:
            out["link"] = str(self.graph("GET", f"/{media}", "reading the reel", token=user, params={"fields": "permalink"}).get("permalink", ""))
        except ConnectorError:
            pass  # it is out; the link can be read later
        return out

    def _ready(self, container: str, user: str) -> str:
        """Where Instagram is with a container: FINISHED, IN_PROGRESS, PUBLISHED, or an error raised."""
        data = self.graph("GET", f"/{container}", "asking about the reel", token=user, params={"fields": "status_code,status"})
        code = str(data.get("status_code", ""))
        if code == "ERROR":
            raise ConnectorError(f"Instagram could not process the video ({data.get('status') or 'no reason given'})")
        if code == "EXPIRED":
            raise ConnectorError("Instagram let the upload expire before it was published", retry=True)
        return code

    def send(self, clip: dict, video: Path, when: datetime | None) -> Sent:
        user, _ = self.tokens()
        account = self.remembered()["instagram_id"]
        if not account:
            raise ConnectorError("no Instagram professional account is linked to the chosen Facebook Page")
        caption = str(clip["meta"]["instagram"]["title"])
        if len(caption) > 2200:
            raise ConnectorError("the Instagram caption is over 2,200 characters")
        fields = {"media_type": "REELS", "upload_type": "resumable", "caption": caption}
        if clip.get("cover") is not None:
            fields["thumb_offset"] = str(int(float(clip["cover"]) * 1000))
        made = self.graph("POST", f"/{account}/media", "starting the reel", token=user, params=fields)
        container, uri = str(made.get("id", "")), str(made.get("uri", ""))
        if not container or not uri:
            raise ConnectorError("Instagram started the reel but gave no address to send the file to", retry=True)

        def stored() -> int:
            phase = (self.graph("GET", f"/{container}", "resuming the upload", token=user, params={"fields": "video_status"}).get("video_status") or {}).get("uploading_phase") or {}
            return int(phase.get("bytes_transferred") or phase.get("bytes_transfered") or 0)

        self.push(uri, user, video, "uploading the reel", stored=stored)
        for look in range(self.POLLS):  # Instagram processes the video before it can be published
            if self._ready(container, user) == "FINISHED":
                done = self._publish(container, user)
                return Sent(done["id"], "posted", done.get("link", ""))
            if look < self.POLLS - 1:
                time.sleep(self.POLL)
        # Still being processed: it is published when the outbox next asks about it.
        return Sent(f"container:{container}", "processing")

    def status(self, post: dict) -> dict[str, Any]:
        user, _ = self.tokens()
        ident = str(post["id"])
        if ident.startswith("container:"):
            code = self._ready(ident.removeprefix("container:"), user)
            if code == "FINISHED":
                return self._publish(ident.removeprefix("container:"), user)
            return {"state": "processing"}
        out: dict[str, Any] = {"state": "posted"}
        if not post.get("link"):
            out["link"] = str(self.graph("GET", f"/{ident}", "reading the reel", token=user, params={"fields": "permalink"}).get("permalink", ""))
        try:  # needs the insights permission; without it there are simply no numbers
            data = self.graph("GET", f"/{ident}/insights", "reading the views", token=user, params={"metric": "views"}).get("data") or []
            values = data[0].get("values") or [{}] if data else [{}]
            if isinstance(values[0].get("value"), int):
                out["views"] = values[0]["value"]
        except ConnectorError:
            pass
        return out
