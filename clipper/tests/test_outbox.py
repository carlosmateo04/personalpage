"""The outbox: queueing posts, sending what is due, trying again, and reading back what became of them."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from clipper import connectors, metadata, outbox, publish, settings
from clipper.connectors import Connector, ConnectorError, Sent

NOW = datetime(2026, 11, 20, 15, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(hours=4)


class Fake(Connector):
    """A platform that records what it is sent. `plan` holds what the next sends do: a Sent, or an error to raise."""

    sent: list = []
    got: dict = {}  # per platform: the file and the cut it was handed
    plan: list = []
    answers: dict = {}

    def account(self):
        return {"name": "Leonida Clips", "connected": "2026-11-20T00:00:00Z"} if self.name in type(self).on else {}

    on: set = set()

    def send(self, clip, video, when):
        type(self).sent.append((self.name, clip["id"], when))
        type(self).got[self.name] = (Path(video).name, clip["start"], clip["end"], clip.get("cover"))
        step = type(self).plan.pop(0) if type(self).plan else Sent(f"{self.name}-1", "scheduled" if when else "posted", f"https://{self.name}.example/1")
        if isinstance(step, Exception):
            raise step
        return step

    def status(self, post):
        return dict(type(self).answers.get(self.name, {}))


class Tube(Fake):
    name, label, native_schedule = "youtube", "YouTube", True


class Tok(Fake):
    name, label, native_schedule = "tiktok", "TikTok", False

    def unfit(self, clip, seconds, size):
        return "longer than TikTok allows" if seconds > 600 else ""


@pytest.fixture
def box(cfg, ledger, tmp_path, monkeypatch):
    """A finished clip and two connected platforms: one that schedules by itself, one that does not."""
    monkeypatch.setattr(connectors, "ALL", {"youtube": Tube, "tiktok": Tok})
    Fake.sent, Fake.plan, Fake.answers, Fake.on, Fake.got = [], [], {}, {"youtube", "tiktok"}, {}
    cfg.publish.platforms = ["youtube", "tiktok", "instagram"]
    video = tmp_path / "c1.mp4"
    video.write_bytes(b"video")
    meta = metadata.compose(cfg, "c1", "RawPlay", cfg.section("moments"), "Tank against a six-star chase", "Tank time")
    ledger.clips["c1"] = {"id": "c1", "status": "held", "file": str(video), "meta": meta, "start": 10.0, "end": 100.0, "slot": NOW.isoformat()}
    return cfg, ledger, ledger.clips["c1"]


def states(rec):
    return {name: post["state"] for name, post in rec["posts"].items()}


def test_which_way_each_platform_goes(box, monkeypatch):
    cfg, _, _ = box
    assert outbox.routes(cfg) == {"youtube": "direct", "tiktok": "direct", "instagram": ""}
    monkeypatch.setenv("UPLOAD_POST_API_KEY", "key")
    assert outbox.routes(cfg)["instagram"] == ""  # a key without a chosen profile is not a way in
    cfg.publish.profile = "gta6clips"
    assert outbox.routes(cfg) == {"youtube": "direct", "tiktok": "direct", "instagram": "service"}
    cfg.publish.prefer_service = ["tiktok"]  # the user would rather have the service post to TikTok
    assert outbox.routes(cfg) == {"youtube": "direct", "tiktok": "service", "instagram": "service"}
    Fake.on = set()
    assert outbox.routes(cfg) == dict.fromkeys(("youtube", "tiktok", "instagram"), "service")


def test_a_platform_that_schedules_is_handed_the_clip_at_once_and_the_other_waits_for_its_time(box):
    cfg, ledger, rec = box
    assert outbox.enqueue(cfg, ledger, rec, LATER, now=NOW) == {"queued": ["youtube", "tiktok"], "skipped": {"instagram": "no account is connected"}}
    assert states(rec) == {"youtube": "waiting", "tiktok": "waiting"} and rec["status"] == "scheduled" and rec["slot"] == LATER.isoformat()

    lines = []
    assert outbox.deliver(cfg, ledger, NOW, lines.append) == {"sent": 1, "failed": 0, "waiting": 1, "busy": False}
    assert Fake.sent == [("youtube", "c1", LATER)] and states(rec) == {"youtube": "scheduled", "tiktok": "waiting"}
    assert rec["posts"]["youtube"]["id"] == "youtube-1" and rec["links"] == {"youtube": "https://youtube.example/1"}
    assert lines == ["  c1 scheduled on YouTube"]

    assert outbox.deliver(cfg, ledger, LATER - timedelta(minutes=1), lines.append)["sent"] == 0  # not yet
    assert outbox.deliver(cfg, ledger, LATER + timedelta(seconds=30), lines.append)["sent"] == 1
    assert Fake.sent[-1] == ("tiktok", "c1", None) and states(rec) == {"youtube": "scheduled", "tiktok": "posted"}
    assert rec["status"] == "scheduled"  # YouTube still has it waiting

    Fake.answers = {"youtube": {"state": "posted", "views": 321}, "tiktok": {"views": 45}}
    assert outbox.refresh(cfg, ledger, LATER + timedelta(hours=1), lines.append) == 1
    assert rec["status"] == "posted" and rec["views"] == {"youtube": 321, "tiktok": 45}
    assert outbox.deliver(cfg, ledger, LATER + timedelta(hours=2), lines.append) == {"sent": 0, "failed": 0, "waiting": 0, "busy": False}
    assert len(Fake.sent) == 2  # nothing is ever sent twice

    saved = type(ledger)(ledger.path).clips["c1"]
    assert saved["status"] == "posted" and states(saved) == {"youtube": "posted", "tiktok": "posted"} and saved["publish"]["mode"] == "live"


def test_posting_now_and_what_cannot_be_queued(box):
    cfg, ledger, rec = box
    outbox.enqueue(cfg, ledger, rec, None, ["tiktok"], now=NOW)
    outbox.deliver(cfg, ledger, NOW, lambda m: None)
    assert Fake.sent == [("tiktok", "c1", None)] and states(rec) == {"tiktok": "posted"}
    assert outbox.enqueue(cfg, ledger, rec, None, now=NOW) == {"queued": ["youtube"], "skipped": {"tiktok": "already sent or queued", "instagram": "no account is connected"}}

    with pytest.raises(publish.PublishError, match=r"nothing could be queued \(youtube: already sent or queued; tiktok: already sent or queued; instagram: no account"):
        outbox.enqueue(cfg, ledger, rec, None, now=NOW)
    ledger.clips["c2"] = {**rec, "id": "c2", "status": "held", "end": 900.0}
    ledger.clips["c2"].pop("posts")
    assert outbox.enqueue(cfg, ledger, ledger.clips["c2"], None, now=NOW)["skipped"]["tiktok"] == "longer than TikTok allows"
    Path(rec["file"]).unlink()
    with pytest.raises(publish.PublishError, match="no longer on this computer"):
        outbox.enqueue(cfg, ledger, {**rec, "posts": {}}, None, now=NOW)


def test_a_failure_worth_retrying_is_tried_again_later_and_then_given_up(box, monkeypatch):
    cfg, ledger, rec = box
    outbox.enqueue(cfg, ledger, rec, None, ["tiktok"], now=NOW)
    Fake.plan = [ConnectorError("TikTok could not be reached", retry=True)] * 2 + [ConnectorError("quota used up", retry=True, wait=7200)] + [
        ConnectorError("TikTok could not be reached", retry=True)] * 2
    lines = []
    assert outbox.deliver(cfg, ledger, NOW, lines.append) == {"sent": 0, "failed": 0, "waiting": 1, "busy": False}
    post = rec["posts"]["tiktok"]
    assert post["state"] == "waiting" and post["tries"] == 1 and post["error"] == "TikTok could not be reached"
    assert post["next_try"] == (NOW + timedelta(minutes=5)).isoformat() and lines == ["  c1 to TikTok: TikTok could not be reached (will try again)"]

    outbox.deliver(cfg, ledger, NOW + timedelta(minutes=4), lines.append)
    assert len(Fake.sent) == 1  # too early
    outbox.deliver(cfg, ledger, NOW + timedelta(minutes=6), lines.append)
    assert post["tries"] == 2 and post["next_try"] == (NOW + timedelta(minutes=16)).isoformat()  # the wait doubles
    outbox.deliver(cfg, ledger, NOW + timedelta(minutes=17), lines.append)
    assert post["tries"] == 3 and post["next_try"] == (NOW + timedelta(minutes=17, hours=2)).isoformat()  # the platform said how long
    outbox.deliver(cfg, ledger, NOW + timedelta(hours=3), lines.append)
    outbox.deliver(cfg, ledger, NOW + timedelta(hours=5), lines.append)
    assert post["state"] == "failed" and post["tries"] == 5 and rec["status"] == "failed" and rec["error"] == "tiktok: TikTok could not be reached"

    assert outbox.retry(cfg, ledger, rec, NOW + timedelta(hours=6)) == ["tiktok"]  # the Retry button
    assert post["state"] == "waiting" and post["tries"] == 0 and "error" not in post and "error" not in rec
    assert outbox.deliver(cfg, ledger, NOW + timedelta(hours=6), lines.append)["sent"] == 1 and rec["status"] == "posted"


def test_failures_that_need_the_user_are_not_retried(box):
    cfg, ledger, rec = box
    outbox.enqueue(cfg, ledger, rec, None, now=NOW)
    Fake.plan = [ConnectorError("the YouTube sign-in has expired", reconnect=True), ConnectorError("the title is too long"), RuntimeError("a bug")]
    counts = outbox.deliver(cfg, ledger, NOW, lambda m: None)
    assert counts["failed"] == 2 and states(rec) == {"youtube": "failed", "tiktok": "failed"}
    assert rec["posts"]["youtube"]["reconnect"] is True and rec["posts"]["tiktok"]["error"] == "the title is too long"
    assert rec["error"] == "youtube: the YouTube sign-in has expired; tiktok: the title is too long"

    outbox.retry(cfg, ledger, rec, NOW)
    outbox.deliver(cfg, ledger, NOW, lambda m: None)  # the third planned step is a fault inside the connector
    assert rec["posts"]["youtube"]["error"] == "RuntimeError: a bug" and states(rec) == {"youtube": "failed", "tiktok": "posted"}
    assert rec["status"] == "posted"  # out on one platform; the failure stays visible on the other


def test_an_interrupted_send_is_not_repeated_blindly_and_two_deliveries_never_overlap(box):
    cfg, ledger, rec = box
    outbox.enqueue(cfg, ledger, rec, None, ["tiktok"], now=NOW)
    rec["posts"]["tiktok"].update(state="sending", started="2026-11-20T14:59:00Z")  # the computer went off mid-upload
    ledger.save()
    assert outbox.deliver(cfg, ledger, NOW + timedelta(minutes=30), lambda m: None)["sent"] == 0 and states(rec) == {"tiktok": "sending"}
    outbox.deliver(cfg, ledger, NOW + timedelta(hours=3), lambda m: None)
    assert rec["posts"]["tiktok"]["error"] == "sending was interrupted; look at the account before sending this again" and Fake.sent == []

    from clipper.state import Lock

    with Lock(cfg.path("state") / "deliver.lock", wait=0) as held:  # another delivery is under way
        assert held and outbox.deliver(cfg, ledger, NOW, lambda m: None) == {"sent": 0, "failed": 0, "waiting": 0, "busy": True}


def test_posts_not_handed_over_yet_can_be_taken_back(box):
    cfg, ledger, rec = box
    outbox.enqueue(cfg, ledger, rec, LATER, now=NOW)
    outbox.deliver(cfg, ledger, NOW, lambda m: None)  # YouTube has it; TikTok is waiting for its time
    assert outbox.take_back(cfg, ledger, rec) == ["tiktok"] and states(rec) == {"youtube": "scheduled"} and rec["status"] == "scheduled"
    assert outbox.take_back(cfg, ledger, rec) == []  # what a platform already holds cannot be taken back from here

    ledger.clips["c3"] = {**rec, "id": "c3", "status": "held"}
    other = ledger.clips["c3"]
    other.pop("posts")
    outbox.enqueue(cfg, ledger, other, LATER, ["tiktok"], now=NOW)
    assert outbox.take_back(cfg, ledger, other) == ["tiktok"] and other["status"] == "held" and "posts" not in other  # ready again


def test_the_posting_service_takes_its_platforms_in_one_upload(box, monkeypatch):
    cfg, ledger, rec = box
    calls = []

    class Service:
        def __init__(self, cfg):
            pass

        def publish(self, clip, video, when, platforms):
            calls.append((clip["id"], when, platforms))
            if "boom" in clip["id"]:
                raise publish.PublishError("upload failed: Invalid API key")
            return {"mode": "live", "request_id": "req-1", "job_id": "job-1"}

        def refresh(self, clip):
            assert clip["publish"] == {"request_id": "req-1", "job_id": "job-1"}
            return {"links": {"instagram": "https://instagram.example/p/1"}, "views": {"instagram": 77}}

    monkeypatch.setattr(publish, "UploadPost", Service)
    monkeypatch.setenv("UPLOAD_POST_API_KEY", "key")
    cfg.publish.profile, cfg.publish.prefer_service = "gta6clips", ["tiktok"]
    outbox.enqueue(cfg, ledger, rec, LATER, now=NOW)
    assert {name: post["via"] for name, post in rec["posts"].items()} == {"youtube": "direct", "tiktok": "service", "instagram": "service"}
    lines = []
    outbox.deliver(cfg, ledger, NOW, lines.append)
    assert calls == [("c1", LATER, ["tiktok", "instagram"])] and Fake.sent == [("youtube", "c1", LATER)]
    assert states(rec) == {"youtube": "scheduled", "tiktok": "scheduled", "instagram": "scheduled"} and rec["posts"]["instagram"]["id"] == "req-1"
    assert "  c1 handed to the posting service for tiktok, instagram" in lines

    outbox.refresh(cfg, ledger, LATER + timedelta(hours=1), lines.append)
    assert rec["posts"]["instagram"]["state"] == "posted" and rec["links"]["instagram"] == "https://instagram.example/p/1" and rec["views"]["instagram"] == 77
    assert rec["posts"]["tiktok"]["state"] == "scheduled"  # no link for it yet

    ledger.clips["boom"] = {**rec, "id": "boom", "status": "held"}
    ledger.clips["boom"].pop("posts")
    outbox.enqueue(cfg, ledger, ledger.clips["boom"], None, ["instagram"], now=NOW)
    outbox.deliver(cfg, ledger, NOW, lines.append)
    assert ledger.clips["boom"]["posts"]["instagram"]["error"] == "upload failed: Invalid API key" and ledger.clips["boom"]["status"] == "failed"


# --- each platform's own version of a clip ------------------------------------------

def test_each_platform_is_sent_its_own_version_of_the_clip(box, tmp_path):
    cfg, ledger, rec = box
    short = tmp_path / "c1.59s.mp4"
    short.write_bytes(b"short video")
    rec["cover"] = 40.0  # 40 seconds into the clip
    rec["cuts"] = {"youtube": {"start": 36.0, "end": 95.0, "file": str(short)}}
    outbox.enqueue(cfg, ledger, rec, None, now=NOW)
    outbox.deliver(cfg, ledger, NOW, lambda m: None)
    assert Fake.got["youtube"] == ("c1.59s.mp4", 36.0, 95.0, 14.0)  # the short version, with the cover moved along with the cut
    assert Fake.got["tiktok"] == ("c1.mp4", 10.0, 100.0, 40.0)  # the clip as it is
    assert rec["start"] == 10.0 and rec["file"].endswith("c1.mp4")  # the record itself is not changed by sending


def test_a_cover_outside_a_platforms_cut_is_left_to_the_platform(box, tmp_path):
    cfg, ledger, rec = box
    short = tmp_path / "c1.59s.mp4"
    short.write_bytes(b"short video")
    rec["cover"] = 5.0  # before the short version starts
    rec["cuts"] = {"youtube": {"start": 36.0, "end": 95.0, "file": str(short)}}
    outbox.enqueue(cfg, ledger, rec, None, ["youtube"], NOW)
    outbox.deliver(cfg, ledger, NOW, lambda m: None)
    assert Fake.got["youtube"] == ("c1.59s.mp4", 36.0, 95.0, None)


def test_a_platform_whose_length_rule_the_clip_does_not_meet_is_left_out_with_the_reason(box, tmp_path):
    cfg, ledger, rec = box
    rec["unfit"] = {"tiktok": "the clip is 45 seconds; tiktok is set to take 61 or more"}
    result = outbox.enqueue(cfg, ledger, rec, None, now=NOW)
    assert result["queued"] == ["youtube"] and result["skipped"]["tiktok"] == "the clip is 45 seconds; tiktok is set to take 61 or more"

    rec2 = dict(rec, id="c2", posts={}, unfit={}, cuts={"youtube": {"start": 36.0, "end": 95.0, "file": str(tmp_path / "gone.mp4")}})
    rec2.pop("posts")
    ledger.clips["c2"] = rec2
    result = outbox.enqueue(cfg, ledger, rec2, None, now=NOW)  # the version's file was deleted
    assert result["queued"] == ["tiktok"] and "no longer on this computer" in result["skipped"]["youtube"]


def test_the_posting_service_gets_one_upload_per_version(box, tmp_path, monkeypatch):
    cfg, ledger, rec = box
    Fake.on = set()
    monkeypatch.setenv("UPLOAD_POST_API_KEY", "key")
    cfg.publish.profile = "gta6clips"
    short = tmp_path / "c1.59s.mp4"
    short.write_bytes(b"short video")
    rec["cuts"] = {"youtube": {"start": 36.0, "end": 95.0, "file": str(short)}}
    uploads = []

    class Service:
        def __init__(self, cfg):
            pass

        def publish(self, clip, video, when, names):
            uploads.append((Path(video).name, sorted(names)))
            return {"request_id": f"req-{len(uploads)}"}

    monkeypatch.setattr(publish, "UploadPost", Service)
    outbox.enqueue(cfg, ledger, rec, None, now=NOW)
    outbox.deliver(cfg, ledger, NOW, lambda m: None)
    assert sorted(uploads) == [("c1.59s.mp4", ["youtube"]), ("c1.mp4", ["instagram", "tiktok"])]
    ids = {name: post["id"] for name, post in rec["posts"].items()}
    assert ids["tiktok"] == ids["instagram"] != ids["youtube"]  # two uploads, each post tied to its own


# --- a post made by hand --------------------------------------------------------------

def test_a_post_made_by_hand_is_kept_on_record_and_not_sent_again(box):
    cfg, ledger, rec = box
    post = outbox.by_hand(cfg, ledger, rec, "tiktok", " https://www.tiktok.com/@leonidaclips/video/7 ")
    assert (post["state"], post["via"], post["link"]) == ("posted", "hand", "https://www.tiktok.com/@leonidaclips/video/7")
    assert rec["status"] == "posted" and rec["links"] == {"tiktok": "https://www.tiktok.com/@leonidaclips/video/7"}
    result = outbox.enqueue(cfg, ledger, rec, None, now=NOW)  # the others still go out; TikTok is not sent a second time
    assert result["queued"] == ["youtube"] and result["skipped"]["tiktok"] == "already sent or queued"
    outbox.deliver(cfg, ledger, NOW, lambda m: None)
    assert [name for name, _, _ in Fake.sent] == ["youtube"]
    with pytest.raises(publish.PublishError, match="already posted on tiktok"):
        outbox.by_hand(cfg, ledger, rec, "tiktok")


def test_by_hand_refuses_what_would_double_post_or_is_not_a_link(box):
    cfg, ledger, rec = box
    outbox.enqueue(cfg, ledger, rec, LATER, now=NOW)
    outbox.deliver(cfg, ledger, NOW, lambda m: None)  # YouTube holds it until its time; TikTok waits here
    with pytest.raises(publish.PublishError, match="already on its way to youtube; take it back first"):
        outbox.by_hand(cfg, ledger, rec, "youtube")
    with pytest.raises(publish.PublishError, match="has to start with https://"):
        outbox.by_hand(cfg, ledger, rec, "tiktok", "javascript:alert(1)")
    with pytest.raises(publish.PublishError, match="unknown platform"):
        outbox.by_hand(cfg, ledger, rec, "myspace")
    assert outbox.by_hand(cfg, ledger, rec, "tiktok")["state"] == "posted"  # a waiting post can be done by hand instead
    assert outbox.deliver(cfg, ledger, LATER, lambda m: None)["sent"] == 0 and [name for name, _, _ in Fake.sent] == ["youtube"]


def test_a_draft_finished_in_the_tiktok_app_can_be_marked_as_posted(box):
    cfg, ledger, rec = box
    Fake.plan = [Sent("yt-1", "posted", "https://youtube.example/1"), Sent("tt-1", "draft", note="in the TikTok inbox")]
    outbox.enqueue(cfg, ledger, rec, None, now=NOW)
    outbox.deliver(cfg, ledger, NOW, lambda m: None)
    assert states(rec)["tiktok"] == "draft"
    outbox.by_hand(cfg, ledger, rec, "tiktok")
    assert states(rec) == {"youtube": "posted", "tiktok": "posted"} and rec["posts"]["tiktok"]["via"] == "hand"
