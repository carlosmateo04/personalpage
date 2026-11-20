"""The Instagram and Facebook connectors, against a local stand-in built from Meta's documentation (Graph API v27.0)."""

from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from clipper import metadata, settings
from clipper.connectors import ConnectorError, meta
from standins import FakeMeta, serve


@pytest.fixture
def graph(monkeypatch):
    FakeMeta.reset()
    server, url = serve(FakeMeta)
    monkeypatch.setattr(meta.Meta, "GRAPH", f"{url}/v27.0")
    monkeypatch.setattr(meta.Meta, "POLL", 0)
    yield FakeMeta
    server.shutdown()


def sign_in(cfg):
    fb = meta.Facebook(cfg)
    fb.save_app("app-1", "app-secret")
    fb.finish(fb.redirect_uri(8765), {"code": "good-code"}, {})
    return fb, meta.Instagram(cfg)


@pytest.fixture
def clip(cfg, tmp_path):
    video = tmp_path / "c1.mp4"
    video.write_bytes(bytes(range(256)) * 2000)
    made = metadata.compose(cfg, "c1", "RawPlay", cfg.section("moments"), "Tank against a six-star chase", "Tank time")
    return {"id": "c1", "meta": made, "start": 10.0, "end": 98.0, "cover": 12.0}, video


def calls(method: str, path: str) -> list[dict]:
    return [c for c in FakeMeta.calls if c["method"] == method and c["path"] == path]


def test_one_sign_in_connects_the_page_and_the_instagram_account_linked_to_it(cfg, graph):
    fb, ig = meta.Facebook(cfg), meta.Instagram(cfg)
    assert fb.redirect_uri(8765) == ig.redirect_uri(8765) == "http://localhost:8765/oauth/meta" and not fb.connected() and not ig.connected()
    fb.save_app("app-1", "app-secret")
    url, kept = fb.auth_url(fb.redirect_uri(8765), "state-1")
    asked = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    assert url.startswith("https://www.facebook.com/v27.0/dialog/oauth?") and asked == {
        "client_id": "app-1", "redirect_uri": "http://localhost:8765/oauth/meta", "state": "state-1", "response_type": "code",
        "scope": "pages_show_list,pages_read_engagement,pages_manage_posts,instagram_basic,instagram_content_publish"}

    account = fb.finish(fb.redirect_uri(8765), {"code": "good-code", "state": "state-1"}, kept)
    exchange, long_lived = calls("GET", "/v27.0/oauth/access_token")
    assert exchange["query"] == {"client_id": "app-1", "redirect_uri": "http://localhost:8765/oauth/meta", "client_secret": "app-secret", "code": "good-code"}
    assert long_lived["query"] == {"grant_type": "fb_exchange_token", "client_id": "app-1", "client_secret": "app-secret", "fb_exchange_token": "short-1"}
    # The Page with an Instagram account is chosen over the one without.
    assert account["name"] == "Leonida Clips" and account["id"] == "111" and [p["name"] for p in account["pages"]] == ["Other Page", "Leonida Clips"]
    assert ig.account()["name"] == "@leonidaclips" and ig.account()["id"] == "178" and ig.connected()
    expires = datetime.fromisoformat(account["expires"].replace("Z", "+00:00"))
    assert timedelta(days=59) < expires - datetime.now(timezone.utc) < timedelta(days=61)  # the sign-in lasts about 60 days
    assert settings.get_secret(cfg.data_root, "meta_user_token") == "long-1" and settings.get_secret(cfg.data_root, "meta_page_token") == "page-1"
    kept_settings = (cfg.data_root / "settings.json").read_text(encoding="utf-8")
    assert "long-1" not in kept_settings and "page-1" not in kept_settings and "app-secret" not in kept_settings

    assert fb.choose("222")["name"] == "Other Page"  # switching Page: this one has no Instagram account
    assert settings.get_secret(cfg.data_root, "meta_page_token") == "page-2" and not meta.Instagram(cfg).connected()


def test_a_token_pasted_from_the_graph_api_explorer_connects_too_and_a_configuration_replaces_the_scope_list(cfg, graph):
    fb = meta.Facebook(cfg)
    fb.save_app("app-1", "app-secret")
    assert fb.finish("", {"token": " explorer-token "}, {})["name"] == "Leonida Clips"
    assert calls("GET", "/v27.0/oauth/access_token")[0]["query"]["fb_exchange_token"] == "explorer-token"
    fb.remember(config_id="cfg-77")  # Facebook Login for Business
    asked = parse_qs(urlparse(fb.auth_url(fb.redirect_uri(8765), "s")[0]).query)
    assert asked["config_id"] == ["cfg-77"] and "scope" not in asked


@pytest.mark.parametrize("setup, query, said", [
    ({}, {"error": "access_denied", "error_reason": "user_denied"}, "cancelled"),
    ({}, {}, "no authorization code"),
    ({}, {"code": "bad"}, r"Meta refused while signing in \(100: Invalid verification code format.\)"),
    ({"pages": []}, {"code": "good-code"}, "manages no Page"),
])
def test_sign_ins_that_do_not_complete(cfg, graph, setup, query, said):
    for name, value in setup.items():
        setattr(graph, name, value)
    fb = meta.Facebook(cfg)
    with pytest.raises(ConnectorError, match="ID and secret first"):
        fb.auth_url("x", "s")
    fb.save_app("app-1", "app-secret")
    with pytest.raises(ConnectorError, match=said):
        fb.finish(fb.redirect_uri(8765), query, {})
    assert not fb.connected() and not settings.get_secret(cfg.data_root, "meta_user_token")


def test_a_clip_of_up_to_90_seconds_becomes_a_page_reel(cfg, graph, clip):
    rec, video = clip
    fb, _ = sign_in(cfg)
    assert fb.native_schedule
    sent = fb.send(rec, video, None)
    assert (sent.id, sent.state, sent.link) == ("900", "processing", "https://www.facebook.com/reel/900")
    start, finish = calls("POST", "/v27.0/111/video_reels")
    assert start["body"] == b'{"upload_phase": "start", "access_token": "page-1"}'
    upload = calls("POST", "/rupload/video-upload/v27.0/900")[0]["headers"]
    assert upload["Authorization"] == "OAuth page-1" and upload["offset"] == "0" and upload["file_size"] == "512000"
    assert graph.stored == video.read_bytes()
    assert finish["query"] == {"access_token": "page-1", "video_id": "900", "upload_phase": "finish", "video_state": "PUBLISHED",
                               "description": rec["meta"]["facebook"]["title"], "title": "Tank against a six-star chase"}

    when = datetime.now(timezone.utc) + timedelta(hours=5)  # a future time is handed to Facebook
    assert fb.send(rec, video, when).state == "scheduled"
    scheduled = calls("POST", "/v27.0/111/video_reels")[-1]["query"]
    assert scheduled["video_state"] == "SCHEDULED" and scheduled["scheduled_publish_time"] == str(int(when.timestamp()))
    soon = datetime.now(timezone.utc) + timedelta(minutes=5)  # closer than Facebook can schedule: it goes out now
    assert fb.send(rec, video, soon).state == "processing" and calls("POST", "/v27.0/111/video_reels")[-1]["query"]["video_state"] == "PUBLISHED"


def test_a_longer_clip_goes_up_as_a_page_video(cfg, graph, clip):
    rec, video = clip
    rec["end"] = 105.0  # 95 seconds: past what a Page reel may be
    fb, _ = sign_in(cfg)
    sent = fb.send(rec, video, None)
    assert (sent.id, sent.state) == ("901", "processing") and sent.note == "posted as a Page video: Page reels stop at 90 seconds"
    session = calls("POST", "/v27.0/app-1/uploads")[0]["query"]
    assert session == {"file_name": "c1.mp4", "file_length": "512000", "file_type": "video/mp4", "access_token": "long-1"}
    upload = calls("POST", "/v27.0/upload:abc")[0]["headers"]
    assert upload["Authorization"] == "OAuth long-1" and upload["file_offset"] == "0" and "file_size" not in upload and graph.stored == video.read_bytes()
    published = graph.form(calls("POST", "/v27.0/111/videos")[0]["body"])
    assert published == {"access_token": "page-1", "title": "Tank against a six-star chase", "description": rec["meta"]["facebook"]["title"],
                         "fbuploader_video_file_chunk": "handle-1"}
    when = datetime.now(timezone.utc) + timedelta(days=2)
    assert fb.send(rec, video, when).state == "scheduled"
    later = graph.form(calls("POST", "/v27.0/111/videos")[1]["body"])
    assert later["published"] == "false" and later["scheduled_publish_time"] == str(int(when.timestamp()))


def test_an_interrupted_upload_resumes_from_what_meta_has(cfg, graph, clip):
    rec, video = clip
    fb, ig = sign_in(cfg)
    graph.drop_after = 200_000
    assert fb.send(rec, video, None).id == "900" and graph.stored == video.read_bytes()
    first, second = calls("POST", "/rupload/video-upload/v27.0/900")
    assert (first["headers"]["offset"], second["headers"]["offset"]) == ("0", "200000")  # from Facebook's bytes_transfered

    graph.drop_after = 300_000
    assert ig.send(rec, video, None).state == "posted" and graph.stored == video.read_bytes()
    assert [c["headers"]["offset"] for c in calls("POST", "/rupload/ig-api-upload/v27.0/c-1")] == ["0", "300000"]


def test_an_instagram_reel_is_uploaded_processed_and_then_published(cfg, graph, clip):
    rec, video = clip
    _, ig = sign_in(cfg)
    assert not ig.native_schedule
    graph.container = ["IN_PROGRESS", "FINISHED"]
    sent = ig.send(rec, video, None)
    assert (sent.id, sent.state, sent.link) == ("m-1", "posted", "https://www.instagram.com/reel/Cabc/")
    container = calls("POST", "/v27.0/178/media")[0]["query"]
    assert container == {"media_type": "REELS", "upload_type": "resumable", "caption": rec["meta"]["instagram"]["title"], "thumb_offset": "12000",
                         "access_token": "long-1"}
    upload = calls("POST", "/rupload/ig-api-upload/v27.0/c-1")[0]["headers"]
    assert upload["Authorization"] == "OAuth long-1" and upload["offset"] == "0" and upload["file_size"] == "512000"
    assert graph.stored == video.read_bytes()
    assert calls("POST", "/v27.0/178/media_publish")[0]["query"] == {"creation_id": "c-1", "access_token": "long-1"}
    assert len([c for c in calls("GET", "/v27.0/c-1") if "status_code" in c["query"]["fields"]]) == 2  # looked twice

    assert ig.status({"id": "m-1", "link": sent.link}) == {"state": "posted", "views": 77}
    graph.insights = False  # without the insights permission there are simply no numbers
    assert ig.status({"id": "m-1"}) == {"state": "posted", "link": "https://www.instagram.com/reel/Cabc/"}


def test_a_reel_instagram_is_slow_with_is_published_when_asked_about_later(cfg, graph, clip):
    rec, video = clip
    _, ig = sign_in(cfg)
    graph.container = ["IN_PROGRESS"] * 6
    sent = ig.send(rec, video, None)
    assert (sent.id, sent.state) == ("container:c-1", "processing") and not calls("POST", "/v27.0/178/media_publish")
    assert ig.status({"id": sent.id}) == {"state": "processing"}  # the sixth look: still not ready
    assert ig.status({"id": sent.id}) == {"state": "posted", "id": "m-1", "link": "https://www.instagram.com/reel/Cabc/"}
    assert len(calls("POST", "/v27.0/178/media_publish")) == 1

    graph.container = ["ERROR"]
    with pytest.raises(ConnectorError, match=r"could not process the video \(2207026\)"):
        ig.send(rec, video, None)
    graph.container = ["EXPIRED"]
    with pytest.raises(ConnectorError, match="expire") as caught:
        ig.status({"id": "container:c-1"})
    assert caught.value.retry


def test_what_instagram_cannot_take(cfg, graph, clip):
    rec, video = clip
    fb, ig = sign_in(cfg)
    assert ig.unfit(rec, 88.0, 1) == "" and ig.unfit(rec, 2.0, 1) == ig.unfit(rec, 1000.0, 1) == "Instagram reels run from 3 seconds to 15 minutes"
    long_caption = {**rec, "meta": {**rec["meta"], "instagram": {"title": "x" * 2201}}}
    with pytest.raises(ConnectorError, match="over 2,200 characters"):
        ig.send(long_caption, video, None)
    fb.choose("222")  # a Page with no Instagram account
    with pytest.raises(ConnectorError, match="Instagram is not connected|no Instagram professional account"):
        meta.Instagram(cfg).send(rec, video, None)


@pytest.mark.parametrize("failure, said, retry, reconnect, wait", [
    ((190, "Error validating access token: Session has expired."), "expired or was withdrawn", False, True, 0),
    ((4, "Application request limit reached"), r"slow down \(Application request limit reached\)", True, False, 3600),
    ((200, "The user hasn't authorized the application to perform this action"), "a permission is missing", False, True, 0),
    ((368, "The action attempted has been deemed abusive or is otherwise disallowed"), "blocked this action for now", True, False, 21600),
    ((2, "Service temporarily unavailable"), "had a problem while starting the reel", True, False, 0),
    ((1363128, "Video duration must be between 3 and 90 seconds"), r"refused while starting the reel \(1363128: Video duration", False, False, 0),
])
def test_what_meta_refuses_says_what_to_do(cfg, graph, clip, failure, said, retry, reconnect, wait):
    rec, video = clip
    fb, _ = sign_in(cfg)
    graph.fail = {"start": failure}
    with pytest.raises(ConnectorError, match=said) as caught:
        fb.send(rec, video, None)
    assert (caught.value.retry, caught.value.reconnect, caught.value.wait) == (retry, reconnect, wait)
    if failure[0] == 190:
        assert meta.Facebook(cfg).account()["problem"] == "the sign-in has expired or was withdrawn"


def test_a_sign_in_past_its_sixty_days_asks_to_connect_again(cfg, graph, clip):
    rec, video = clip
    fb, ig = sign_in(cfg)
    fb.remember(expires="2026-01-01T00:00:00Z")
    for conn in (meta.Facebook(cfg), meta.Instagram(cfg)):
        with pytest.raises(ConnectorError, match="has run out") as caught:
            conn.send(rec, video, None)
        assert caught.value.reconnect
    assert not calls("POST", "/v27.0/111/video_reels")


@pytest.mark.parametrize("status, expected", [
    ({"video_status": "ready", "publishing_phase": {"status": "complete", "publish_status": "published"}}, {"link": "https://www.facebook.com/reel/900/", "state": "posted"}),
    ({"video_status": "processing", "publishing_phase": {"status": "not_started", "publish_status": "scheduled"}}, {"link": "https://www.facebook.com/reel/900/", "state": "scheduled"}),
    ({"video_status": "processing"}, {"link": "https://www.facebook.com/reel/900/", "state": "processing"}),
    ({"video_status": "error", "processing_phase": {"status": "error", "error": {"message": "The video is too short"}}},
     {"link": "https://www.facebook.com/reel/900/", "state": "failed", "error": "Facebook could not publish the video: The video is too short"}),
])
def test_what_became_of_a_facebook_video(cfg, graph, status, expected):
    fb, _ = sign_in(cfg)
    graph.video_status = status
    assert fb.status({"id": "900"}) == expected


def test_disconnecting_withdraws_the_permissions_and_forgets_both_accounts(cfg, graph):
    fb, ig = sign_in(cfg)
    ig.disconnect()
    assert calls("DELETE", "/v27.0/me/permissions")[0]["query"] == {"access_token": "long-1"}
    assert not meta.Facebook(cfg).connected() and not meta.Instagram(cfg).connected()
    assert not settings.get_secret(cfg.data_root, "meta_user_token") and not settings.get_secret(cfg.data_root, "meta_page_token")
    assert meta.Facebook(cfg).app() == {"client_id": "app-1", "has_secret": True, "ready": True}
