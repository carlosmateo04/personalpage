"""The live publisher, exercised against a local stand-in for the Upload-Post API.

The stand-in's response shapes are assumptions; what these tests pin down is the
request this code sends through the official client.
"""

import json
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

import pytest

from clipper import publish


class FakeApi(BaseHTTPRequestHandler):
    calls: list = []

    def log_message(self, *args):
        pass

    def _reply(self, payload, code=200):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        FakeApi.calls.append(("GET", self.path, dict(self.headers), {}))
        if self.path.startswith("/api/uploadposts/users"):
            self._reply({"success": True, "profiles": [{"username": "gta6clips", "social_accounts": {"tiktok": {}}}]})
        elif self.path.startswith("/api/uploadposts/status"):
            self._reply({"status": "completed", "results": [
                {"platform": "tiktok", "success": True, "url": "https://www.tiktok.com/@x/video/1"},
                {"platform": "youtube", "success": True, "post_url": "https://youtube.com/shorts/abc"},
            ]})
        elif self.path.startswith("/api/uploadposts/post-analytics"):
            self._reply({"platforms": {"tiktok": {"post_metrics": {"views": 1234}}, "youtube": {"post_metrics": {"views": 56}}}})
        else:
            self._reply({"error": "not found"}, 404)

    def do_POST(self):
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        boundary = self.headers["Content-Type"].split("boundary=")[1].encode()
        fields: dict = {}
        for part in raw.split(b"--" + boundary):
            head, _, value = part.partition(b"\r\n\r\n")
            if b'name="' not in head:
                continue
            name = head.split(b'name="')[1].split(b'"')[0].decode()
            value = value.rsplit(b"\r\n", 1)[0]
            fields.setdefault(name, []).append(len(value) if b"filename=" in head else value.decode())
        FakeApi.calls.append(("POST", self.path, dict(self.headers), fields))
        if fields.get("user") == ["nobody"]:
            self._reply({"success": False, "message": "Profile not found"}, 404)
        else:
            self._reply({"success": True, "request_id": "req-1", "job_id": "job-1"})


@pytest.fixture
def api(monkeypatch):
    from upload_post import UploadPostClient

    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeApi)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(UploadPostClient, "BASE_URL", f"http://127.0.0.1:{server.server_port}/api")
    monkeypatch.setenv("UPLOAD_POST_API_KEY", "test-key")
    FakeApi.calls = []
    yield FakeApi
    server.shutdown()


def _clip():
    return {"id": "c123", "meta": {
        "title": "No way!", "tiktok": {"title": "#ad No way! #gta6"}, "instagram": {"title": "#ad No way! #gta6 #gaming"},
        "facebook": {"title": "#ad No way!"}, "youtube": {"title": "No way! | GTA 6 #shorts", "description": "desc", "tags": ["gta 6", "gta vi"]},
    }}


def test_live_upload_sends_the_expected_request(cfg, api, tmp_path):
    cfg.publish.mode, cfg.publish.profile, cfg.publish.facebook_page_id = "live", "gta6clips", "999"
    cfg.campaign.paid = True
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"0" * 2048)
    when = datetime(2026, 11, 19, 15, 0, tzinfo=ZoneInfo("America/New_York"))  # sent to the API in UTC
    pub = publish.publisher(cfg)
    assert "found" in pub.check()
    result = pub.publish(_clip(), video, when)
    assert result["request_id"] == "req-1" and result["job_id"] == "job-1"
    method, path, headers, f = api.calls[-1]
    assert (method, path) == ("POST", "/api/upload")
    assert headers["Authorization"] == "Apikey test-key"
    assert headers["Idempotency-Key"] == "c123"
    assert f["video"] == [2048]
    assert f["user"] == ["gta6clips"]
    assert f["platform[]"] == ["tiktok", "instagram", "youtube", "facebook"]
    assert f["scheduled_date"] == ["2026-11-19T20:00:00Z"]
    assert f["tiktok_title"] == ["#ad No way! #gta6"] and f["post_mode"] == ["DIRECT_POST"]
    assert f["media_type"] == ["REELS"] and f["facebook_media_type"] == ["REELS"] and f["facebook_page_id"] == ["999"]
    assert f["youtube_title"] == ["No way! | GTA 6 #shorts"] and f["tags[]"] == ["gta 6", "gta vi"]
    assert f["privacyStatus"] == ["public"] and f["selfDeclaredMadeForKids"] == ["false"]
    assert f["paid_partnership"] == ["true"]


def test_unpaid_clip_carries_no_paid_label(cfg, api, tmp_path):
    cfg.publish.mode, cfg.publish.profile = "live", "gta6clips"
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"0")
    publish.publisher(cfg).publish(_clip(), video, datetime(2026, 11, 19, 20, 0, tzinfo=timezone.utc))
    assert "paid_partnership" not in api.calls[-1][3]


def test_live_errors_are_reported(cfg, api, tmp_path):
    cfg.publish.mode, cfg.publish.profile = "live", "nobody"
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"0")
    pub = publish.publisher(cfg)
    with pytest.raises(publish.PublishError, match="not found"):
        pub.check()
    with pytest.raises(publish.PublishError, match="Profile not found"):
        pub.publish(_clip(), video, datetime(2026, 11, 19, 20, 0, tzinfo=timezone.utc))
    cfg.publish.profile = ""
    with pytest.raises(publish.PublishError, match="publish.profile"):
        publish.publisher(cfg)


def test_refresh_collects_links_and_views(cfg, api):
    cfg.publish.mode, cfg.publish.profile = "live", "gta6clips"
    got = publish.publisher(cfg).refresh({"id": "c123", "publish": {"request_id": "req-1"}})
    assert got["links"] == {"tiktok": "https://www.tiktok.com/@x/video/1", "youtube": "https://youtube.com/shorts/abc"}
    assert got["views"] == {"tiktok": 1234, "youtube": 56}
    assert got["state"] == "completed"


def test_dry_run_writes_the_outbox_and_sends_nothing(cfg, api, tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"0")
    pub = publish.publisher(cfg)
    when = datetime(2026, 11, 19, 20, 0, tzinfo=timezone.utc)
    assert pub.publish(_clip(), video, when) == {"mode": "dry_run"}
    pub.publish(_clip(), video, when)  # the same clip again replaces its entry
    outbox = json.loads((cfg.path("out") / "outbox.json").read_text())
    assert len(outbox) == 1 and outbox[0]["clip"] == "c123"
    assert api.calls == []


def test_dropped_connection_is_retried_with_the_same_idempotency_key(cfg, api, tmp_path, monkeypatch):
    from upload_post import UploadPostClient, UploadPostError

    cfg.publish.mode, cfg.publish.profile = "live", "gta6clips"
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"0")
    real, attempts = UploadPostClient.upload_video, []

    def flaky(self, *a, **kw):
        attempts.append(kw["idempotency_key"])
        if len(attempts) < 3:
            raise UploadPostError("connection reset")  # no status code: the network dropped
        return real(self, *a, **kw)

    monkeypatch.setattr(UploadPostClient, "upload_video", flaky)
    monkeypatch.setattr(publish.time, "sleep", lambda s: None)
    result = publish.publisher(cfg).publish(_clip(), video, datetime(2026, 11, 19, 20, 0, tzinfo=timezone.utc))
    assert result["request_id"] == "req-1" and attempts == ["c123"] * 3
