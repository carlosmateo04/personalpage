"""Followers, earnings and comments, against the stand-ins for Google and Meta; and the comment inbox."""

import json
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from clipper import audience, connectors, money, progress, settings, store
from clipper.connectors import ConnectorError, meta, youtube
from standins import GOOD_KEY, FakeGoogle, FakeMeta, serve
from test_business import clip

EXTRA = " https://www.googleapis.com/auth/youtube.force-ssl https://www.googleapis.com/auth/yt-analytics-monetary.readonly"


@pytest.fixture
def online(cfg, monkeypatch):
    """YouTube and Facebook/Instagram connected, comments and earnings switched on and granted."""
    servers = []
    for fake in (FakeGoogle, FakeMeta):
        fake.reset()
        server, fake.url = serve(fake)
        servers.append(server)
    monkeypatch.setattr(youtube.YouTube, "TOKEN", f"{FakeGoogle.url}/token")
    monkeypatch.setattr(youtube.YouTube, "API", f"{FakeGoogle.url}/youtube/v3")
    monkeypatch.setattr(youtube.YouTube, "ANALYTICS", f"{FakeGoogle.url}/v2/reports")
    monkeypatch.setattr(meta.Meta, "GRAPH", f"{FakeMeta.url}/v27.0")
    cfg.publish.platforms = ["tiktok", "youtube", "instagram", "facebook"]
    cfg.audience.enabled, cfg.progress.earnings = True, True
    FakeGoogle.scope += EXTRA
    FakeMeta.pages = [{"id": "111", "name": "Leonida Clips", "access_token": "page-1", "instagram_business_account": {"id": "178"}}]
    yt = connectors.get("youtube", cfg)
    yt.save_app("client-123", "GOCSPX-secret")
    url, kept = yt.auth_url(yt.redirect_uri(8765), "s")
    asked = parse_qs(urlparse(url).query)
    FakeGoogle.challenge = asked["code_challenge"][0]
    yt.finish(yt.redirect_uri(8765), {"code": "good-code"}, kept)
    fb = connectors.get("facebook", cfg)
    fb.save_app("app-1", "app-secret")
    fb.finish(fb.redirect_uri(8765), {"code": "good-code"}, {})
    yield cfg, asked["scope"][0]
    for server in servers:
        server.shutdown()


def test_the_further_permissions_are_only_asked_for_when_switched_on(cfg, online):
    cfg, asked = online
    assert asked.endswith("youtube.force-ssl https://www.googleapis.com/auth/yt-analytics-monetary.readonly")
    url, _ = connectors.get("facebook", cfg).auth_url("http://localhost:8765/oauth/meta", "s")
    assert "instagram_manage_comments" in url and "pages_manage_engagement" in url
    cfg.audience.enabled, cfg.audience.credit_comment, cfg.progress.earnings = False, False, False
    assert len(connectors.get("youtube", cfg).scopes()) == 2  # uploading and reading the channel, as before
    assert "instagram_manage_comments" not in connectors.get("facebook", cfg).auth_url("http://localhost:8765/oauth/meta", "s")[0]


def test_followers_are_read_where_the_platform_gives_them(cfg, online, ledger):
    cfg, _ = online
    assert connectors.get("youtube", cfg).followers() == 640 and connectors.get("instagram", cfg).followers() == 4321 and connectors.get("facebook", cfg).followers() == 987
    assert connectors.get("tiktok", cfg).followers() is None  # TikTok does not tell an app
    cfg.progress.followers = {"tiktok": 1200}
    found = progress.followers(cfg, fresh=True)
    assert found == {"tiktok": {"count": 1200, "from": "typed in"}, "youtube": {"count": 640, "from": "platform"},
                     "instagram": {"count": 4321, "from": "platform"}, "facebook": {"count": 987, "from": "platform"}}
    before = len(FakeGoogle.calls)
    assert progress.followers(cfg)["youtube"]["count"] == 640 and len(FakeGoogle.calls) == before  # not asked again within the hour
    FakeGoogle.channel["statistics"] = {"hiddenSubscriberCount": True}
    assert connectors.get("youtube", cfg).followers() is None  # a channel that hides its count


def test_youtubes_earnings_go_into_the_money_record_month_by_month(cfg, online):
    cfg, _ = online
    assert progress.earnings(cfg, datetime(2026, 11, 20, tzinfo=timezone.utc)) == {"2026-10": 3.5, "2026-11": 12.25}
    asked = next(c for c in FakeGoogle.calls if c["path"] == "/v2/reports")["query"]
    assert asked == {"ids": "channel==MINE", "startDate": "2026-01-01", "endDate": "2026-11-20", "metrics": "estimatedRevenue", "dimensions": "month"}
    entries = {e["id"]: e for e in money.entries(cfg)}
    assert entries["youtube:2026-11"]["amount"] == 12.25 and entries["youtube:2026-10"]["source"] == "YouTube (its estimate)"
    FakeGoogle.earned = [["2026-11", 20.0]]
    progress.earnings(cfg, datetime(2026, 11, 20, tzinfo=timezone.utc))
    assert {e["id"]: e for e in money.entries(cfg)}["youtube:2026-11"]["amount"] == 20.0  # the month's figure is replaced, not added to
    cfg.progress.earnings = False
    assert progress.earnings(cfg) == {}
    settings.set_account(cfg.data_root, "youtube", {**settings.account(cfg.data_root, "youtube"), "scopes": []})  # connected before earnings were switched on
    with pytest.raises(ConnectorError, match="was not given the permission to read earnings") as caught:
        connectors.get("youtube", cfg).earnings("2026-01-01", "2026-11-20")
    assert caught.value.reconnect


def posted(ledger):
    rec = clip(ledger, "c1", views={"youtube": 10, "instagram": 5, "facebook": 3, "tiktok": 9})
    rec["posts"]["youtube"]["id"], rec["posts"]["instagram"]["id"], rec["posts"]["facebook"]["id"] = "vid123", "m-1", "900"
    rec["meta"] = {"credit": "Credit: RawPlay"}
    rec["origin"] = "https://example.com/v"
    return rec


def test_the_inbox_gathers_unanswered_comments_and_claude_drafts_the_replies(cfg, online, ledger, claude_api):
    cfg, _ = online
    posted(ledger)
    claude_api.replies.append({"replies": [{"id": "youtube:yc-1", "reply": "Parked behind the army base, north gate."}, {"id": "instagram:ic-1", "reply": ""},
                                           {"id": "facebook:fc-1", "reply": "The second heist."}]})
    found = audience.refresh(cfg, ledger)
    assert [(i["key"], i["text"], i["draft"]) for i in found["items"]] == [
        ("facebook:fc-1", "Which mission is that?", "The second heist."), ("instagram:ic-1", "Where is this spot?", ""),
        ("youtube:yc-1", "How did you get the tank?", "Parked behind the army base, north gate.")]  # answered ones and the channel's own are left out
    sent = claude_api.seen[0]["body"]["messages"][0]["content"][0]["text"]
    assert "comment: How did you get the tank?" in sent and "clip: Clip c1" in sent
    assert "UCviewer" not in sent and "555" not in json.dumps(store.read(cfg, "audience"))  # who wrote is neither sent to Claude nor kept

    audience.answer(cfg, ledger, "youtube:yc-1", "  Parked behind the army base.  ")
    assert FakeGoogle.said == [{"snippet": {"parentId": "yc-1", "textOriginal": "Parked behind the army base."}}]
    audience.answer(cfg, ledger, "instagram:ic-1", "Vespucci beach, by the pier.")
    audience.answer(cfg, ledger, "facebook:fc-1", "The second heist.")
    assert FakeMeta.said == [("/ic-1/replies", "Vespucci beach, by the pier."), ("/fc-1/comments", "The second heist.")]
    with pytest.raises(KeyError):
        audience.answer(cfg, ledger, "youtube:yc-1", "again")  # once
    with pytest.raises(ConnectorError, match="Write the reply first"):
        audience.answer(cfg, ledger, "youtube:yc-9", " ")
    assert audience.refresh(cfg, ledger, draft=False)["items"] == []  # answered here: not offered again, though the platform still lists them


def test_a_comment_can_be_left_unanswered_and_problems_are_said_not_hidden(cfg, online, ledger, claude_api):
    cfg, _ = online
    posted(ledger)
    claude_api.replies.append({"replies": []})
    assert len(audience.refresh(cfg, ledger)["items"]) == 3
    audience.skip(cfg, "facebook:fc-1")
    assert [i["key"] for i in audience.refresh(cfg, ledger, draft=False)["items"]] == ["instagram:ic-1", "youtube:yc-1"]
    settings.set_account(cfg.data_root, "youtube", {**settings.account(cfg.data_root, "youtube"), "scopes": []})
    found = audience.refresh(cfg, ledger, draft=False)
    assert found["problems"] == ["YouTube: the YouTube sign-in was not given the permission for comments: connect YouTube again"]
    cfg.audience.enabled = False
    with pytest.raises(ConnectorError, match="Comments are switched off"):
        audience.refresh(cfg, ledger)


def test_the_credit_is_commented_once_on_a_youtube_post(cfg, online, ledger):
    cfg, _ = online
    rec = posted(ledger)
    assert not audience.credit(cfg, rec)  # off unless switched on
    cfg.audience.credit_comment = True
    assert audience.credit(cfg, rec, lambda m: None)
    assert FakeGoogle.said == [{"snippet": {"videoId": "vid123", "topLevelComment": {"snippet": {"textOriginal": "Credit: RawPlay. Original: https://example.com/v"}}}}]
    assert not audience.credit(cfg, rec) and len(FakeGoogle.said) == 1  # once
