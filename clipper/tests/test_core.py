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
    assert any("too little speech" in r for r in gates.check(cfg, _cand("no way"), "src1", ledger))
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
