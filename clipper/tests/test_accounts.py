"""The app's Accounts tab: connecting each platform from the page, posting through the connections, and the Claude settings."""

import json
from urllib.parse import parse_qs, urlparse

import pytest

from clipper import settings
from clipper.connectors import meta, tiktok, youtube
from clipper.state import Ledger
from standins import GOOD_KEY, FakeGoogle, FakeMeta, FakeTikTok, serve
from test_ui import TOKEN, app, call  # noqa: F401 - `app` is the running-app fixture


@pytest.fixture
def platforms(app, monkeypatch):  # noqa: F811
    """The running app, with stand-ins for Google, TikTok and Meta behind the connectors."""
    app.port = int(app.url.rsplit(":", 1)[1])
    servers = []
    for fake in (FakeGoogle, FakeTikTok, FakeMeta):
        fake.reset()
        server, url = serve(fake)
        servers.append(server)
        fake.url = url
    monkeypatch.setattr(youtube.YouTube, "TOKEN", f"{FakeGoogle.url}/token")
    monkeypatch.setattr(youtube.YouTube, "REVOKE", f"{FakeGoogle.url}/revoke")
    monkeypatch.setattr(youtube.YouTube, "API", f"{FakeGoogle.url}/youtube/v3")
    monkeypatch.setattr(youtube.YouTube, "UPLOAD", f"{FakeGoogle.url}/upload/youtube/v3/videos")
    monkeypatch.setattr(youtube.YouTube, "BACKOFF", 0)
    monkeypatch.setattr(tiktok.TikTok, "API", FakeTikTok.url)
    monkeypatch.setattr(meta.Meta, "GRAPH", f"{FakeMeta.url}/v27.0")
    monkeypatch.setattr(meta.Meta, "POLL", 0)
    yield app
    for server in servers:
        server.shutdown()


def platform(app, name):  # noqa: F811
    return next(p for p in call(app, "/api/state")[1]["posting"]["platforms"] if p["name"] == name)


def start(app, body):  # noqa: F811
    """Press Connect; returns the sign-in page's address as a dict of its parameters."""
    status, answer, _ = call(app, "/api/connect-account", body)
    assert status == 200, answer
    return {k: v[0] for k, v in parse_qs(urlparse(answer["url"]).query).items()}


def connect_youtube(app):  # noqa: F811
    asked = start(app, {"platform": "youtube", "client_id": "client-123", "client_secret": "GOCSPX-secret"})
    FakeGoogle.challenge = asked["code_challenge"]
    return call(app, f"/?code=good-code&state={asked['state']}", token="")  # the browser coming back from Google carries no token


def test_youtube_is_connected_from_the_page_and_the_sign_in_comes_back_to_the_app(platforms):
    app = platforms  # noqa: F811
    before = platform(app, "youtube")
    assert before["route"] == "" and before["account"] == "" and before["redirect"] == f"http://127.0.0.1:{app.port}" and before["schedules"]
    asked = start(app, {"platform": "youtube", "client_id": "client-123", "client_secret": "GOCSPX-secret"})
    assert asked["redirect_uri"] == f"http://127.0.0.1:{app.port}" and asked["client_id"] == "client-123" and len(asked["state"]) >= 24

    assert call(app, "/?code=good-code&state=not-ours", token="")[0] == 400  # a sign-in this app did not start
    FakeGoogle.challenge = asked["code_challenge"]
    status, page, _ = call(app, f"/?code=good-code&state={asked['state']}", token="")
    assert status == 200 and b"YouTube is connected: Leonida Clips" in page and b"close this tab" in page
    status, page, _ = call(app, f"/?code=good-code&state={asked['state']}", token="")
    assert status == 400 and b"no longer waiting" in page  # the same sign-in cannot be used twice

    after = platform(app, "youtube")
    assert after["account"] == "Leonida Clips" and after["route"] == "direct" and after["app"] == {"client_id": "client-123", "has_secret": True, "ready": True}
    everything = json.dumps(call(app, "/api/state")[1])
    assert "rt-good" not in everything and "GOCSPX" not in everything  # no sign-in and no secret ever goes back to the page

    asked = start(app, {"platform": "youtube"})  # connecting again needs no retyping
    status, page, _ = call(app, f"/?error=access_denied&state={asked['state']}", token="")
    assert status == 400 and b"cancelled" in page and platform(app, "youtube")["account"] == "Leonida Clips"

    assert call(app, "/api/disconnect-account", {"platform": "youtube"})[0] == 200
    assert platform(app, "youtube")["route"] == "" and any(c["path"] == "/revoke" for c in FakeGoogle.calls)
    assert call(app, "/api/connect-account", {"platform": "myspace"})[0] == 400


def test_tiktok_and_meta_come_back_on_their_own_addresses(platforms):
    app = platforms  # noqa: F811
    asked = start(app, {"platform": "tiktok", "client_id": "aw123", "client_secret": "tt-secret"})
    assert asked["redirect_uri"] == f"http://127.0.0.1:{app.port}/oauth/tiktok/"
    FakeTikTok.challenge = asked["code_challenge"]
    status, page, _ = call(app, f"/oauth/tiktok/?code=good-code&state={asked['state']}", token="")
    assert status == 200 and b"TikTok is connected: Leonida Clips" in page and platform(app, "tiktok")["route"] == "direct"

    asked = start(app, {"platform": "instagram", "client_id": "app-1", "client_secret": "app-secret", "config_id": ""})
    assert asked["redirect_uri"] == f"http://localhost:{app.port}/oauth/meta"
    status, page, _ = call(app, f"/oauth/meta?code=good-code&state={asked['state']}", token="")
    assert status == 200 and b"Facebook and Instagram are connected" in page
    assert (platform(app, "facebook")["account"], platform(app, "instagram")["account"]) == ("Leonida Clips", "@leonidaclips")
    posting = call(app, "/api/state")[1]["posting"]
    assert posting["page"] == "111" and [p["name"] for p in posting["pages"]] == ["Other Page", "Leonida Clips"]
    assert call(app, "/api/choose-page", {"page": "222"})[0] == 200  # the other Page has no Instagram account
    assert platform(app, "facebook")["account"] == "Other Page" and platform(app, "instagram")["route"] == ""

    call(app, "/api/disconnect-account", {"platform": "facebook"})
    status, answer, _ = call(app, "/api/connect-account", {"platform": "facebook", "token": "explorer-token"})  # the paste-a-token way in
    assert status == 200 and answer["connected"] == "Other Page"  # the Page chosen before is kept across a new sign-in
    call(app, "/api/choose-page", {"page": "111"})
    assert platform(app, "facebook")["account"] == "Leonida Clips" and platform(app, "instagram")["route"] == "direct"
    assert call(app, "/api/connect-account", {"platform": "facebook", "token": "nonsense"})[0] == 400


def test_a_clip_is_posted_through_the_connection_and_its_progress_shows_on_the_card(platforms):
    app = platforms  # noqa: F811
    assert connect_youtube(app)[0] == 200
    status, _, _ = call(app, "/api/post", {"id": "c1", "when": "now", "platforms": ["youtube"]})
    assert status == 200 and FakeGoogle.stored == (app.data / "out" / "20261120-000000" / "c1.mp4").read_bytes()
    clip = call(app, "/api/state")[1]["clips"][0]
    assert clip["state"] == "queued" and clip["posts"]["youtube"] == {"state": "processing", "link": "https://www.youtube.com/watch?v=vid123",
                                                                   "views": None, "note": "", "via": "direct"}
    assert call(app, "/api/state")[1]["posting"]["log"] == ["  c1 uploaded to YouTube"]
    assert call(app, "/api/deliver", {})[1] == {"ok": True, "sent": 0, "failed": 0, "waiting": 0, "busy": False}  # which also asks YouTube about it
    clip = call(app, "/api/state")[1]["clips"][0]
    assert clip["state"] == "posted" and clip["posts"]["youtube"]["state"] == "posted" and clip["posts"]["youtube"]["views"] == 1234
    assert call(app, "/api/post", {"id": "c1", "when": "now"})[0] == 400  # not twice


def test_a_post_that_fails_can_be_tried_again_or_taken_back(platforms):
    app = platforms  # noqa: F811
    connect_youtube(app)
    FakeGoogle.fail = {"open": (400, "invalidTitle")}
    status, answer, _ = call(app, "/api/post", {"id": "c1", "when": "now", "platforms": ["youtube"]})
    assert status == 400 and answer["error"] == "youtube: YouTube did not accept the title"
    clip = call(app, "/api/state")[1]["clips"][0]
    assert clip["state"] == "failed" and clip["posts"]["youtube"]["note"] == "YouTube did not accept the title"

    assert call(app, "/api/retry", {"id": "c1"})[0] == 200  # the Try again button
    assert call(app, "/api/state")[1]["clips"][0]["posts"]["youtube"]["state"] == "processing"
    assert call(app, "/api/take-back", {"id": "c1"})[0] == 400  # YouTube has it now: nothing to take back from here

    ledger = Ledger(app.data / "state" / "ledger.json")
    ledger.clips["c1"]["posts"]["youtube"] = {"state": "waiting", "via": "direct", "due": "2099-01-01T00:00:00+00:00", "tries": 0}
    ledger.clips["c1"]["status"] = "scheduled"
    ledger.save()
    assert call(app, "/api/take-back", {"id": "c1"})[0] == 200
    assert call(app, "/api/state")[1]["clips"][0]["state"] == "ready"  # back among the ready clips


def test_the_next_free_time_is_handed_to_youtube_at_once(platforms):
    app = platforms  # noqa: F811
    connect_youtube(app)
    assert call(app, "/api/post-all", {})[1] == {"ok": True, "count": 1}
    opened = next(c for c in FakeGoogle.calls if c["method"] == "POST" and c["path"] == "/upload/youtube/v3/videos")
    status = json.loads(opened["body"])["status"]
    assert status["privacyStatus"] == "private" and status["publishAt"].endswith("Z")  # YouTube holds it until its time
    clip = call(app, "/api/state")[1]["clips"][0]
    assert clip["state"] == "queued" and clip["posts"]["youtube"]["state"] == "scheduled"


def test_claude_is_set_up_from_the_page(platforms, claude_api, monkeypatch):
    app = platforms  # noqa: F811
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    state = call(app, "/api/state")[1]["ai"]
    assert state["off"] == "no Claude API key is saved (add one in the app under Accounts)" and not state["has_key"]
    assert state["provider"] == "anthropic" and state["model"] == "claude-opus-5-5" and state["scout"] and state["edit"]
    assert state["models"][0] == {"id": "claude-opus-5-5", "label": "Claude Opus 5.5", "price": "$4 in, $20 out per million tokens"}
    assert state["usage"]["requests"] == 0

    status, answer, _ = call(app, "/api/ai-key", {"api_key": "sk-ant-wrong"})
    assert status == 400 and "did not accept" in answer["error"] and not settings.get_secret(app.data, "anthropic_api_key")
    assert call(app, "/api/ai-key", {"api_key": "  "})[0] == 400
    assert call(app, "/api/ai-key", {"api_key": f" {GOOD_KEY}\n"})[0] == 200
    state = call(app, "/api/state")[1]
    assert state["ai"]["has_key"] and state["ai"]["off"] == "" and GOOD_KEY not in json.dumps(state)  # the key never goes back to the page

    status, answer, _ = call(app, "/api/ai", {"direction": "Tanks and helicopters.", "scout": False, "model": "claude-sonnet-5-5", "test": True})
    assert status == 200 and answer["problem"] == ""  # saved, and Claude answers
    state = call(app, "/api/state")[1]["ai"]
    assert (state["direction"], state["scout"], state["model"]) == ("Tanks and helicopters.", False, "claude-sonnet-5-5")
    status, answer, _ = call(app, "/api/ai", {"direction": "x" * 700})
    assert status == 400 and "600 characters" in answer["error"] and call(app, "/api/state")[1]["ai"]["direction"] == "Tanks and helicopters."

    status, answer, _ = call(app, "/api/ai", {"provider": "bedrock", "aws_profile": "bedrock-sso", "aws_region": "us-east-1", "test": True})
    assert status == 200 and 'the profile "bedrock-sso" is missing or has expired' in answer["problem"] and "aws sso login --profile bedrock-sso" in answer["problem"]
    assert call(app, "/api/ai", {"provider": "openai"})[0] == 400
    call(app, "/api/ai", {"provider": "anthropic"})
    assert call(app, "/api/ai-key-remove", {})[0] == 200 and not call(app, "/api/state")[1]["ai"]["has_key"]
