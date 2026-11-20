"""The app's server: what the page reads and the actions its buttons trigger."""

import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer

import pytest

from clipper import pipeline, settings, ui
from clipper.state import Ledger, now_iso
from test_publish import FakeApi

TOKEN = "test-token"


@pytest.fixture
def app(tmp_path, monkeypatch):
    """A running app on a free port, with its data in a temporary folder and one finished clip."""
    monkeypatch.setenv("CLIPPER_DATA", str(tmp_path))
    monkeypatch.delenv("UPLOAD_POST_API_KEY", raising=False)
    config_file = tmp_path / "config.toml"
    config_file.write_text('[[sources]]\ntype = "folder"\npath = "inbox"\n\n[publish]\nhold_for_review = true\n', encoding="utf-8")
    run = tmp_path / "out" / "20261120-000000"
    run.mkdir(parents=True)
    video = run / "c1.mp4"
    video.write_bytes(bytes(range(256)) * 40)  # 10,240 bytes standing in for a clip
    ledger = Ledger(tmp_path / "state" / "ledger.json")
    ledger.clips["c1"] = {
        "id": "c1", "status": "held", "run": "20261120-000000", "created": now_iso(), "file": str(video),
        "start": 10.0, "end": 100.0, "score": 3.0, "section": "glitches", "creator": "RawPlay", "origin": "https://example.com/v",
        "permission": "", "slot": "2026-11-20T11:30:00-05:00", "title": "Car falls through the map",
        "meta": {"title": "Car falls through the map", "tiktok": {"title": "Car falls through the map #gta6"},
                 "instagram": {"title": "Car falls through the map #gta6"}, "facebook": {"title": "Car falls through the map #gta6"},
                 "youtube": {"title": "Car falls through the map #shorts", "description": "desc", "tags": ["gta 6"]}},
    }
    ledger.save()
    instance = ui.App(str(config_file), TOKEN)
    server = ThreadingHTTPServer(("127.0.0.1", 0), ui.handler_for(instance))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    instance.url = f"http://127.0.0.1:{server.server_port}"
    instance.data = tmp_path
    yield instance
    instance.stop_run()
    server.shutdown()


def call(app, path, body=None, token=TOKEN, headers=None):
    req = urllib.request.Request(app.url + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"X-Clipper-Token": token, "Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if resp.headers.get("Content-Type", "").startswith("application/json") else raw), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, (json.loads(raw) if raw[:1] == b"{" else raw), dict(exc.headers)


def test_page_and_state(app):
    status, page, _ = call(app, "/")
    assert status == 200 and b"Make clips now" in page and TOKEN.encode() in page and b"__TOKEN__" not in page
    status, state, _ = call(app, "/api/state")
    assert status == 200
    clip = state["clips"][0]
    assert (clip["id"], clip["state"], clip["section"], clip["seconds"]) == ("c1", "ready", "GLITCH", 90)
    assert clip["media"] == "/media/20261120-000000/c1.mp4" and clip["when"]
    assert state["accounts"] == {"connected": False, "profiles": [], "error": ""}
    assert state["settings"]["searches"] == [] and state["settings"]["auto_post"] is False


def test_actions_need_the_token_and_a_local_address(app):
    assert call(app, "/api/state", token="wrong")[0] == 403
    assert call(app, "/api/drop", {"id": "c1"}, token="wrong")[0] == 403
    assert call(app, "/api/state", headers={"Host": "evil.example.com"})[0] == 403  # another site pointed at this port
    assert Ledger(app.data / "state" / "ledger.json").clips["c1"]["status"] == "held"


def test_video_is_served_in_ranges_and_nothing_outside_the_clips_folder(app):
    (app.data / "secret.txt").write_text("no", encoding="utf-8")
    status, body, headers = call(app, "/media/20261120-000000/c1.mp4", headers={"Range": "bytes=256-511"})
    assert status == 206 and body == bytes(range(256)) and headers["Content-Range"] == "bytes 256-511/10240"
    status, body, _ = call(app, "/media/20261120-000000/c1.mp4")
    assert status == 200 and len(body) == 10240
    assert call(app, "/media/../secret.txt")[0] == 404
    assert call(app, "/media/%2e%2e/secret.txt")[0] == 404
    assert call(app, "/media/20261120-000000/nope.mp4")[0] == 404


def test_editing_a_clips_text(app):
    status, _, _ = call(app, "/api/clip", {"id": "c1", "title": "Car sinks into the road", "caption": "Look at this #gta6", "description": "New description"})
    assert status == 200
    rec = Ledger(app.data / "state" / "ledger.json").clips["c1"]
    assert rec["meta"]["title"] == "Car sinks into the road" and rec["meta"]["youtube"]["title"] == "Car sinks into the road #shorts"
    assert {rec["meta"][p]["title"] for p in ("tiktok", "instagram", "facebook")} == {"Look at this #gta6"}
    kit = (app.data / "out" / "20261120-000000" / "c1.txt").read_text(encoding="utf-8")
    assert "Title: Car sinks into the road #shorts" in kit and "Look at this #gta6" in kit and "New description" in kit
    assert call(app, "/api/clip", {"id": "missing", "title": "x"})[0] == 400


def test_posting_needs_a_connection_then_sends_the_edited_text(app, monkeypatch):
    from upload_post import UploadPostClient

    status, body, _ = call(app, "/api/post", {"id": "c1", "when": "now"})
    assert status == 400 and "profile" in body["error"]  # not connected yet

    api = ThreadingHTTPServer(("127.0.0.1", 0), FakeApi)
    threading.Thread(target=api.serve_forever, daemon=True).start()
    monkeypatch.setattr(UploadPostClient, "BASE_URL", f"http://127.0.0.1:{api.server_port}/api")
    FakeApi.calls = []
    try:
        status, body, _ = call(app, "/api/connect", {"api_key": "  my-key  "})
        assert status == 200 and body["accounts"]["profiles"] == [{"name": "gta6clips", "platforms": ["tiktok"]}]
        assert settings.get_secret(app.data, "upload_post_api_key") == "my-key"
        assert "my-key" not in json.dumps(call(app, "/api/state")[1])  # the key never goes back to the page
        assert call(app, "/api/state")[1]["settings"]["profile"] == "gta6clips"  # the only profile is chosen

        call(app, "/api/clip", {"id": "c1", "caption": "Edited caption #gta6"})
        status, _, _ = call(app, "/api/post", {"id": "c1", "when": "now", "platforms": ["tiktok", "youtube"]})
        assert status == 200
        method, path, headers, fields = FakeApi.calls[-1]
        assert (method, path) == ("POST", "/api/upload") and headers["Authorization"] == "Apikey my-key"
        assert fields["platform[]"] == ["tiktok", "youtube"] and "scheduled_date" not in fields  # now, to the ticked accounts only
        assert fields["tiktok_title"] == ["Edited caption #gta6"] and "instagram_title" not in fields
        rec = Ledger(app.data / "state" / "ledger.json").clips["c1"]
        assert rec["status"] == "posted" and rec["publish"]["request_id"] == "req-1"
        assert call(app, "/api/state")[1]["clips"][0]["state"] == "posted"
        assert call(app, "/api/post", {"id": "c1", "when": "now"})[0] == 400  # not twice
        assert call(app, "/api/drop", {"id": "c1"})[0] == 400  # and a sent clip cannot be dropped

        call(app, "/api/disconnect", {})
        assert settings.get_secret(app.data, "upload_post_api_key") == ""
        assert call(app, "/api/state")[1]["accounts"]["connected"] is False
    finally:
        api.shutdown()


def test_next_free_time_schedules_instead_of_posting(app, monkeypatch):
    from upload_post import UploadPostClient

    api = ThreadingHTTPServer(("127.0.0.1", 0), FakeApi)
    threading.Thread(target=api.serve_forever, daemon=True).start()
    monkeypatch.setattr(UploadPostClient, "BASE_URL", f"http://127.0.0.1:{api.server_port}/api")
    FakeApi.calls = []
    try:
        call(app, "/api/connect", {"api_key": "my-key"})
        status, body, _ = call(app, "/api/post-all", {})
        assert status == 200 and body["count"] == 1
        fields = FakeApi.calls[-1][3]
        when = datetime.strptime(fields["scheduled_date"][0], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        assert when > datetime.now(timezone.utc)
        assert Ledger(app.data / "state" / "ledger.json").clips["c1"]["status"] == "scheduled"
        assert call(app, "/api/state")[1]["clips"][0]["state"] == "queued"
    finally:
        api.shutdown()


def test_settings_survive_and_bad_ones_are_refused(app):
    wanted = {"moments": {"min_len": 85, "max_len": 95, "whole_clip_min": 85, "whole_clip_max": 95, "max_per_source": 2},
              "schedule": {"slots": ["09:00", "18:30"], "timezone": "America/Chicago"}, "brand": {"handle": "@leonidaclips"},
              "searches": [{"query": "gta 6 police chase no commentary", "section": "moments"}, {"query": "  ", "section": "tricks"}]}
    assert call(app, "/api/settings", wanted)[0] == 200
    got = call(app, "/api/state")[1]["settings"]
    assert (got["min_len"], got["max_len"], got["slots"], got["handle"]) == (85, 95, ["09:00", "18:30"], "@leonidaclips")
    assert got["searches"] == [{"query": "gta 6 police chase no commentary", "section": "moments"}]  # the blank row is dropped
    cfg = app.cfg()
    assert [s.type for s in cfg.sources] == ["ytdlp", "folder"]  # the inbox stays
    assert "search_query=gta+6+police+chase+no+commentary" in cfg.sources[0].url and cfg.sources[0].title_any

    status, body, _ = call(app, "/api/settings", {"schedule": {"slots": ["25:99"]}})
    assert status == 400 and "HH:MM" in body["error"]
    assert call(app, "/api/state")[1]["settings"]["slots"] == ["09:00", "18:30"]  # the bad value was not kept
    status, body, _ = call(app, "/api/settings", {"paths": {"out": "C:/"}})
    assert status == 400 and "cannot be changed" in body["error"]


def test_dropping_and_running(app):
    assert call(app, "/api/drop", {"id": "c1"})[0] == 200
    assert call(app, "/api/state")[1]["clips"][0]["state"] == "dropped"
    assert call(app, "/api/run", {})[0] == 200
    assert call(app, "/api/run", {})[0] == 400  # one run at a time
    app.proc.wait(timeout=120)
    import time
    time.sleep(0.5)
    state = call(app, "/api/state")[1]
    assert state["run"]["running"] is False and any("plan" in line for line in state["run"]["log"])
    assert state["run"]["log"][-1].startswith("Finished")
