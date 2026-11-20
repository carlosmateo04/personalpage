"""The TikTok connector, against a local stand-in built from TikTok's documentation."""

import hashlib
import json
from urllib.parse import parse_qs, urlparse

import pytest

from clipper import metadata, settings
from clipper.connectors import ConnectorError, tiktok
from standins import FakeTikTok, serve


@pytest.fixture
def tt(monkeypatch):
    FakeTikTok.reset()
    server, url = serve(FakeTikTok)
    monkeypatch.setattr(tiktok.TikTok, "API", url)
    monkeypatch.setattr(tiktok.TikTok, "PAUSE", 0)
    yield FakeTikTok
    server.shutdown()


def sign_in(cfg) -> tiktok.TikTok:
    conn = tiktok.TikTok(cfg)
    conn.save_app("aw123", "tt-secret")
    url, kept = conn.auth_url(conn.redirect_uri(8765), "state-1")
    FakeTikTok.challenge = parse_qs(urlparse(url).query)["code_challenge"][0]
    conn.finish(conn.redirect_uri(8765), {"code": "good-code"}, kept)
    return conn


@pytest.fixture
def clip(cfg, tmp_path):
    video = tmp_path / "c1.mp4"
    video.write_bytes(bytes(range(256)) * 2000)
    meta = metadata.compose(cfg, "c1", "RawPlay", cfg.section("moments"), "Tank against a six-star chase", "Tank time")
    return {"id": "c1", "meta": meta, "start": 10.0, "end": 100.0, "cover": 31.5}, video


def calls(method: str, path: str) -> list[dict]:
    return [c for c in FakeTikTok.calls if c["method"] == method and c["path"] == path]


def test_signing_in_uses_the_hex_challenge_and_keeps_the_newest_refresh_token(cfg, tt):
    conn = tiktok.TikTok(cfg)
    assert conn.redirect_uri(8765) == "http://127.0.0.1:8765/oauth/tiktok/"
    with pytest.raises(ConnectorError, match="client secret as well"):
        conn.save_app("aw123")  # TikTok wants the secret even from a desktop app
    conn.save_app("aw123", "tt-secret")
    url, kept = conn.auth_url(conn.redirect_uri(8765), "state-1")
    asked = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    assert url.startswith("https://www.tiktok.com/v2/auth/authorize/?") and asked["client_key"] == "aw123" and asked["response_type"] == "code"
    assert asked["scope"] == "user.info.basic,video.upload" and asked["redirect_uri"] == "http://127.0.0.1:8765/oauth/tiktok/"
    assert asked["code_challenge"] == hashlib.sha256(kept["verifier"].encode()).hexdigest() and asked["code_challenge_method"] == "S256"

    tt.challenge = asked["code_challenge"]
    account = conn.finish(conn.redirect_uri(8765), {"code": "good-code", "state": "state-1"}, kept)
    assert account["name"] == "Leonida Clips" and account["id"] == "open-1" and account["mode"] == "draft"
    assert tt.form(calls("POST", "/v2/oauth/token/")[0]["body"]) == {
        "client_key": "aw123", "client_secret": "tt-secret", "code": "good-code", "grant_type": "authorization_code",
        "redirect_uri": "http://127.0.0.1:8765/oauth/tiktok/", "code_verifier": kept["verifier"]}
    assert settings.get_secret(cfg.data_root, "tiktok_refresh_token") == "rft-1"

    later = tiktok.TikTok(cfg)  # a later run signs in again with the refresh token, and gets a new one each time
    assert later.access() == "act-2" and settings.get_secret(cfg.data_root, "tiktok_refresh_token") == "rft-2"
    assert tiktok.TikTok(cfg).access() == "act-3" and settings.get_secret(cfg.data_root, "tiktok_refresh_token") == "rft-3"
    settings.set_secret(cfg.data_root, "tiktok_refresh_token", "rft-1")  # an old one no longer works
    with pytest.raises(ConnectorError, match="expired or was withdrawn") as caught:
        tiktok.TikTok(cfg).access()
    assert caught.value.reconnect and "expired" in tiktok.TikTok(cfg).account()["problem"]


@pytest.mark.parametrize("change, query, said", [
    ({}, {"error": "access_denied"}, "cancelled"),
    ({}, {"error": "invalid_request", "error_description": "Redirect_uri is not matched"}, "Redirect_uri is not matched"),
    ({}, {}, "no authorization code"),
    ({}, {"code": "old-code"}, r"TikTok refused the sign-in \(invalid_grant: Authorization code is expired.\)"),
    ({"scope": "user.info.basic"}, {"code": "good-code"}, r"permission to send videos \(video.upload\) was not given"),
])
def test_sign_ins_that_do_not_complete(cfg, tt, change, query, said):
    conn = tiktok.TikTok(cfg)
    conn.save_app("aw123", "tt-secret")
    url, kept = conn.auth_url(conn.redirect_uri(8765), "s")
    tt.challenge = parse_qs(urlparse(url).query)["code_challenge"][0]
    for name, value in change.items():
        setattr(tt, name, value)
    with pytest.raises(ConnectorError, match=said):
        conn.finish(conn.redirect_uri(8765), query, kept)
    assert not conn.connected()


def test_a_draft_lands_in_the_tiktok_inbox_without_a_caption(cfg, tt, clip):
    rec, video = clip
    conn = sign_in(cfg)
    assert not conn.native_schedule
    sent = conn.send(rec, video, None)
    assert (sent.id, sent.state, sent.link) == ("v_pub_file~v2.123", "draft", "") and "paste the caption and post it" in sent.note
    opened = calls("POST", "/v2/post/publish/inbox/video/init/")[0]
    assert json.loads(opened["body"]) == {"source_info": {"source": "FILE_UPLOAD", "video_size": 512000, "chunk_size": 512000, "total_chunk_count": 1}}
    assert opened["headers"]["Content-Type"] == "application/json; charset=UTF-8" and opened["headers"]["Authorization"] == "Bearer act-1"
    put = calls("PUT", "/upload/video/")[0]
    assert put["query"] == {"upload_id": "1", "upload_token": "tok"}  # the upload address is used exactly as given
    assert put["headers"]["Content-Range"] == "bytes 0-511999/512000" and put["headers"]["Content-Type"] == "video/mp4" and put["headers"]["Content-Length"] == "512000"
    assert tt.stored == video.read_bytes()
    assert not calls("POST", "/v2/post/publish/creator_info/query/")


def test_private_mode_posts_for_the_owners_eyes_only(cfg, tt, clip):
    rec, video = clip
    cfg.publish.tiktok_direct = "private"
    tt.scope = "user.info.basic,video.publish"
    conn = sign_in(cfg)
    assert conn.scopes() == ["user.info.basic", "video.publish"]
    sent = conn.send(rec, video, None)
    assert sent.state == "processing" and "posted as private" in sent.note
    assert json.loads(calls("POST", "/v2/post/publish/video/init/")[0]["body"]) == {
        # duets and stitches are switched off on the account itself, so the post does not ask for them
        "post_info": {"title": rec["meta"]["tiktok"]["title"], "privacy_level": "SELF_ONLY", "disable_duet": True, "disable_comment": False,
                      "disable_stitch": True, "brand_content_toggle": False, "brand_organic_toggle": False, "is_aigc": False, "video_cover_timestamp_ms": 31500},
        "source_info": {"source": "FILE_UPLOAD", "video_size": 512000, "chunk_size": 512000, "total_chunk_count": 1}}

    cfg.publish.tiktok_privacy_level = "PUBLIC_TO_EVERYONE"  # private mode stays private whatever the audience setting says
    tiktok.TikTok(cfg).send(rec, video, None)
    assert json.loads(calls("POST", "/v2/post/publish/video/init/")[-1]["body"])["post_info"]["privacy_level"] == "SELF_ONLY"
    cfg.publish.tiktok_privacy_level = ""
    tt.longest = 60
    with pytest.raises(ConnectorError, match="the clip is 90 seconds; this TikTok account may post up to 60"):
        tiktok.TikTok(cfg).send(rec, video, None)
    tt.longest = 600
    long_caption = json.loads(json.dumps(rec))
    long_caption["meta"]["tiktok"]["title"] = "x" * 2201
    with pytest.raises(ConnectorError, match="over 2,200 characters"):
        tiktok.TikTok(cfg).send(long_caption, video, None)


def test_a_large_file_goes_up_in_chunks_in_order_and_a_failed_chunk_is_sent_again(cfg, tt, clip, monkeypatch):
    # TikTok's own example: 50,000,123 bytes in chunks of 10,000,000 is five chunks, the last one taking the odd 123 bytes.
    assert tiktok.plan_chunks(50_000_123, 10_000_000) == (50_000_123, 1, [(0, 50_000_122)])  # up to 64 MB goes in one piece
    size, count, spans = tiktok.plan_chunks(70_000_123, 10_000_000)
    assert (size, count) == (10_000_000, 7) and spans[0] == (0, 9_999_999) and spans[-1] == (60_000_000, 70_000_122) and len(spans) == 7
    assert tiktok.plan_chunks(4_194_304, 10_000_000) == (4_194_304, 1, [(0, 4_194_303)])

    rec, video = clip
    conn = sign_in(cfg)
    monkeypatch.setattr(tiktok, "plan_chunks", lambda size, chunk: (200_000, 2, [(0, 199_999), (200_000, 511_999)]))
    tt.put_failures = 1  # the first attempt at the first chunk fails inside TikTok
    assert conn.send(rec, video, None).state == "draft" and tt.stored == video.read_bytes()
    assert [c["headers"]["Content-Range"] for c in calls("PUT", "/upload/video/")] == [
        "bytes 0-199999/512000", "bytes 0-199999/512000", "bytes 200000-511999/512000"]
    tt.put_failures = 9
    with pytest.raises(ConnectorError, match="kept failing") as caught:
        conn.send(rec, video, None)
    assert caught.value.retry


@pytest.mark.parametrize("failure, said, retry, reconnect, wait", [
    (("spam_risk_too_many_posts", 403), "daily limit of posts", True, False, 21600),
    (("rate_limit_exceeded", 429), "slow down", True, False, 180),
    (("scope_not_authorized", 401), "connect TikTok again", False, True, 0),
    (("unaudited_client_can_only_post_to_private_accounts", 403), "has not audited this app, so it only lets it post privately.*post this clip by hand, or use the posting service", False, False, 0),
    (("spam_risk_user_banned_from_posting", 403), "not letting this account post", False, False, 0),
    (("internal_error", 500), "problem of its own", True, False, 0),
    (("something_new", 400), r"refused while starting the upload \(something_new: something_new happened\)", False, False, 0),
])
def test_what_tiktok_refuses_says_what_to_do(cfg, tt, clip, failure, said, retry, reconnect, wait):
    rec, video = clip
    conn = sign_in(cfg)
    tt.fail = {"init": failure}
    with pytest.raises(ConnectorError, match=said) as caught:
        conn.send(rec, video, None)
    assert (caught.value.retry, caught.value.reconnect, caught.value.wait) == (retry, reconnect, wait) and not calls("PUT", "/upload/video/")


def test_a_stale_access_token_is_renewed_once(cfg, tt, clip):
    rec, video = clip
    conn = sign_in(cfg)
    conn._token = ("stale", 9e12)
    assert conn.send(rec, video, None).state == "draft"
    assert settings.get_secret(cfg.data_root, "tiktok_refresh_token") == "rft-2"  # renewed, and the new refresh token kept


@pytest.mark.parametrize("answer, expected", [
    ({"status": "PROCESSING_UPLOAD"}, {"state": "processing"}),
    ({"status": "SEND_TO_USER_INBOX"}, {"state": "draft"}),
    ({"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": []}, {"state": "posted"}),
    ({"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": [7301]}, {"state": "posted", "post_id": "7301"}),
    ({"status": "FAILED", "fail_reason": "duration_check_failed"}, {"state": "failed", "error": "TikTok could not take the video: the video is too long or too short for TikTok"}),
    ({"status": "FAILED", "fail_reason": "new_reason"}, {"state": "failed", "error": "TikTok could not take the video: new_reason"}),
])
def test_what_became_of_a_post(cfg, tt, answer, expected):
    conn = sign_in(cfg)
    tt.post_status = answer
    assert conn.status({"id": "v_pub_file~v2.123"}) == expected
    assert json.loads(calls("POST", "/v2/post/publish/status/fetch/")[0]["body"]) == {"publish_id": "v_pub_file~v2.123"}


def test_disconnecting_withdraws_the_access_at_tiktok(cfg, tt):
    conn = sign_in(cfg)
    conn.disconnect()
    assert tt.form(calls("POST", "/v2/oauth/revoke/")[0]["body"]) == {"client_key": "aw123", "client_secret": "tt-secret", "token": "act-1"}
    assert not conn.connected() and not settings.get_secret(cfg.data_root, "tiktok_refresh_token") and conn.app()["ready"]


def public(cfg, tt) -> tiktok.TikTok:
    """Signed in for posting with a caption, to an account that offers every audience and has nothing switched off."""
    cfg.publish.tiktok_direct = "public"
    tt.scope = "user.info.basic,video.publish"
    tt.options = ["PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY"]
    tt.off = {"comment_disabled": False, "duet_disabled": False, "stitch_disabled": False}
    return sign_in(cfg)


def post_info() -> dict:
    return json.loads(calls("POST", "/v2/post/publish/video/init/")[-1]["body"])["post_info"]


def test_public_mode_posts_with_the_caption_for_everyone_by_default(cfg, tt, clip):
    rec, video = clip
    conn = public(cfg, tt)
    assert conn.scopes() == ["user.info.basic", "video.publish"] and conn.account()["mode"] == "public"
    sent = conn.send(rec, video, None)
    assert sent.state == "processing" and sent.note == "posted for: everyone"
    assert post_info() == {"title": rec["meta"]["tiktok"]["title"], "privacy_level": "PUBLIC_TO_EVERYONE", "disable_comment": False, "disable_duet": False,
                           "disable_stitch": False, "brand_content_toggle": False, "brand_organic_toggle": False, "is_aigc": False, "video_cover_timestamp_ms": 31500}
    # What the account offers is asked before every post, as TikTok requires, and kept for the app to offer the same choices.
    assert len(calls("POST", "/v2/post/publish/creator_info/query/")) == 1
    assert conn.account()["audiences"] == ["PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY"] and conn.account()["longest"] == 600


def test_a_clips_own_tiktok_choices_win_over_the_settings(cfg, tt, clip):
    rec, video = clip
    conn = public(cfg, tt)
    cfg.publish.tiktok_duet = False  # the setting: no duets
    assert conn.options() == {"audience": "PUBLIC_TO_EVERYONE", "comments": True, "duet": False, "stitch": True, "own_brand": False, "paid": False}
    rec["tiktok"] = {"audience": "MUTUAL_FOLLOW_FRIENDS", "comments": False, "duet": True, "own_brand": True}
    assert conn.send(rec, video, None).note == "posted for: friends"
    sent = post_info()
    assert (sent["privacy_level"], sent["disable_comment"], sent["disable_duet"], sent["disable_stitch"]) == ("MUTUAL_FOLLOW_FRIENDS", True, False, False)
    assert sent["brand_organic_toggle"] is True and sent["brand_content_toggle"] is False

    tt.off = {"comment_disabled": True, "duet_disabled": True, "stitch_disabled": False}  # switched off on the account since
    rec["tiktok"] = {"comments": True, "duet": True, "stitch": True}
    conn.send(rec, video, None)
    assert (post_info()["disable_comment"], post_info()["disable_duet"], post_info()["disable_stitch"]) == (True, True, False)


def test_public_mode_says_plainly_what_tiktok_will_not_take(cfg, tt, clip):
    rec, video = clip
    conn = public(cfg, tt)
    tt.options = ["FOLLOWER_OF_CREATOR", "MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY"]  # a private account has no "everyone"
    with pytest.raises(ConnectorError, match=r'does not offer "Everyone" for this account \(it offers: Followers, Friends, Only me\)'):
        conn.send(rec, video, None)
    assert not calls("POST", "/v2/post/publish/video/init/")  # nothing was started

    tt.options = ["PUBLIC_TO_EVERYONE", "SELF_ONLY"]
    tt.fail = {"init": ("unaudited_client_can_only_post_to_private_accounts", 403)}  # what an app without the audit is told
    with pytest.raises(ConnectorError, match="TikTok has not audited this app") as caught:
        conn.send(rec, video, None)
    assert not caught.value.retry and not caught.value.reconnect  # trying again cannot help; the clip says what to do instead

    rec["tiktok"] = {"audience": "SELF_ONLY", "paid": True}
    with pytest.raises(ConnectorError, match="paid partnership cannot be posted as"):
        conn.send(rec, video, None)


def test_changing_between_draft_and_posting_asks_to_connect_again(cfg, tt, clip):
    rec, video = clip
    conn = sign_in(cfg)  # connected for drafts
    cfg.publish.tiktok_direct = "public"
    with pytest.raises(ConnectorError, match="TikTok was connected for drafts, and the setting has changed since: connect TikTok again") as caught:
        tiktok.TikTok(cfg).send(rec, video, None)
    assert caught.value.reconnect and not calls("POST", "/v2/post/publish/video/init/")
    cfg.publish.tiktok_direct = "draft"
    assert conn.send(rec, video, None).state == "draft"


def test_five_waiting_drafts_is_a_wait_not_a_failure(cfg, tt, clip):
    rec, video = clip
    conn = sign_in(cfg)
    tt.fail = {"init": ("spam_risk_too_many_pending_share", 403)}
    with pytest.raises(ConnectorError, match="five drafts are already waiting") as caught:
        conn.send(rec, video, None)
    assert caught.value.retry and caught.value.wait == 3600
