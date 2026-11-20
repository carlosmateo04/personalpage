from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np
import soundfile as sf

from clipper import gates, metadata, moments, schedule
from clipper.transcribe import words_from_tokens
from conftest import speak


def test_tokens_join_into_words():
    tokens = [" We", " need", " about", " ", "$", "2", ",", "0", "0", "0", " be", "fore", "."]
    starts = [i * 0.2 for i in range(len(tokens))]
    words = words_from_tokens(tokens, starts, [0.1] * len(tokens), offset=10.0)
    assert [w.w for w in words] == ["We", "need", "about", "$2,000", "before."]
    assert words[0].s == 10.0
    assert all(a.e <= b.s for a, b in zip(words, words[1:]))


def _stream(tmp_path):
    """90 s of quiet talk with a loud, excited stretch from 40 s to 58 s."""
    rate, total = 16000, 90
    rng = np.random.default_rng(1)
    audio = rng.normal(0, 0.01, rate * total).astype(np.float32)
    audio[40 * rate : 58 * rate] *= 30
    wav = tmp_path / "a.wav"
    sf.write(wav, audio, rate)
    words = []
    for t in range(2, 38, 6):
        words += speak("we are just driving around the city for now.", t, 0.4)
    words += speak("wait wait wait. no way! he drove the bus off the bridge!", 40.5, 0.35)
    words += speak("oh my god did you see that? clip that right now!", 46.0, 0.35)
    words += speak("that is the craziest thing I have ever seen in this game!", 51.0, 0.4)
    for t in range(62, 86, 6):
        words += speak("okay let me check the map again.", t, 0.4)
    return wav, words, float(total)


def test_moment_is_found_around_the_loud_stretch(cfg, tmp_path):
    wav, words, total = _stream(tmp_path)
    found = moments.find(cfg.moments, wav, words, total)
    assert found, "no candidate"
    best = found[0]
    assert best.start <= 41 and best.end >= 55
    assert cfg.moments.min_len - 1 <= best.duration <= cfg.moments.max_len + 1
    starts = {round(s.s, 2) for s in moments.sentences(words)}
    assert any(abs(best.start - s) < 0.3 for s in starts)  # opens on a sentence, not mid-word
    for a in found:
        for b in found:
            assert a is b or a.end <= b.start or b.end <= a.start


def test_quiet_stream_yields_nothing(cfg, tmp_path):
    rate = 16000
    wav = tmp_path / "q.wav"
    sf.write(wav, np.random.default_rng(2).normal(0, 0.01, rate * 60).astype(np.float32), rate)
    words = []
    for t in range(0, 56, 6):
        words += speak("we are just driving around the city for now.", t, 0.4)
    assert moments.find(cfg.moments, wav, words, 60.0) == []


def test_slots_skip_taken_past_and_stop_at_the_horizon(cfg):
    s = cfg.schedule
    tz = ZoneInfo(s.timezone)
    now = datetime(2026, 11, 18, 15, 50, tzinfo=timezone.utc)  # 10:50 in New York
    first = datetime(2026, 11, 18, 11, 30, tzinfo=tz)
    slots = schedule.next_slots(s, now, {first.isoformat()}, 3)
    assert [d.strftime("%d %H:%M") for d in slots] == ["18 15:00", "18 19:00", "18 21:30"]
    late = datetime(2026, 11, 18, 16, 15, tzinfo=timezone.utc)  # 11:15: inside the 20 minute lead of 11:30
    assert schedule.next_slots(s, late, set(), 1)[0].strftime("%H:%M") == "15:00"
    assert len(schedule.next_slots(s, now, set(), 99)) == 4 * (s.max_days_ahead + 1)


def _cand(text, start=100.0, pace=0.3):
    words = speak(text, start, pace)
    return moments.Candidate(start, words[-1].e + 0.4, start + 3, 2.0, words)


LONG = "no way he just drove the bus off the bridge oh my god did you see that clip that right now " * 4


def test_gates(cfg, ledger):
    good = _cand(LONG)
    assert gates.check(cfg, good, "src1", ledger) == []
    quiet = moments.Candidate(100.0, 125.0, 110.0, 2.0, speak("no way", 100.0))
    assert gates.check(cfg, quiet, "src1", ledger) == []  # gameplay with next to no speech is fine by default
    cfg.filters.require_speech = True
    assert any("too little speech" in r for r in gates.check(cfg, quiet, "src1", ledger))
    cfg.filters.require_speech = False
    silent_twin = moments.Candidate(100.0, 125.0, 110.0, 2.0, [])
    ledger.clips["silent"] = {"id": "silent", "source_id": "other", "start": 0, "end": 30, "status": "posted", "text": "", "created": "0"}
    assert gates.check(cfg, silent_twin, "src1", ledger) == []  # two silent clips are not "the same moment"
    del ledger.clips["silent"]
    casino = _cand(LONG + " thanks to stake for the sponsor")
    assert any("banned term" in r for r in gates.check(cfg, casino, "src1", ledger))
    ledger.clips["old"] = {"id": "old", "source_id": "src1", "start": 95.0, "end": 120.0, "status": "scheduled", "text": "x"}
    assert any("overlaps" in r for r in gates.check(cfg, good, "src1", ledger))
    ledger.clips["old"]["status"] = "failed"  # a failed clip does not block the moment
    assert gates.check(cfg, good, "src1", ledger) == []
    ledger.clips["twin"] = {"id": "twin", "source_id": "other", "start": 0, "end": 30, "status": "posted", "text": gates.normalise(good.text), "created": "1"}
    assert any("same moment" in r for r in gates.check(cfg, good, "src1", ledger))


def test_metadata_discloses_paid_clips(cfg):
    cfg.campaign.paid = True
    cfg.campaign.name = "StreamerX"
    cfg.campaign.required_hashtags = ["streamerxclips"]
    cand = _cand("okay chat. no way he just drove the bus off the bridge! that was insane.")
    meta = metadata.build(cfg, cand, "clip1", "StreamerX")
    for platform in ("tiktok", "instagram", "facebook"):
        assert meta[platform]["title"].startswith("#ad ")
        assert "#streamerxclips" in meta[platform]["title"]
    assert meta["label"].startswith("Paid partnership")
    assert meta["youtube"]["description"].startswith("Paid partnership with StreamerX")
    assert len(meta["youtube"]["title"]) <= 100 and meta["youtube"]["title"].endswith("#shorts")
    assert "bus off the bridge" in meta["title"]
    assert meta == metadata.build(cfg, cand, "clip1", "StreamerX")  # deterministic
    cfg.campaign.paid = False
    plain = metadata.build(cfg, cand, "clip1", "StreamerX")
    assert plain["label"] == "" and "#ad" not in plain["tiktok"]["title"]


def test_payout_follows_the_campaign_terms(cfg):
    c = cfg.campaign
    c.rate_per_1k, c.min_payout, c.max_payout, c.fee, c.counted_platforms = 0.70, 2.0, 35.0, 0.10, ["tiktok", "instagram"]
    assert c.payout("tiktok", 2_000) == 0.0  # $1.40 is under the $2 minimum
    assert c.payout("tiktok", 10_000) == 6.30  # $7.00 less the 10% fee
    assert c.payout("instagram", 500_000) == 31.50  # capped at $35, less the fee
    assert c.payout("youtube", 500_000) == 0.0  # not a counted platform
    c.rate_per_1k = 0.0
    assert c.payout("tiktok", 10_000) == 0.0


def test_report_estimates_earnings(cfg, ledger):
    from clipper import report

    c = cfg.campaign
    c.name, c.rate_per_1k, c.min_payout, c.max_payout, c.fee, c.counted_platforms = "StreamerX", 0.70, 2.0, 35.0, 0.10, ["tiktok", "instagram"]
    now = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
    ledger.clips["a"] = {
        "id": "a", "title": "No way", "status": "posted", "slot": "2026-11-19T15:00:00-05:00",
        "links": {"tiktok": "https://t/1", "youtube": "https://y/1"}, "views": {"tiktok": 10_000, "youtube": 90_000},
    }
    text = report.build(cfg, ledger, None, now)
    assert "tiktok: https://t/1, 10,000 views, about $6.30" in text
    assert "youtube: https://y/1, 90,000 views" in text and "90,000 views, about" not in text
    assert "| Last 7 days | 10,000 | $6.30 |" in text
    c.rate_per_1k = 0.0
    assert "Estimated campaign earnings" not in report.build(cfg, ledger, None, now)


def test_paid_campaign_must_be_named_before_going_live(cfg):
    import pytest

    from clipper import config

    cfg.campaign.paid, cfg.publish.mode = True, "live"
    with pytest.raises(config.ConfigError, match="campaign.name"):
        config.validate(cfg)
    cfg.campaign.name = "StreamerX"
    config.validate(cfg)
    cfg.campaign.name, cfg.publish.mode = "", "dry_run"
    config.validate(cfg)  # fine while nothing is posted


def test_config_saved_by_a_windows_editor_still_loads(tmp_path):
    from clipper import config

    path = tmp_path / "config.toml"
    path.write_bytes(b"\xef\xbb\xbf" + b'[brand]\r\nname = "Test"\r\n\r\n[[sources]]\r\ntype = "folder"\r\npath = "inbox"\r\n')  # byte-order mark, Windows line ends
    cfg = config.load(path)
    assert cfg.brand.name == "Test" and cfg.sources[0].type == "folder"


def test_cleanup_deletes_only_old_files_in_its_own_folders(cfg):
    import os

    from clipper import pipeline

    now = datetime(2026, 12, 1, tzinfo=timezone.utc)

    def make(path, age_days, size=10):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)
        stamp = now.timestamp() - age_days * 86400
        os.utime(path, (stamp, stamp))
        return path

    work, out = cfg.path("work"), cfg.path("out")
    old_video = make(work / "downloads" / "old.mp4", 5, 1000)
    new_video = make(work / "downloads" / "new.mp4", 1)
    old_audio = make(work / "audio" / "old.wav", 3)
    other = make(work / "notes" / "keep.txt", 99)  # not a folder the clipper cleans
    make(out / "20261001-000000" / "plan.json", 61)
    old_clip = make(out / "20261001-000000" / "c1.mp4", 61)
    make(out / "20261125-000000" / "plan.json", 6)
    new_clip = make(out / "20261125-000000" / "c2.mp4", 6)
    outbox = make(out / "outbox.json", 99)
    inbox = make(cfg.path("inbox") / "mine.mp4", 99)

    result = pipeline.cleanup(cfg, now, lambda m: None)
    assert not old_video.exists() and not old_audio.exists() and not old_clip.exists()
    assert not (out / "20261001-000000").exists()  # the emptied run folder goes too
    assert new_video.exists() and new_clip.exists() and other.exists() and outbox.exists() and inbox.exists()
    assert result["files"] == 4 and result["bytes"] == 1000 + 10 + 10 + 10


def test_search_check_passes_a_good_clip_and_names_what_is_wrong_with_a_bad_one(cfg):
    from clipper import metadata

    section = cfg.section("tricks")
    good = metadata.compose(cfg, "c1", "RawPlay", section, "Fastest way across the bridge", "Skip the traffic", "https://example.com/v",
                            summary="The player jumps the gap.", tags=["gta 6 bridge jump"])
    assert metadata.proof(cfg, good) == []
    assert good["youtube"]["title"] == "Fastest way across the bridge | GTA 6 Tips #shorts"
    assert all("#gta6" in good[p]["title"].split() for p in ("tiktok", "instagram", "facebook"))  # the main tag is on every clip

    assert metadata.proof(cfg, metadata.compose(cfg, "c2", "", cfg.section("moments"), "GTA 6 Highlight", "GTA 6 Highlight")) == [
        "the title only names the game and the section; it does not say what happens"]

    bad = metadata.compose(cfg, "c3", "RawPlay", section, "INSANE UNBELIEVABLE CASINO MONEY TRICK YOU HAVE TO SEE", "x")
    bad["tiktok"]["title"] = "look at this #one #one"
    bad["youtube"]["title"] = "Money trick"
    bad["youtube"]["tags"] = ["money"]
    problems = metadata.proof(cfg, bad)
    for expected in ("the title is mostly capital letters", "the YouTube title does not name GTA 6", "the YouTube title does not end in #shorts",
                     "YouTube needs at least 3 tags, one of them naming GTA 6", "the TikTok caption needs 3 to 10 hashtags",
                     "the TikTok caption repeats a hashtag", "the TikTok caption does not name GTA 6",
                     "the TikTok caption does not credit the source", "the text uses a banned term: casino"):
        assert expected in problems, expected


def test_youtube_tags_stay_under_the_limit(cfg):
    from clipper import metadata

    cfg.seo.youtube_tags = [f"gta 6 tag number {n} with some extra words" for n in range(40)]
    meta = metadata.compose(cfg, "c1", "RawPlay", cfg.section("moments"), "Tank against a six-star chase", "Tank time")
    assert 300 < sum(len(t) + 1 for t in meta["youtube"]["tags"]) <= 500 and metadata.proof(cfg, meta) == []
