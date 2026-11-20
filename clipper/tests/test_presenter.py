"""Gameplay-only filter: no streamer on camera, nobody talking over the game."""

import numpy as np
import soundfile as sf

from clipper import config, gates, intake, metadata, moments, presenter
from conftest import speak
from test_intake import FakeSite


def _with_faces(monkeypatch, per_frame):
    """Pretend the detector found these face boxes (x, y, w, h) in 16 sampled frames."""
    shots = [np.zeros((540, 960, 3), dtype=np.uint8) for _ in per_frame]
    for i, shot in enumerate(shots):
        shot[0, 0, 0] = i  # tag each frame so the fake detector can tell them apart
    monkeypatch.setattr(presenter, "frames", lambda *a, **k: shots)
    monkeypatch.setattr(presenter, "_faces", lambda shot: per_frame[int(shot[0, 0, 0])])


def test_a_face_that_stays_put_is_a_facecam(cfg, monkeypatch, tmp_path):
    steady = [[(780 + (i % 3) * 6, 420 + (i % 2) * 5, 40, 50)] if i not in (3, 9) else [] for i in range(16)]
    _with_faces(monkeypatch, steady)  # bottom-right, 14 of 16 frames, wobbling a few pixels
    found, where = presenter.facecam(cfg, tmp_path / "v.mp4", 0, 30)
    assert found and "bottom right" in where and "88%" in where


def test_game_characters_moving_around_are_not_a_facecam(cfg, monkeypatch, tmp_path):
    roaming = [[((i * 137) % 800, (i * 71) % 400, 90, 110)] for i in range(16)]
    _with_faces(monkeypatch, roaming)
    assert presenter.facecam(cfg, tmp_path / "v.mp4", 0, 30) == (False, "")
    closeup = [[(600, 200, 100, 120)] if 4 <= i < 9 else [] for i in range(16)]  # a 5-frame cutscene close-up
    _with_faces(monkeypatch, closeup)
    assert presenter.facecam(cfg, tmp_path / "v.mp4", 0, 30) == (False, "")
    _with_faces(monkeypatch, [[] for _ in range(16)])
    assert presenter.facecam(cfg, tmp_path / "v.mp4", 0, 30) == (False, "")


def test_commentator_talk_is_recognised(cfg):
    assert presenter.talk(cfg, "yo chat, look at this") == ["chat"]
    assert "this game" in presenter.talk(cfg, "Bro, this game is unreal.")
    assert presenter.talk(cfg, "Jason, get in the car. We have to go, now!") == []  # game dialogue
    game_dialogue = "Jason, get in the car. We need to chat about the money. " * 40
    assert presenter.talk(cfg, game_dialogue) == []  # one odd word in a long video is not a commentator
    streamer = "Welcome back to the channel. This game is crazy, chat. " + "We drive around the city. " * 40
    assert set(presenter.talk(cfg, streamer)) >= {"chat", "this game"}


def test_filter_can_be_switched_off(cfg, monkeypatch, tmp_path):
    _with_faces(monkeypatch, [[(780, 420, 40, 50)] for _ in range(16)])
    path = tmp_path / "v.mp4"
    reasons = presenter.check(cfg, path, 0, 30, "chat look at this")
    assert len(reasons) == 2 and "talking over" in reasons[0] and "facecam" in reasons[1]
    cfg.filters.no_facecam = cfg.filters.no_commentary = False
    assert presenter.check(cfg, path, 0, 30, "chat look at this") == []


def test_action_without_speech_is_found(cfg, tmp_path):
    """A loud stretch (a chase, an explosion) in footage where nobody speaks."""
    rate = 16000
    audio = np.random.default_rng(4).normal(0, 0.01, rate * 120).astype(np.float32)
    audio[50 * rate : 70 * rate] *= 30
    wav = tmp_path / "action.wav"
    sf.write(wav, audio, rate)
    found = moments.find(cfg.moments, wav, [], 120.0)
    assert len(found) == 1 and found[0].words == []
    assert found[0].start <= 50 and found[0].end >= 68
    assert cfg.moments.min_len <= found[0].duration <= cfg.moments.max_len + 1


def test_sparse_game_dialogue_does_not_shrink_the_clip(cfg, tmp_path):
    """One shouted line in a 25 s action stretch: the clip covers the action, not just the line."""
    rate = 16000
    audio = np.random.default_rng(6).normal(0, 0.01, rate * 120).astype(np.float32)
    audio[50 * rate : 75 * rate] *= 30
    wav = tmp_path / "chase.wav"
    sf.write(wav, audio, rate)
    line = speak("get down!", 60.0, 0.4)
    found = moments.find(cfg.moments, wav, line, 120.0)
    assert len(found) == 1 and found[0].duration >= cfg.moments.min_len
    assert found[0].start <= 51 and found[0].end >= 70 and [w.w for w in found[0].words] == ["get", "down!"]


def test_titles_without_commentary(cfg):
    silent = moments.Candidate(100.0, 130.0, 115.0, 3.0, [])
    glitch = cfg.section("glitches")
    cut = metadata.build(cfg, silent, "c1", "Shirrako", glitch, "GTA 6 Gameplay Walkthrough Part 1 FULL GAME [4K PS5 PRO] - No Commentary", "", whole=False)
    assert cut["title"] == "GTA 6 Glitch"  # the upload title says nothing, so the game and the section are used
    guide = metadata.build(cfg, silent, "c1", "PowerPyx", cfg.section("findings"), "GTA 6 - Bigfoot / Sasquatch Easter Egg Location", "", whole=False)
    assert "Bigfoot / Sasquatch Easter Egg Location" in guide["title"]
    assert metadata.clean_source_title("GTA 6 - How to get the Jetpack (No Commentary) [4K]", "GTA 6") == "GTA 6 - How to get the Jetpack"
    assert metadata.clean_source_title("Grand Theft Auto VI Gameplay Walkthrough Part 4 - No Commentary (GTA 6)", "GTA 6") == ""


def test_sources_can_filter_by_title(cfg, ledger, tmp_path, monkeypatch):
    site = FakeSite(tmp_path)
    site.videos = {
        "a": {"title": "GTA 6 Police Chase - No Commentary", "view_count": 9000, "duration": 300, "channel": "RawPlay"},
        "b": {"title": "GTA 6 Police Chase REACTION", "view_count": 9500, "duration": 300, "channel": "Reactor"},
        "c": {"title": "GTA 6 funny moments", "view_count": 9900, "duration": 300, "channel": "Funny"},
    }
    monkeypatch.setattr(intake, "_yt", site)
    monkeypatch.setattr(intake, "PAUSE", 0)
    cfg.sources = [config.Source(type="ytdlp", url="https://site/search", title_any=["no commentary"], title_none=["reaction"], max_items=5)]
    items = intake.collect(cfg, ledger, lambda m: None)
    assert [i.title for i in items] == ["GTA 6 Police Chase - No Commentary"]
    assert sum("--skip-download" in c for c in site.calls) == 1  # the others were dropped on their titles alone


def _talk(cfg, script, duration):
    """Timed words from (start second, sentence) pairs."""
    words = []
    for at, sentence in script:
        words += speak(sentence, at, 0.4)
    return presenter.commentary(cfg, words, duration)


def test_a_spoken_intro_does_not_rule_out_a_long_video(cfg):
    game = [(t, "get in the car we have to go now") for t in range(120, 2600, 90)]
    intro = [(4, "hey guys welcome back to my channel"), (9, "this is gta 6 gameplay with no commentary")]
    verdict, avoid = _talk(cfg, intro + game, 2700.0)
    assert verdict == "parts" and len(avoid) == 1
    assert avoid[0][0] == 0.0 and 40 < avoid[0][1] < 60  # the intro plus half a minute, nothing else
    assert presenter.clock(avoid[0][1]).startswith("0:")


def test_commentary_all_the_way_through_rules_it_out(cfg):
    game = [(t, "get in the car we have to go now") for t in range(120, 2600, 90)]
    chatter = [(t, "chat this game is crazy look at the graphics") for t in range(30, 2600, 300)]
    verdict, avoid = _talk(cfg, sorted(chatter + game), 2700.0)
    assert verdict == "throughout" and len(avoid) >= 4


def test_stray_words_in_game_dialogue_are_ignored(cfg):
    game = [(t, "get in the car we have to go now") for t in range(120, 2600, 90)]
    assert _talk(cfg, game + [(1000, "we need to chat about the money")], 2700.0) == ("none", [])
    assert _talk(cfg, game, 2700.0) == ("none", [])


def test_clips_meet_the_length_the_platforms_pay_for(tmp_path):
    """With the project's own settings, every clip cut from a long video runs about a minute and a half."""
    from clipper import config as cfgmod

    real = cfgmod.load()  # the config.toml that ships with the clipper
    assert (real.moments.min_len, real.moments.max_len) == (85, 95)
    assert (real.moments.whole_clip_min, real.moments.whole_clip_max) == (80, 100)
    rate = 16000
    audio = np.random.default_rng(9).normal(0, 0.01, rate * 600).astype(np.float32)
    for at, length in ((100, 15), (300, 40), (480, 8)):  # three bursts of action of different lengths
        audio[at * rate : (at + length) * rate] *= 30
    wav = tmp_path / "long.wav"
    sf.write(wav, audio, rate)
    shout = speak("get down! go go go!", 305.0, 0.4)  # one shouted line inside the second burst
    found = moments.find(real.moments, wav, shout, 600.0)
    assert len(found) == 3
    for clip in found:
        assert 84.99 <= clip.duration <= 96, clip.duration  # about a minute and a half
    starts = sorted(c.start for c in found)
    assert starts[0] <= 100 and 250 <= starts[1] <= 300 and starts[2] <= 480
    short = moments.Candidate(0.0, 70.0, 35.0, 3.0, [])
    from clipper.state import Ledger
    assert any("too short" in r for r in gates.check(real, short, "s", Ledger(tmp_path / "l.json"), whole=True))
    ok = moments.Candidate(0.0, 92.0, 40.0, 3.0, [])
    assert gates.check(real, ok, "s", Ledger(tmp_path / "l.json"), whole=True) == []
