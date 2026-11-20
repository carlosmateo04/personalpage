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
    assert rec["meta"]["title"] == "Car sinks into the road" and rec["meta"]["youtube"]["title"] == "Car sinks into the road | GTA 6 Glitch #shorts"
    assert {rec["meta"][p]["title"] for p in ("tiktok", "instagram", "facebook")} == {"Look at this #gta6"}
    kit = (app.data / "out" / "20261120-000000" / "c1.txt").read_text(encoding="utf-8")
    assert "Title: Car sinks into the road | GTA 6 Glitch #shorts" in kit and "Look at this #gta6" in kit and "New description" in kit
    assert call(app, "/api/clip", {"id": "missing", "title": "x"})[0] == 400


def test_posting_needs_a_connection_then_sends_the_edited_text(app, monkeypatch):
    from upload_post import UploadPostClient

    status, body, _ = call(app, "/api/post", {"id": "c1", "when": "now"})
    assert status == 400 and "tiktok: no account is connected" in body["error"]  # not connected yet
    assert call(app, "/api/state")[1]["clips"][0]["state"] == "ready"  # and the clip is still waiting, unharmed

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
        assert {name: (post["state"], post["via"], post["id"]) for name, post in rec["posts"].items()} == {
            "tiktok": ("processing", "service", "req-1"), "youtube": ("processing", "service", "req-1")}
        assert call(app, "/api/state")[1]["clips"][0]["state"] == "queued"  # with the service until it reports the links
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


# --- the versions of a clip, TikTok's options and posts made by hand ---------------------

def test_the_page_is_told_which_version_each_platform_gets(app):
    short = app.data / "out" / "20261120-000000" / "c1.59s.mp4"
    short.write_bytes(b"short version")
    ledger = Ledger(app.data / "state" / "ledger.json")
    ledger.clips["c1"]["cuts"] = {"youtube": {"start": 36.0, "end": 95.0, "file": str(short)}}
    ledger.clips["c1"]["unfit"] = {"facebook": "the clip is 90 seconds; facebook is set to take 120 or more"}
    ledger.save()
    clip = call(app, "/api/state")[1]["clips"][0]
    assert clip["versions"] == [{"seconds": 90, "platforms": ["tiktok", "instagram"], "media": "/media/20261120-000000/c1.mp4"},
                                {"seconds": 59, "platforms": ["youtube"], "media": "/media/20261120-000000/c1.59s.mp4"}]
    assert clip["unfit"] == {"facebook": "the clip is 90 seconds; facebook is set to take 120 or more"}
    assert call(app, "/media/20261120-000000/c1.59s.mp4")[1] == b"short version"  # the page can play it
    status, body, _ = call(app, "/api/reveal", {"id": "c1", "platform": "youtube"})  # Explorer is opened on Windows; elsewhere the path is said
    assert status in (200, 400) and (status == 200 or "c1.59s.mp4" in body["error"])


def test_the_cut_each_platform_gets_is_set_in_the_settings(app):
    got = call(app, "/api/state")[1]["settings"]["edits"]
    assert got == {"tiktok": {"min_len": 61, "max_len": 0}, "youtube": {"min_len": 0, "max_len": 59},
                   "facebook": {"min_len": 0, "max_len": 90}, "instagram": {"min_len": 0, "max_len": 180}}
    assert call(app, "/api/settings", {"edits": {"youtube": {"min_len": 0, "max_len": 180}, "tiktok": {"min_len": 0, "max_len": 0}}})[0] == 200
    got = call(app, "/api/state")[1]["settings"]["edits"]
    assert got["youtube"] == {"min_len": 0, "max_len": 180} and got["tiktok"] == {"min_len": 0, "max_len": 0} and got["facebook"]["max_len"] == 90
    status, body, _ = call(app, "/api/settings", {"edits": {"facebook": {"min_len": 120, "max_len": 90}}})
    assert status == 400 and "min_len cannot be above max_len" in body["error"]
    assert call(app, "/api/state")[1]["settings"]["edits"]["facebook"] == {"min_len": 0, "max_len": 90}  # the bad value was not kept


def test_tiktok_options_are_set_for_all_clips_and_for_one(app):
    state = call(app, "/api/state")[1]
    assert state["posting"]["tiktok"]["mode"] == "draft" and state["posting"]["tiktok"]["upload_page"] == "https://www.tiktok.com/tiktokstudio/upload"
    assert state["posting"]["tiktok"]["defaults"] == {"audience": "PUBLIC_TO_EVERYONE", "comments": True, "duet": True, "stitch": True, "own_brand": False, "paid": False}
    assert [a["label"] for a in state["posting"]["tiktok"]["audiences"]] == ["Everyone", "Friends", "Followers", "Only me"]
    assert call(app, "/api/settings", {"publish": {"tiktok_direct": "public", "tiktok_privacy_level": "MUTUAL_FOLLOW_FRIENDS", "tiktok_duet": False}})[0] == 200
    state = call(app, "/api/state")[1]
    assert state["posting"]["tiktok"]["mode"] == "public" and state["settings"]["tiktok_direct"] == "public"
    assert state["posting"]["tiktok"]["defaults"]["audience"] == "MUTUAL_FOLLOW_FRIENDS" and state["posting"]["tiktok"]["defaults"]["duet"] is False
    assert call(app, "/api/settings", {"publish": {"tiktok_direct": "everyone"}})[0] == 400

    assert call(app, "/api/clip", {"id": "c1", "tiktok": {"audience": "SELF_ONLY", "comments": False, "own_brand": True, "anything": "else"}})[0] == 200
    assert call(app, "/api/state")[1]["clips"][0]["tiktok"] == {"audience": "SELF_ONLY", "comments": False, "own_brand": True}
    assert Ledger(app.data / "state" / "ledger.json").clips["c1"]["title"] == "Car falls through the map"  # the text was left alone
    status, body, _ = call(app, "/api/clip", {"id": "c1", "tiktok": {"audience": "THE_WORLD"}})
    assert status == 400 and "TikTok audience" in body["error"]


def test_a_clip_posted_by_hand_is_marked_and_the_others_can_still_be_sent(app, monkeypatch):
    status, body, _ = call(app, "/api/posted-by-hand", {"id": "c1", "platform": "tiktok", "link": "tiktok.com/x"})
    assert status == 400 and "https://" in body["error"]
    assert call(app, "/api/posted-by-hand", {"id": "c1", "platform": "tiktok", "link": "https://www.tiktok.com/@me/video/7"})[0] == 200
    clip = call(app, "/api/state")[1]["clips"][0]
    assert clip["state"] == "posted" and clip["posts"]["tiktok"] == {"state": "posted", "link": "https://www.tiktok.com/@me/video/7", "views": None,
                                                                       "note": "posted by hand", "via": "hand"}
    assert call(app, "/api/posted-by-hand", {"id": "c1", "platform": "tiktok"})[0] == 400  # once
    assert call(app, "/api/posted-by-hand", {"id": "missing", "platform": "tiktok"})[0] == 400

    from upload_post import UploadPostClient

    api = ThreadingHTTPServer(("127.0.0.1", 0), FakeApi)
    threading.Thread(target=api.serve_forever, daemon=True).start()
    monkeypatch.setattr(UploadPostClient, "BASE_URL", f"http://127.0.0.1:{api.server_port}/api")
    FakeApi.calls = []
    assert call(app, "/api/connect", {"api_key": "my-key"})[0] == 200
    assert call(app, "/api/post", {"id": "c1", "when": "now"})[0] == 400  # without names nothing is sent for a clip that is already out
    assert call(app, "/api/post", {"id": "c1", "when": "now", "platforms": ["tiktok"]})[0] == 400  # and never where it already is
    assert call(app, "/api/post", {"id": "c1", "when": "now", "platforms": ["youtube"]})[0] == 200  # the others, when named
    method, path, headers, fields = FakeApi.calls[-1]
    assert (method, path) == ("POST", "/api/upload") and fields["platform[]"] == ["youtube"]
    posts = call(app, "/api/state")[1]["clips"][0]["posts"]
    assert (posts["tiktok"]["via"], posts["youtube"]["state"], posts["youtube"]["via"]) == ("hand", "processing", "service")
    api.shutdown()


def test_the_monetization_check_and_its_settings_are_on_the_page(app):
    from clipper import monetize

    state = call(app, "/api/state")[1]
    assert state["clips"][0]["money"] == {}  # a clip made before the check existed
    assert state["settings"]["monetize"] == {"enabled": True, "calm_open": 15.0, "mute_profanity": True, "music": "note", "strict": True,
                                             "listens": monetize.tagger_ready(app.cfg())}
    ledger = Ledger(app.data / "state" / "ledger.json")
    rec = ledger.clips["c1"]
    rec.update(review={"keep": True}, graphic=[13.0], strong=[], seen={"music": [0.0] * 18, "frozen": 0.0, "height": 1080})
    rec["money"] = monetize.check(app.cfg(), rec)
    ledger.save()
    clip = call(app, "/api/state")[1]["clips"][0]
    opening = next(line for line in clip["money"]["youtube"] if line["rule"] == "opening")
    assert opening["level"] == "fail" and "YouTube" not in clip["unfit"] and "first 7 seconds" in clip["unfit"]["youtube"]  # and it is not sent there

    call(app, "/api/clip", {"id": "c1", "caption": "Car falls through the map #gta6 #a #b #c #d #e #f"})  # an edit by hand is checked again
    lines = call(app, "/api/state")[1]["clips"][0]["money"]["instagram"]
    assert next(line for line in lines if line["rule"] == "hashtags")["level"] == "fail"

    assert call(app, "/api/settings", {"monetize": {"music": "hold", "calm_open": 10, "strict": False, "mute_profanity": False}})[0] == 200
    got = call(app, "/api/state")[1]
    assert (got["settings"]["monetize"]["music"], got["settings"]["monetize"]["calm_open"], got["settings"]["monetize"]["strict"]) == ("hold", 10, False)
    assert got["clips"][0]["unfit"] == {}  # no longer held back from YouTube
    status, body, _ = call(app, "/api/settings", {"monetize": {"music": "mute"}})
    assert status == 400 and "note or hold" in body["error"]
