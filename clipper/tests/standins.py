"""Local stand-ins for the outside services the clipper talks to.

Each one answers the way the service's documentation says it does and remembers
what it was sent, so a test can read back the exact request.
"""

import json
from http.server import BaseHTTPRequestHandler

GOOD_KEY = "sk-ant-test-good"
AWS_KEY = "AKIAGOODKEY"

ERRORS = {400: "invalid_request_error", 401: "authentication_error", 403: "permission_error", 404: "not_found_error",
          429: "rate_limit_error", 500: "api_error", 529: "overloaded_error"}


class FakeClaude(BaseHTTPRequestHandler):
    """The Claude Messages API: answers with the next queued reply and remembers what it was sent.

    A queued reply is a dict (sent back as the JSON text of the answer; "_stop" sets the
    stop reason), a string (sent back as it is), or a number (an error with that status).
    It accepts an Anthropic API key and requests signed with the test AWS key, as Bedrock would.
    """

    replies: list = []
    seen: list = []

    def log_message(self, *args) -> None:
        pass

    def _send(self, status: int, body: dict) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        if status >= 400:
            self.send_header("x-should-retry", "false")  # the SDK would otherwise wait and try again
        self.end_headers()
        self.wfile.write(raw)

    def _fail(self, status: int, message: str = "no") -> None:
        self._send(status, {"type": "error", "error": {"type": ERRORS.get(status, "api_error"), "message": message}})

    def _key_ok(self) -> bool:
        auth = self.headers.get("Authorization") or ""
        if GOOD_KEY in (self.headers.get("x-api-key"), auth.removeprefix("Bearer ")) or auth.startswith(f"AWS4-HMAC-SHA256 Credential={AWS_KEY}/"):
            return True  # the SDK sends an API key in one header or the other, depending on its version
        self._fail(401, "invalid x-api-key")
        return False

    def do_GET(self) -> None:
        if self._key_ok():
            self._send(200, {"id": self.path.rsplit("/", 1)[-1], "type": "model", "display_name": "Test", "created_at": "2026-01-01T00:00:00Z"})

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).seen.append({"path": self.path, "beta": self.headers.get("anthropic-beta", ""), "auth": self.headers.get("Authorization", ""), "body": body})
        if not self._key_ok():
            return
        if self.path.split("?")[0].endswith("/count_tokens"):
            self._send(200, {"input_tokens": 3})
            return
        reply = type(self).replies.pop(0)
        if isinstance(reply, int):
            self._fail(reply)
            return
        reply = dict(reply) if isinstance(reply, dict) else reply
        stop = reply.pop("_stop", "end_turn") if isinstance(reply, dict) else "end_turn"
        content = [{"type": "thinking", "thinking": "", "signature": "sig"}]
        if stop != "refusal":
            content.append({"type": "text", "text": reply if isinstance(reply, str) else json.dumps(reply)})
        self._send(200, {"id": "msg_1", "type": "message", "role": "assistant", "model": body["model"], "content": content,
                         "stop_reason": stop, "stop_sequence": None,
                         "stop_details": {"type": "refusal", "category": "cyber", "explanation": "declined"} if stop == "refusal" else None,
                         "usage": {"input_tokens": 100, "output_tokens": 50}})


# What the editor answers for one clip (see review.schema).
KEEP = {"keep": True, "reason": "Pure gameplay: a police chase that ends in a crash.", "start": "", "end": "", "section": "glitches",
        "title": "police car launches off the pier.", "hook": "It just took off", "summary": "A police car hits a ramp and flies into the sea.",
        "tags": ["GTA 6 police chase", "#gta 6 pier jump", "gta 6 police chase", "gta 6 casino trick"], "callouts": [], "cover": "", "score": 84}
DROP = {"keep": False, "reason": "A webcam box with a person is visible in every frame.", "start": "", "end": "", "section": "moments",
        "title": "", "hook": "", "summary": "", "tags": [], "callouts": [], "cover": "", "score": 0}


# --- Google: OAuth and the YouTube Data API -------------------------------------------

import base64 as _b64
import hashlib as _hashlib
import threading as _threading
from http.server import ThreadingHTTPServer as _Server
from urllib.parse import parse_qs as _parse_qs, urlparse as _urlparse


def serve(handler):
    """Start a stand-in on a free local port; returns (server, base address)."""
    server = _Server(("127.0.0.1", 0), handler)
    _threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


class Recorder(BaseHTTPRequestHandler):
    """Shared plumbing: every request is remembered as (method, path, query, headers, body)."""

    calls: list = []

    def log_message(self, *args) -> None:
        pass

    def reply(self, status: int, payload=None, headers: dict | None = None) -> None:
        raw = b"" if payload is None else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(raw)

    def take(self, read_body: bool = True):
        """Parse and remember the request; returns (path, query, body bytes)."""
        url = _urlparse(self.path)
        query = {k: v[0] for k, v in _parse_qs(url.query, keep_blank_values=True).items()}
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)) if read_body else b""
        type(self).calls.append({"method": self.command, "path": url.path, "query": query, "headers": dict(self.headers), "body": body})
        return url.path, query, body

    @staticmethod
    def form(body: bytes) -> dict:
        return {k: v[0] for k, v in _parse_qs(body.decode(), keep_blank_values=True).items()}


class FakeGoogle(Recorder):
    """Google's token service and the parts of the YouTube Data API the clipper uses.

    Set before a test: `challenge` (the PKCE challenge sent to the sign-in page), `scope`
    (what the user granted), `fail` (an error for one step, e.g. {"open": (403, "quotaExceeded")}),
    `drop_after` (close the connection after this many bytes of the first upload request) and
    `video` (what videos.list says about the uploaded video).
    """

    calls: list = []
    earned: list = [["2026-10", 3.5], ["2026-11", 12.25]]  # what YouTube Analytics says the channel earned, per month
    said: list = []  # the comments and replies the channel made
    threads: list = [  # the newest comments on a video: one unanswered, one the channel answered, one of the channel's own
        {"snippet": {"topLevelComment": {"id": "yc-1", "snippet": {"textDisplay": "How did you get the tank?", "authorChannelId": {"value": "UCviewer"},
                                                                   "publishedAt": "2026-11-20T12:00:00Z"}}, "totalReplyCount": 0}},
        {"snippet": {"topLevelComment": {"id": "yc-2", "snippet": {"textDisplay": "first", "authorChannelId": {"value": "UCother"}, "publishedAt": "2026-11-20T11:00:00Z"}}},
         "replies": {"comments": [{"snippet": {"authorChannelId": {"value": "UC123"}}}]}},
        {"snippet": {"topLevelComment": {"id": "yc-3", "snippet": {"textDisplay": "Credit: RawPlay", "authorChannelId": {"value": "UC123"}, "publishedAt": "2026-11-20T10:00:00Z"}}}},
    ]
    challenge = ""
    scope = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"
    refresh_token = "rt-good"
    channel: dict | None = {"id": "UC123", "snippet": {"title": "Leonida Clips", "customUrl": "@leonidaclips"}, "statistics": {"subscriberCount": "640", "hiddenSubscriberCount": False}}
    fail: dict = {}
    drop_after = 0
    stored = b""
    total = 0
    tokens = 0
    video: dict = {"status": {"uploadStatus": "processed", "privacyStatus": "public"}, "statistics": {"viewCount": "1234"}}
    made_private = False

    @classmethod
    def reset(cls) -> None:
        cls.calls, cls.fail, cls.stored, cls.total, cls.tokens, cls.drop_after, cls.made_private = [], {}, b"", 0, 0, 0, False
        cls.scope = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"
        cls.refresh_token, cls.challenge = "rt-good", ""
        cls.channel = {"id": "UC123", "snippet": {"title": "Leonida Clips", "customUrl": "@leonidaclips"}, "statistics": {"subscriberCount": "640", "hiddenSubscriberCount": False}}
        cls.video = {"status": {"uploadStatus": "processed", "privacyStatus": "public"}, "statistics": {"viewCount": "1234"}}
        cls.said = []

    def refuse(self, step: str) -> bool:
        """Answer with the error set for this step, once."""
        cls = type(self)
        if step not in cls.fail:
            return False
        status, reason = cls.fail.pop(step)
        self.reply(status, {"error": {"code": status, "message": f"{reason} happened", "errors": [{"domain": "youtube", "reason": reason}]}})
        return True

    def authorised(self) -> bool:
        if (self.headers.get("Authorization") or "").startswith("Bearer at-"):
            return True
        self.reply(401, {"error": {"code": 401, "message": "Invalid Credentials", "errors": [{"reason": "authError"}]}})
        return False

    def do_POST(self) -> None:
        cls = type(self)
        path, query, body = self.take()
        if path == "/token":
            fields = self.form(body)
            if self.refuse("token"):
                return
            if fields.get("grant_type") == "authorization_code":
                made = _b64.urlsafe_b64encode(_hashlib.sha256(fields.get("code_verifier", "").encode()).digest()).rstrip(b"=").decode()
                if fields.get("code") != "good-code" or made != cls.challenge:
                    self.reply(400, {"error": "invalid_grant", "error_description": "Malformed auth code."})
                    return
                cls.tokens += 1
                answer = {"access_token": f"at-{cls.tokens}", "expires_in": 3599, "scope": cls.scope, "token_type": "Bearer"}
                if cls.refresh_token:
                    answer["refresh_token"] = cls.refresh_token
                self.reply(200, answer)
            elif fields.get("grant_type") == "refresh_token" and fields.get("refresh_token") == cls.refresh_token:
                cls.tokens += 1
                self.reply(200, {"access_token": f"at-{cls.tokens}", "expires_in": 3599, "scope": cls.scope, "token_type": "Bearer"})
            else:
                self.reply(400, {"error": "invalid_grant", "error_description": "Token has been expired or revoked."})
        elif path == "/revoke":
            self.reply(200, {})
        elif path == "/upload/youtube/v3/videos":
            if self.refuse("open") or not self.authorised():
                return
            cls.stored, cls.total = b"", int(self.headers["X-Upload-Content-Length"])
            self.reply(200, None, {"Location": f"http://{self.headers['Host']}/upload/session/1"})
        elif path in ("/youtube/v3/comments", "/youtube/v3/commentThreads"):
            if not self.authorised():
                return
            cls.said.append(json.loads(body))
            self.reply(200, {"id": f"reply-{len(cls.said)}"})
        elif path == "/youtube/v3/playlistItems":
            if not self.refuse("playlist") and self.authorised():
                self.reply(200, {"id": "PLitem1"})
        else:
            self.reply(404, {"error": {"code": 404, "message": "not found"}})

    def do_PUT(self) -> None:
        cls = type(self)
        length = int(self.headers.get("Content-Length") or 0)
        span = self.headers.get("Content-Range", "")
        if cls.drop_after and length:  # the connection dies part-way through the file
            keep, cls.drop_after = cls.drop_after, 0
            self.take(read_body=False)
            cls.stored += self.rfile.read(keep)
            self.close_connection = True
            self.connection.close()
            return
        path, query, body = self.take()
        if self.refuse("put") or not self.authorised():
            return
        if span.startswith("bytes */"):  # how much has arrived?
            if len(cls.stored) == cls.total:
                self.finish_upload()
            else:
                self.reply(308, None, {"Range": f"bytes=0-{len(cls.stored) - 1}"} if cls.stored else {})
            return
        start = int(span.removeprefix("bytes ").split("-")[0]) if span else 0
        if start != len(cls.stored):
            self.reply(400, {"error": {"code": 400, "message": "wrong offset", "errors": [{"reason": "invalidRange"}]}})
            return
        cls.stored += body
        if len(cls.stored) == cls.total:
            self.finish_upload()
        else:
            self.reply(308, None, {"Range": f"bytes=0-{len(cls.stored) - 1}"})

    def finish_upload(self) -> None:
        cls = type(self)
        opened = next(c for c in reversed(cls.calls) if c["method"] == "POST" and c["path"] == "/upload/youtube/v3/videos")
        asked = json.loads(opened["body"])
        status = dict(asked["status"])
        if cls.made_private:
            status["privacyStatus"] = "private"
        self.reply(201, {"kind": "youtube#video", "id": "vid123", "snippet": asked["snippet"], "status": {**status, "uploadStatus": "uploaded"}})

    def do_GET(self) -> None:
        cls = type(self)
        path, query, _ = self.take()
        if not self.authorised():
            return
        if path == "/youtube/v3/channels":
            if not self.refuse("channel"):
                self.reply(200, {"items": [cls.channel] if cls.channel else []})
        elif path == "/youtube/v3/videos":
            if not self.refuse("videos"):
                self.reply(200, {"items": [{"id": query.get("id"), **cls.video}] if cls.video else []})
        elif path == "/v2/reports":  # YouTube Analytics: what the channel earned, per month
            if "yt-analytics-monetary.readonly" not in cls.scope:
                self.reply(403, {"error": {"code": 403, "message": "Insufficient Permission", "errors": [{"reason": "insufficientPermissions"}]}})
            else:
                self.reply(200, {"columnHeaders": [{"name": "month"}, {"name": "estimatedRevenue"}], "rows": cls.earned})
        elif path == "/youtube/v3/commentThreads":
            if "youtube.force-ssl" not in cls.scope:
                self.reply(403, {"error": {"code": 403, "message": "Insufficient Permission", "errors": [{"reason": "insufficientPermissions"}]}})
            else:
                self.reply(200, {"items": cls.threads})
        else:
            self.reply(404, {"error": {"code": 404, "message": "not found"}})


# --- TikTok: Login Kit and the Content Posting API ---------------------------------------

class FakeTikTok(Recorder):
    """TikTok's token service and posting API, as its documentation describes them."""

    calls: list = []
    challenge = ""
    scope = "user.info.basic,video.upload"
    serial = 0
    fail: dict = {}
    put_failures = 0
    stored = b""
    options: list = ["FOLLOWER_OF_CREATOR", "MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY"]
    longest = 600
    post_status: dict = {"status": "PROCESSING_UPLOAD"}
    off: dict = {"comment_disabled": False, "duet_disabled": True, "stitch_disabled": True}  # what the account itself has switched off

    @classmethod
    def reset(cls) -> None:
        cls.calls, cls.fail, cls.stored, cls.serial, cls.put_failures, cls.challenge = [], {}, b"", 0, 0, ""
        cls.scope, cls.longest, cls.post_status = "user.info.basic,video.upload", 600, {"status": "PROCESSING_UPLOAD"}
        cls.options = ["FOLLOWER_OF_CREATOR", "MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY"]
        cls.off = {"comment_disabled": False, "duet_disabled": True, "stitch_disabled": True}

    def answer(self, data: dict, code: str = "ok", status: int = 200, message: str = "") -> None:
        self.reply(status, {"data": data, "error": {"code": code, "message": message, "log_id": "20261120"}})

    def refuse(self, step: str) -> bool:
        cls = type(self)
        if step not in cls.fail:
            return False
        code, status = cls.fail.pop(step)
        self.answer({}, code, status, f"{code} happened")
        return True

    def authorised(self) -> bool:
        if self.headers.get("Authorization") == f"Bearer act-{type(self).serial}":
            return True
        self.answer({}, "access_token_invalid", 401, "The access token is invalid or not found in the request.")
        return False

    def do_POST(self) -> None:
        cls = type(self)
        path, query, body = self.take()
        if path == "/v2/oauth/token/":
            fields = self.form(body)
            good_code = fields.get("grant_type") == "authorization_code" and fields.get("code") == "good-code" \
                and _hashlib.sha256(fields.get("code_verifier", "").encode()).hexdigest() == cls.challenge
            good_refresh = fields.get("grant_type") == "refresh_token" and fields.get("refresh_token") == f"rft-{cls.serial}" and cls.serial
            if fields.get("client_secret") != "tt-secret" or not (good_code or good_refresh):
                self.reply(400, {"error": "invalid_grant", "error_description": "Authorization code is expired.", "log_id": "20261120"})
                return
            cls.serial += 1  # a refresh token works once: every answer carries the next one
            self.reply(200, {"access_token": f"act-{cls.serial}", "expires_in": 7200, "refresh_token": f"rft-{cls.serial}", "refresh_expires_in": 15552000,
                             "open_id": "open-1", "scope": cls.scope, "token_type": "Bearer"})
        elif path == "/v2/oauth/revoke/":
            self.reply(200, {})
        elif not self.authorised():
            return
        elif path in ("/v2/post/publish/inbox/video/init/", "/v2/post/publish/video/init/"):
            if not self.refuse("init"):
                cls.stored = b""
                self.answer({"publish_id": "v_pub_file~v2.123", "upload_url": f"http://{self.headers['Host']}/upload/video/?upload_id=1&upload_token=tok"})
        elif path == "/v2/post/publish/creator_info/query/":
            if not self.refuse("creator"):
                self.answer({"creator_username": "leonidaclips", "creator_nickname": "Leonida Clips", "privacy_level_options": cls.options,
                             **cls.off, "max_video_post_duration_sec": cls.longest})
        elif path == "/v2/post/publish/status/fetch/":
            if not self.refuse("status"):
                self.answer(dict(cls.post_status))
        else:
            self.answer({}, "invalid_params", 400)

    def do_PUT(self) -> None:
        cls = type(self)
        path, query, body = self.take()
        if cls.put_failures:
            cls.put_failures -= 1
            self.reply(500, {"error": {"code": "internal_error"}})
            return
        first, rest = self.headers["Content-Range"].removeprefix("bytes ").split("-")
        last, total = rest.split("/")
        if int(first) != len(cls.stored) or int(last) - int(first) + 1 != len(body):
            self.reply(416, {"error": {"code": "invalid_params"}})
            return
        cls.stored += body
        self.reply(201 if len(cls.stored) == int(total) else 206, {})

    def do_GET(self) -> None:
        path, query, _ = self.take()
        if self.authorised() and path == "/v2/user/info/":
            self.answer({"user": {"open_id": "open-1", "display_name": "Leonida Clips"}})


# --- Meta: Facebook Login, Page reels and videos, Instagram reels --------------------------

class FakeMeta(Recorder):
    """The parts of Meta's Graph API (v27.0) and its upload hosts that the clipper uses."""

    calls: list = []
    pages: list = []
    fail: dict = {}
    stored = b""
    drop_after = 0
    container: list = []
    video_status: dict = {}
    insights = True
    said: list = []  # the replies made to comments

    @classmethod
    def reset(cls) -> None:
        cls.calls, cls.fail, cls.stored, cls.drop_after, cls.container, cls.insights = [], {}, b"", 0, [], True
        cls.pages = [{"id": "222", "name": "Other Page", "access_token": "page-2"},
                     {"id": "111", "name": "Leonida Clips", "access_token": "page-1", "instagram_business_account": {"id": "178"}}]
        cls.video_status = {"video_status": "ready", "publishing_phase": {"status": "complete", "publish_status": "published"}}

    def error(self, code: int, message: str, status: int = 400) -> None:
        self.reply(status, {"error": {"message": message, "type": "OAuthException", "code": code, "fbtrace_id": "A1"}})

    def refuse(self, step: str) -> bool:
        cls = type(self)
        if step not in cls.fail:
            return False
        code, message = cls.fail.pop(step)
        self.error(code, message)
        return True

    def token(self, query: dict, wanted: tuple) -> bool:
        if query.get("access_token") in wanted or (self.headers.get("Authorization") or "").removeprefix("OAuth ") in wanted:
            return True
        self.error(190, "Error validating access token: Session has expired.", 401)
        return False

    def do_GET(self) -> None:
        cls = type(self)
        path, query, _ = self.take()
        path = path.removeprefix("/v27.0")
        if path == "/oauth/access_token":
            if self.refuse("token"):
                return
            if query.get("client_secret") != "app-secret":
                self.error(1, "Error validating client secret.")
            elif query.get("grant_type") == "fb_exchange_token" and query.get("fb_exchange_token") in ("short-1", "explorer-token"):
                self.reply(200, {"access_token": "long-1", "token_type": "bearer", "expires_in": 5183944})
            elif query.get("code") == "good-code":
                self.reply(200, {"access_token": "short-1", "token_type": "bearer"})
            else:
                self.error(100, "Invalid verification code format.")
        elif path == "/me/accounts":
            if not self.refuse("accounts") and self.token(query, ("long-1",)):
                self.reply(200, {"data": cls.pages})
        elif path == "/178":
            self.reply(200, {"followers_count": 4321, "id": "178"} if query.get("fields") == "followers_count" else {"username": "leonidaclips", "id": "178"})
        elif path == "/111":
            self.reply(200, {"followers_count": 987, "id": "111"})
        elif path == "/m-1/comments":  # Instagram: one unanswered comment, one the account answered
            self.reply(200, {"data": [{"id": "ic-1", "text": "Where is this spot?", "timestamp": "2026-11-20T12:30:00+0000", "from": {"id": "555"}},
                                      {"id": "ic-2", "text": "nice", "timestamp": "2026-11-20T09:00:00+0000", "from": {"id": "556"}, "replies": {"data": [{"from": {"id": "178"}}]}}]})
        elif path in ("/900/comments", "/901/comments"):  # Facebook
            self.reply(200, {"data": [{"id": "fc-1", "message": "Which mission is that?", "created_time": "2026-11-20T13:00:00+0000", "from": {"id": "777"}}]})
        elif path in ("/900", "/901"):
            if not self.refuse("status") and self.token(query, ("page-1",)):
                status = {**cls.video_status, "uploading_phase": {"status": "in_progress", "bytes_transfered": len(cls.stored)}}
                self.reply(200, {"id": path[1:], "status": status, "permalink_url": f"/reel{path}/"})
        elif path == "/upload:abc":
            self.reply(200, {"id": "upload:abc", "file_offset": len(cls.stored)})
        elif path == "/c-1":
            if "video_status" in query.get("fields", ""):
                self.reply(200, {"video_status": {"uploading_phase": {"status": "in_progress", "bytes_transferred": len(cls.stored)}}})
            else:
                code = cls.container.pop(0) if cls.container else "FINISHED"
                self.reply(200, {"status_code": code, "status": "2207026" if code == "ERROR" else "Finished", "id": "c-1"})
        elif path == "/m-1":
            self.reply(200, {"permalink": "https://www.instagram.com/reel/Cabc/", "id": "m-1"})
        elif path == "/m-1/insights":
            if cls.insights:
                self.reply(200, {"data": [{"name": "views", "period": "lifetime", "values": [{"value": 77}]}]})
            else:
                self.error(10, "Application does not have permission for this action")
        else:
            self.error(100, "Unsupported get request.", 404)

    def do_DELETE(self) -> None:
        self.take()
        self.reply(200, {"success": True})

    def do_POST(self) -> None:
        cls = type(self)
        if "/rupload/" in self.path or self.path.startswith("/v27.0/upload:"):
            offset = int(self.headers.get("offset") or self.headers.get("file_offset") or 0)
            length = int(self.headers.get("Content-Length") or 0)
            if cls.drop_after:  # the connection dies part-way through the file
                keep, cls.drop_after = cls.drop_after, 0
                self.take(read_body=False)
                cls.stored = cls.stored[:offset] + self.rfile.read(keep)
                self.close_connection = True
                self.connection.close()
                return
            path, query, body = self.take()
            if self.refuse("upload"):
                return
            if offset != len(cls.stored):
                self.error(6000, "There was a problem uploading your video file.")
                return
            cls.stored += body
            self.reply(200, {"h": "handle-1"} if path.startswith("/v27.0/upload:") else {"success": True})
            return
        path, query, body = self.take()
        path = path.removeprefix("/v27.0")
        sent = json.loads(body) if body[:1] == b"{" else self.form(body)
        if path == "/111/video_reels":
            if (sent or query).get("upload_phase") == "start":
                if not self.refuse("start"):
                    cls.stored = b""
                    self.reply(200, {"video_id": "900", "upload_url": f"http://{self.headers['Host']}/rupload/video-upload/v27.0/900"})
            elif not self.refuse("finish"):
                self.reply(200, {"success": True})
        elif path == "/app-1/uploads":
            cls.stored = b""
            self.reply(200, {"id": "upload:abc"})
        elif path == "/111/videos":
            if not self.refuse("videos"):
                self.reply(200, {"id": "901"})
        elif path == "/178/media":
            if not self.refuse("media"):
                cls.stored = b""
                self.reply(200, {"id": "c-1", "uri": f"http://{self.headers['Host']}/rupload/ig-api-upload/v27.0/c-1"})
        elif path == "/178/media_publish":
            if not self.refuse("publish"):
                self.reply(200, {"id": "m-1"})
        elif path in ("/ic-1/replies", "/fc-1/comments"):
            cls.said.append((path, (sent or query).get("message", "")))
            self.reply(200, {"id": "r-1"})
        else:
            self.error(100, "Unsupported post request.", 404)
