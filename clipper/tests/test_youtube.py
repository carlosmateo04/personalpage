"""The YouTube connector, against a local stand-in for Google's sign-in and the YouTube Data API.

The stand-in follows the documented protocol (resumable upload sessions, 308 with a
Range header, errors with a reason); what the real service does beyond that is not
tested here.
"""

import json
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from clipper import metadata, settings
from clipper.connectors import ConnectorError, youtube
from standins import FakeGoogle, serve

WHEN = datetime(2026, 11, 21, 16, 30, tzinfo=timezone.utc)


@pytest.fixture
def google(monkeypatch):
    FakeGoogle.reset()
    server, url = serve(FakeGoogle)
    monkeypatch.setattr(youtube.YouTube, "TOKEN", f"{url}/token")
    monkeypatch.setattr(youtube.YouTube, "REVOKE", f"{url}/revoke")
    monkeypatch.setattr(youtube.YouTube, "API", f"{url}/youtube/v3")
    monkeypatch.setattr(youtube.YouTube, "UPLOAD", f"{url}/upload/youtube/v3/videos")
    monkeypatch.setattr(youtube.YouTube, "BACKOFF", 0)
    yield FakeGoogle
    server.shutdown()


def sign_in(cfg) -> youtube.YouTube:
    yt = youtube.YouTube(cfg)
    yt.save_app("client-123.apps.googleusercontent.com", "GOCSPX-secret")
    url, kept = yt.auth_url("http://127.0.0.1:8765", "state-1")
    FakeGoogle.challenge = parse_qs(urlparse(url).query)["code_challenge"][0]
    yt.finish("http://127.0.0.1:8765", {"code": "good-code", "state": "state-1"}, kept)
    return yt


@pytest.fixture
def clip(cfg, tmp_path):
    video = tmp_path / "c1.mp4"
    video.write_bytes(bytes(range(256)) * 4000)  # about a megabyte, every byte position recognisable
    meta = metadata.compose(cfg, "c1", "RawPlay", cfg.section("glitches"), "Police car launches off the pier", "It just took off",
                            "https://example.com/v", summary="A police car hits a ramp.", tags=["gta 6 police chase"])
    return {"id": "c1", "meta": meta}, video


def calls(method: str, path: str) -> list[dict]:
    return [c for c in FakeGoogle.calls if c["method"] == method and c["path"] == path]


def test_signing_in_stores_the_long_lived_sign_in_and_shows_the_channel(cfg, google):
    yt = youtube.YouTube(cfg)
    assert yt.app() == {"client_id": "", "has_secret": False, "ready": False} and not yt.connected() and yt.redirect_uri(8765) == "http://127.0.0.1:8765"
    with pytest.raises(ConnectorError, match="client ID first"):
        yt.auth_url("http://127.0.0.1:8765", "s")
    with pytest.raises(ConnectorError, match="client ID first"):
        yt.save_app("  ")
    yt.save_app(" client-123.apps.googleusercontent.com ", " GOCSPX-secret ")
    assert yt.app() == {"client_id": "client-123.apps.googleusercontent.com", "has_secret": True, "ready": True}

    url, kept = yt.auth_url("http://127.0.0.1:8765", "state-1")
    asked = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert asked["client_id"] == "client-123.apps.googleusercontent.com" and asked["redirect_uri"] == "http://127.0.0.1:8765"
    assert asked["response_type"] == "code" and asked["state"] == "state-1" and asked["code_challenge_method"] == "S256"
    assert asked["scope"] == "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"
    assert 43 <= len(kept["verifier"]) <= 128 and asked["code_challenge"] != kept["verifier"]

    google.challenge = asked["code_challenge"]
    account = yt.finish("http://127.0.0.1:8765", {"code": "good-code", "state": "state-1"}, kept)
    assert account["name"] == "Leonida Clips" and account["id"] == "UC123" and account["handle"] == "@leonidaclips" and account["can_read"]
    sent = google.form(calls("POST", "/token")[0]["body"])
    assert sent == {"client_id": "client-123.apps.googleusercontent.com", "client_secret": "GOCSPX-secret", "code": "good-code",
                    "code_verifier": kept["verifier"], "grant_type": "authorization_code", "redirect_uri": "http://127.0.0.1:8765"}
    assert calls("GET", "/youtube/v3/channels")[0]["query"] == {"part": "snippet", "mine": "true"}
    assert youtube.YouTube(cfg).connected() and youtube.YouTube(cfg).account()["name"] == "Leonida Clips"
    # The sign-in and the app's secret are kept as secrets, never in the settings file the page can read.
    assert settings.get_secret(cfg.data_root, "youtube_refresh_token") == "rt-good"
    kept_settings = (cfg.data_root / "settings.json").read_text(encoding="utf-8")
    assert "rt-good" not in kept_settings and "GOCSPX" not in kept_settings and "client-123" in kept_settings


def test_a_playlist_in_the_settings_asks_for_the_permission_to_add_to_it(cfg):
    cfg.sections[0].youtube_playlist_id = "PL123"
    yt = youtube.YouTube(cfg)
    yt.save_app("client-123")
    asked = parse_qs(urlparse(yt.auth_url("http://127.0.0.1:8765", "s")[0]).query)["scope"][0].split()
    assert asked[-1] == "https://www.googleapis.com/auth/youtube" and len(asked) == 3


@pytest.mark.parametrize("change, query, said", [
    ({}, {"error": "access_denied"}, "cancelled"),
    ({}, {"error": "admin_policy_enforced"}, "admin_policy_enforced"),
    ({}, {"state": "state-1"}, "no authorization code"),
    ({}, {"code": "wrong-code"}, "Google refused the sign-in \\(invalid_grant: Malformed auth code.\\)"),
    ({"scope": "https://www.googleapis.com/auth/youtube.readonly"}, {"code": "good-code"}, "permission to upload videos was not given"),
    ({"refresh_token": ""}, {"code": "good-code"}, 'must be of the type "Desktop app"'),
    ({"channel": None}, {"code": "good-code"}, "has no YouTube channel yet"),
])
def test_sign_ins_that_do_not_complete_leave_nothing_connected(cfg, google, change, query, said):
    yt = youtube.YouTube(cfg)
    yt.save_app("client-123")
    url, kept = yt.auth_url("http://127.0.0.1:8765", "state-1")
    google.challenge = parse_qs(urlparse(url).query)["code_challenge"][0]
    for name, value in change.items():
        setattr(google, name, value)
    with pytest.raises(ConnectorError, match=said):
        yt.finish("http://127.0.0.1:8765", query, kept)
    assert not yt.connected() and not settings.get_secret(cfg.data_root, "youtube_refresh_token")


def test_an_upload_opens_a_session_and_sends_the_file(cfg, google, clip):
    rec, video = clip
    yt = sign_in(cfg)
    sent = yt.send(rec, video, None)
    assert (sent.id, sent.state, sent.link, sent.note) == ("vid123", "processing", "https://www.youtube.com/watch?v=vid123", "")
    opened = calls("POST", "/upload/youtube/v3/videos")[0]
    assert opened["query"] == {"uploadType": "resumable", "part": "snippet,status,paidProductPlacementDetails", "notifySubscribers": "true"}
    assert opened["headers"]["X-Upload-Content-Length"] == "1024000" and opened["headers"]["X-Upload-Content-Type"] == "video/mp4"
    assert opened["headers"]["Content-Type"] == "application/json; charset=UTF-8" and opened["headers"]["Authorization"] == "Bearer at-1"
    assert json.loads(opened["body"]) == {
        "snippet": {"title": "Police car launches off the pier | GTA 6 Glitch #shorts", "description": rec["meta"]["youtube"]["description"],
                    "tags": rec["meta"]["youtube"]["tags"], "categoryId": "20"},
        "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False, "containsSyntheticMedia": False},
        "paidProductPlacementDetails": {"hasPaidProductPlacement": False},
    }
    put = calls("PUT", "/upload/session/1")
    assert len(put) == 1 and put[0]["headers"]["Content-Length"] == "1024000" and put[0]["headers"]["Content-Type"] == "video/mp4"
    assert "Content-Range" not in put[0]["headers"] and google.stored == video.read_bytes()


def test_a_future_time_is_handed_to_youtube_as_a_scheduled_private_upload(cfg, google, clip):
    rec, video = clip
    cfg.publish.youtube_notify = False
    yt = sign_in(cfg)
    assert yt.native_schedule
    sent = yt.send(rec, video, WHEN)
    opened = calls("POST", "/upload/youtube/v3/videos")[0]
    assert json.loads(opened["body"])["status"] == {"privacyStatus": "private", "publishAt": "2026-11-21T16:30:00Z",
                                                     "selfDeclaredMadeForKids": False, "containsSyntheticMedia": False}
    assert sent.state == "scheduled" and opened["query"]["notifySubscribers"] == "false"

    cfg.publish.youtube_privacy = "unlisted"  # nothing to schedule: it is never made public
    sent = youtube.YouTube(cfg).send(rec, video, WHEN)
    assert json.loads(calls("POST", "/upload/youtube/v3/videos")[1]["body"])["status"]["privacyStatus"] == "unlisted" and sent.state == "processing"


def test_an_interrupted_upload_is_picked_up_where_it_stopped(cfg, google, clip):
    rec, video = clip
    yt = sign_in(cfg)
    google.drop_after = 300_000  # the connection dies after 300,000 bytes
    sent = yt.send(rec, video, None)
    assert sent.id == "vid123" and google.stored == video.read_bytes()  # the whole file, every byte in place
    first, asked, resumed = calls("PUT", "/upload/session/1")
    assert "Content-Range" not in first["headers"]
    assert asked["headers"]["Content-Range"] == "bytes */1024000" and asked["headers"]["Content-Length"] == "0"
    assert resumed["headers"]["Content-Range"] == "bytes 300000-1023999/1024000" and resumed["headers"]["Content-Length"] == "724000"

    google.calls.clear()
    google.fail = {"put": (503, "backendError")}  # a server error before anything is stored: start again from byte 0
    assert youtube.YouTube(cfg).send(rec, video, None).id == "vid123" and google.stored == video.read_bytes()
    assert [c["headers"].get("Content-Range") for c in calls("PUT", "/upload/session/1")] == [None, "bytes */1024000", None]


def test_an_upload_that_cannot_get_through_is_left_for_later(cfg, google, clip, monkeypatch):
    rec, video = clip
    yt = sign_in(cfg)
    monkeypatch.setattr(youtube.YouTube, "RESUMES", 3)
    monkeypatch.setattr(google, "refuse", lambda self, step: step == "put" and (self.reply(503, {"error": {"code": 503, "errors": [{"reason": "backendError"}]}}) or True))
    with pytest.raises(ConnectorError, match="kept being interrupted") as caught:
        yt.send(rec, video, None)
    assert caught.value.retry and not caught.value.reconnect

    monkeypatch.setattr(google, "refuse", lambda self, step: step == "put" and (self.reply(404, {"error": {"code": 404}}) or True))
    with pytest.raises(ConnectorError, match="session expired") as caught:
        yt.send(rec, video, None)
    assert caught.value.retry


@pytest.mark.parametrize("failure, said, retry, reconnect, wait", [
    ((403, "quotaExceeded"), "used up its YouTube quota for today", True, False, 14400),
    ((400, "uploadLimitExceeded"), "daily upload limit", True, False, 21600),
    ((403, "insufficientPermissions"), "connect YouTube again", False, True, 0),
    ((401, "youtubeSignupRequired"), "no YouTube channel", False, False, 0),
    ((400, "invalidTitle"), "did not accept the title", False, False, 0),
    ((500, "backendError"), "had a problem while starting the upload", True, False, 0),
    ((400, "somethingNew"), r"refused while starting the upload \(400 somethingNew: somethingNew happened\)", False, False, 0),
])
def test_what_youtube_refuses_says_what_to_do(cfg, google, clip, failure, said, retry, reconnect, wait):
    rec, video = clip
    yt = sign_in(cfg)
    google.fail = {"open": failure}
    with pytest.raises(ConnectorError, match=said) as caught:
        yt.send(rec, video, None)
    assert (caught.value.retry, caught.value.reconnect, caught.value.wait) == (retry, reconnect, wait)
    assert not calls("PUT", "/upload/session/1")  # nothing was uploaded


def test_a_stale_access_token_is_renewed_and_a_withdrawn_sign_in_asks_to_reconnect(cfg, google, clip):
    rec, video = clip
    sign_in(cfg)
    yt = youtube.YouTube(cfg)  # a later run: only the long-lived sign-in is on disk
    yt._token = ("stale", 9e12)
    assert yt.send(rec, video, None).id == "vid123"  # refused once with 401, renewed, sent
    assert google.form(calls("POST", "/token")[-1]["body"]) == {"client_id": "client-123.apps.googleusercontent.com", "client_secret": "GOCSPX-secret",
                                                               "grant_type": "refresh_token", "refresh_token": "rt-good"}
    google.refresh_token = "rt-other"  # the user withdrew the access at Google
    with pytest.raises(ConnectorError, match="expired or was withdrawn") as caught:
        youtube.YouTube(cfg).send(rec, video, None)
    assert caught.value.reconnect and "expired or was withdrawn" in youtube.YouTube(cfg).account()["problem"]


def test_text_is_never_cut_to_fit_and_what_youtube_changed_is_reported(cfg, google, clip):
    rec, video = clip
    yt = sign_in(cfg)
    for field, value, said in (("title", "t" * 101, "between 1 and 100 characters"), ("title", " ", "between 1 and 100"),
                               ("description", "é" * 2501, "over 5,000 bytes"), ("title", "A <b>bold</b> claim", "does not allow < or >")):
        changed = json.loads(json.dumps(rec))
        changed["meta"]["youtube"][field] = value
        with pytest.raises(ConnectorError, match=said):
            yt.send(changed, video, None)
    assert not calls("POST", "/upload/youtube/v3/videos")

    google.made_private = True  # an account setting, or a restricted project, forces private
    sent = yt.send(rec, video, None)
    assert sent.note == "YouTube made this video private although public was asked for; check it in YouTube Studio"

    google.made_private = False
    rec["meta"]["playlist"] = "PL123"
    assert yt.send(rec, video, None).note == ""
    assert json.loads(calls("POST", "/youtube/v3/playlistItems")[0]["body"]) == {
        "snippet": {"playlistId": "PL123", "resourceId": {"kind": "youtube#video", "videoId": "vid123"}}}
    google.fail = {"playlist": (403, "playlistItemsNotAccessible")}
    assert "not added to the playlist" in yt.send(rec, video, None).note  # the video is up all the same


@pytest.mark.parametrize("video, expected", [
    ({"status": {"uploadStatus": "processed", "privacyStatus": "public"}, "statistics": {"viewCount": "1234"}}, {"views": 1234, "state": "posted"}),
    ({"status": {"uploadStatus": "uploaded", "privacyStatus": "public"}, "statistics": {}}, {"views": 0, "state": "processing"}),
    ({"status": {"uploadStatus": "processed", "privacyStatus": "private", "publishAt": "2026-11-21T16:30:00Z"}, "statistics": {"viewCount": "0"}},
     {"views": 0, "state": "scheduled"}),
    ({"status": {"uploadStatus": "processed", "privacyStatus": "private"}, "statistics": {"viewCount": "3"}},
     {"views": 3, "state": "posted", "note": "the video is private on YouTube; check it in YouTube Studio"}),
    ({"status": {"uploadStatus": "rejected", "rejectionReason": "claim"}}, {"state": "failed", "error": "YouTube rejected the video: blocked by a content owner's claim"}),
    ({"status": {"uploadStatus": "failed", "failureReason": "codec"}}, {"state": "failed", "error": "YouTube could not process the video (codec)"}),
    (None, {"state": "failed", "error": "the video is no longer on YouTube"}),
])
def test_what_became_of_an_upload(cfg, google, video, expected):
    yt = sign_in(cfg)
    google.video = video
    assert yt.status({"id": "vid123"}) == expected
    assert calls("GET", "/youtube/v3/videos")[0]["query"] == {"part": "status,statistics", "id": "vid123"}


def test_disconnecting_withdraws_the_access_at_google_and_keeps_the_app_details(cfg, google):
    yt = sign_in(cfg)
    yt.disconnect()
    assert calls("POST", "/revoke")[0]["query"] == {"token": "rt-good"}
    assert not yt.connected() and yt.account() == {} and not settings.get_secret(cfg.data_root, "youtube_refresh_token")
    assert yt.app() == {"client_id": "client-123.apps.googleusercontent.com", "has_secret": True, "ready": True}  # connecting again is one click
    with pytest.raises(ConnectorError, match="not connected") as caught:
        yt.access()
    assert caught.value.reconnect
