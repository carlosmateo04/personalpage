"""The platforms' rules for paying, beyond length: what is built into a clip, what is measured, and the check per platform."""

import subprocess

import numpy as np
import pytest
import soundfile as sf

from clipper import config, metadata, monetize, outbox, publish, render, review, variants
from clipper.transcribe import Word
from standins import KEEP
from test_review import clips_of, plan_with

listens = pytest.mark.skipif(not monetize.tagger_ready(config.Config()), reason="the sound-recognition model is not installed")


def video(path, seconds: int, picture: str, sound: str):
    """A small test video: `picture` and `sound` are ffmpeg sources."""
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", picture.format(d=seconds), *sound.split("|"),
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)], check=True)
    return path


def tune(path, seconds: int = 20):
    """A chord progression with a beat: what a radio station sounds like to the listener."""
    rate = 16000
    t = np.arange(rate * seconds) / rate
    sound = np.zeros_like(t)
    for i in range(int(seconds / 0.83)):
        root = [220, 174.6, 261.6, 196][i % 4]
        part = (t >= i * 0.83) & (t < (i + 1) * 0.83)
        for mult in (1, 1.25, 1.5, 2):
            sound[part] += 0.15 * np.sin(2 * np.pi * root * mult * t[part]) * np.exp(-3 * (t[part] - i * 0.83))
    sf.write(path, sound.astype(np.float32), rate)
    return path


# --- built into the clip ----------------------------------------------------------------

def test_strong_language_is_silenced_where_it_is_spoken(cfg):
    words = [Word("what", 1.0, 1.3), Word("the", 1.3, 1.5), Word("fuck", 1.5, 1.9), Word("shit", 1.95, 2.3), Word("drive", 5.0, 5.4), Word("Bitches", 9.0, 9.5)]
    assert monetize.mute_spans(cfg, words) == [(1.44, 2.36), (8.94, 9.56)]  # words close together become one stretch
    assert monetize.mute_spans(cfg, [{"w": "damn", "s": 1.0, "e": 1.4}]) == []  # mild words are left alone
    cfg.monetize.mute_profanity = False
    assert monetize.mute_spans(cfg, words) == []
    assert monetize.strong_words([{"w": "fucking", "s": 12.34, "e": 12.9}, {"w": "go", "s": 13.0, "e": 13.2}]) == [12.34]


def test_the_rendered_sound_is_silent_in_those_stretches(cfg, tmp_path):
    cfg.render.width, cfg.render.height, cfg.render.fps, cfg.render.preset, cfg.render.progress_bar = 270, 480, 15, "ultrafast", False
    src = video(tmp_path / "src.mp4", 8, "testsrc2=size=640x360:rate=15:duration={d}", "-f|lavfi|-i|sine=frequency=440:duration=8")
    ass = tmp_path / "c.ass"
    ass.write_text("[Script Info]\nScriptType: v4.00+\n\n[V4+ Styles]\n\n[Events]\n", encoding="utf-8")
    out = render.render(cfg, src, 0.0, 8.0, ass, tmp_path / "out.mp4", None, [(3.0, 4.0)])
    wav = tmp_path / "out.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(out), "-ac", "1", "-ar", "16000", str(wav)], check=True)
    sound, rate = sf.read(wav, dtype="float32")
    loud = lambda a, b: float(np.sqrt(np.mean(sound[int(a * rate):int(b * rate)] ** 2)))  # noqa: E731
    assert loud(1.0, 2.5) > 0.05 and loud(5.0, 7.0) > 0.05  # the tone is there before and after
    assert loud(3.2, 3.8) < loud(1.0, 2.5) / 50  # and gone in between


def test_an_opening_is_kept_clear_of_graphic_moments():
    # Start earlier when there is room, so the moment stays in the clip and comes after the first 15 seconds.
    assert variants.calm_start(100, 190, [105], 15, 85, 190, 95) == (89.5, 184.5)
    # No room before: start just after it.
    assert variants.calm_start(100, 190, [105], 15, 100, 190, 95) == (107.0, 190)
    # Several early moments: past the last of them.
    assert variants.calm_start(100, 190, [103, 109, 150], 15, 100, 190) == (111.0, 190)
    assert variants.calm_start(100, 190, [150], 15, 85, 190) == (100, 190)  # nothing early: as it was
    assert variants.calm_start(100, 190, [105], 0, 85, 190) == (100, 190)  # the rule switched off
    assert variants.calm_start(100, 130, [105, 118, 128], 15, 100, 130) is None  # graphic from start to end: cannot be done


def test_a_platforms_own_cut_opens_calm_too(cfg):
    cfg.edits = {"youtube": {"min_len": 0, "max_len": 59}}
    clip = {"id": "c1", "start": 100.0, "end": 190.0, "peak": 150.0, "graphic": [130.0]}
    assert variants.cut_for(cfg, clip, "youtube")[0] == (114.5, 173.5)  # by the moment alone it would start at 126.4, four seconds before the gore
    assert variants.cut_for(cfg, dict(clip, graphic=[]), "youtube")[0] == (126.4, 185.4)
    cfg.monetize.enabled = False
    assert variants.cut_for(cfg, clip, "youtube")[0] == (126.4, 185.4)


def test_the_cover_is_never_a_graphic_moment():
    assert monetize.safe_cover(90, 35.0, []) == 35.0
    assert monetize.safe_cover(90, None, []) == 30.0  # a third of the way in when none was chosen
    assert monetize.safe_cover(90, 35.0, [34.0, 36.5]) == 31.5  # the nearest moment two and a half seconds clear of both
    assert monetize.safe_cover(90, None, [29.5, 31.0]) == 27.0


# --- measured ---------------------------------------------------------------------------

@listens
def test_music_is_heard_and_speech_and_noise_are_not(cfg, tmp_path):
    src = video(tmp_path / "radio.mp4", 20, "testsrc2=size=320x180:rate=10:duration={d}", f"-i|{tune(tmp_path / 'tune.wav')}")
    heard = monetize.music(cfg, src, 0.0, 20.0)
    assert len(heard) == 4 and all(p > 0.8 for p in heard), heard
    quiet = video(tmp_path / "noise.mp4", 12, "testsrc2=size=320x180:rate=10:duration={d}", "-f|lavfi|-i|anoisesrc=d=12:c=brown:a=0.2")
    assert all(p < 0.3 for p in monetize.music(cfg, quiet, 0.0, 12.0))
    cfg.monetize.enabled = False
    assert monetize.music(cfg, src, 0.0, 20.0) is None


def test_without_the_listening_model_the_music_check_is_skipped_not_failed(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(monetize, "tagger_ready", lambda cfg: False)
    assert monetize.music(cfg, tmp_path / "missing.mp4", 0.0, 10.0) is None
    rec = {"id": "c1", "start": 0.0, "end": 60.0, "meta": {}, "seen": {"music": None}, "review": {"keep": True}}
    cfg.publish.platforms = ["tiktok"]
    line = next(l for l in monetize.check(cfg, rec)["tiktok"] if l["rule"] == "music")
    assert line["level"] == "warn" and "not checked for music" in line["text"]


def test_a_still_picture_is_told_from_a_moving_one(tmp_path):
    still = video(tmp_path / "still.mp4", 10, "color=c=navy:size=320x180:rate=10:duration={d}", "-f|lavfi|-i|sine=frequency=300:duration=10")
    moving = video(tmp_path / "moving.mp4", 10, "testsrc2=size=320x180:rate=10:duration={d}", "-f|lavfi|-i|sine=frequency=300:duration=10")
    assert monetize.frozen(still, 0.0, 10.0) > 0.9
    assert monetize.frozen(moving, 0.0, 10.0) == 0.0
    assert monetize.frozen(tmp_path / "missing.mp4", 0.0, 10.0) is None
    # A video with a keyframe every half second, as downloaded videos have every few seconds: only those are compared.
    for name, picture, want in (("still-k.mp4", "color=c=navy:size=320x180:rate=10:duration=10", 1.0), ("moving-k.mp4", "testsrc2=size=320x180:rate=10:duration=10", 0.0)):
        path = tmp_path / name
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", picture, "-c:v", "libx264", "-preset", "ultrafast", "-g", "5",
                        "-pix_fmt", "yuv420p", str(path)], check=True)
        assert len(monetize._keyframes(path, 0.0, 10.0)) >= 6 and monetize.frozen(path, 0.0, 10.0) == want


# --- the check ----------------------------------------------------------------------------

@pytest.fixture
def rec(cfg):
    """A finished 90-second clip of another channel's footage that Claude has looked at, with a 59-second version for YouTube."""
    cfg.publish.platforms = ["tiktok", "instagram", "youtube", "facebook"]
    meta = metadata.compose(cfg, "c1", "RawPlay", cfg.section("moments"), "Tank against a six-star chase", "Tank time", "https://example.com/v")
    return {"id": "c1", "start": 100.0, "end": 190.0, "meta": meta, "permission": "", "review": {"keep": True, "score": 80},
            "graphic": [], "strong": [], "seen": {"music": [0.0] * 18, "frozen": 0.0, "height": 1080},
            "cuts": {"youtube": {"start": 120.0, "end": 179.0, "file": "c1.59s.mp4"}}}


def levels(found: dict, name: str) -> dict:
    return {line["rule"]: line["level"] for line in found[name]}


def said(found: dict, name: str, rule: str) -> str:
    return next(line["text"] for line in found[name] if line["rule"] == rule)


def test_a_clean_clip_meets_every_rule_but_originality(cfg, rec):
    found = monetize.check(cfg, rec)
    assert set(found) == {"tiktok", "instagram", "youtube", "facebook"}
    assert levels(found, "youtube") == {"opening": "ok", "language": "ok", "marks": "ok", "music": "ok", "picture": "ok", "hashtags": "ok", "original": "warn"}
    assert levels(found, "instagram") == {"language": "ok", "marks": "ok", "music": "ok", "picture": "ok", "hashtags": "ok", "original": "warn"}
    assert "not paid as original without your own commentary or footage" in said(found, "tiktok", "original")
    assert monetize.blocked(cfg, dict(rec, money=found)) == {} and monetize.summary(found) == "to know: original"
    assert levels(monetize.check(cfg, dict(rec, permission="own footage")), "tiktok")["original"] == "ok"
    assert levels(monetize.check(cfg, dict(rec, voiceover=True)), "facebook")["original"] == "ok"
    cfg.monetize.enabled = False
    assert monetize.check(cfg, rec) == {}


def test_youtube_is_judged_on_the_seconds_its_own_version_opens_with(cfg, rec):
    rec["graphic"] = [124.0]  # four seconds into YouTube's version, 24 into the clip itself
    found = monetize.check(cfg, rec)
    assert levels(found, "youtube")["opening"] == "fail" and "in the first 7 seconds it earns no ads" in said(found, "youtube", "opening")
    assert "opening" not in levels(found, "tiktok")  # the other three name no such seconds
    rec["graphic"] = [131.0]
    assert levels(monetize.check(cfg, rec), "youtube")["opening"] == "warn"  # seconds 8 to 15: limited ads
    rec["graphic"] = [150.0]
    assert levels(monetize.check(cfg, rec), "youtube")["opening"] == "ok"
    del rec["review"]  # Claude did not look: nobody knows
    found = monetize.check(cfg, rec)
    assert levels(found, "youtube")["opening"] == "warn" and levels(found, "tiktok")["marks"] == "warn"


def test_music_language_picture_and_words(cfg, rec):
    rec["seen"]["music"] = [0.9] * 8 + [0.1] * 10  # the first 40 seconds
    found = monetize.check(cfg, rec)
    assert levels(found, "tiktok")["music"] == "warn" and "music plays for about 40 of 90 seconds" in said(found, "tiktok", "music")
    assert "about 20 of 59 seconds" in said(found, "youtube", "music") and "the ad money goes to them" in said(found, "youtube", "music")
    del rec["cuts"]  # YouTube set to take the whole minute and a half
    found = monetize.check(cfg, rec)
    assert levels(found, "youtube")["music"] == "fail" and "a Short over a minute is blocked" in said(found, "youtube", "music")
    assert monetize.holds(cfg, rec) == ""
    cfg.monetize.music = "hold"
    assert monetize.holds(cfg, rec) == ""  # under half of the clip
    rec["seen"]["music"] = [0.9] * 12 + [0.1] * 6
    assert monetize.holds(cfg, rec) == "music plays for about 60 of 90 seconds"

    rec["seen"]["music"] = [0.0] * 18
    rec["strong"] = [110.0, 185.0]
    assert said(monetize.check(cfg, rec), "tiktok", "language") == "strong language silenced 2 time(s) and masked in the captions"
    cfg.monetize.mute_profanity = False
    assert levels(monetize.check(cfg, rec), "tiktok")["language"] == "warn"
    rec["meta"]["title"] = "What the fuck was that"
    assert levels(monetize.check(cfg, rec), "tiktok")["language"] == "fail"
    rec["meta"]["title"] = "Tank against a six-star chase"

    rec["seen"].update(frozen=0.8)
    assert "stands still for 80% of the clip" in said(monetize.check(cfg, rec), "facebook", "picture")
    rec["seen"].update(frozen=0.0, height=480)
    assert levels(monetize.check(cfg, rec), "instagram")["picture"] == "warn"
    rec["seen"].update(height=1080)

    rec["meta"]["instagram"]["title"] += " #one #two #three"
    rec["meta"]["facebook"]["title"] += " Like and share!"
    rec["meta"]["tiktok"]["title"] += " follow for more https://example.com"
    found = monetize.check(cfg, rec)
    assert levels(found, "instagram")["hashtags"] == "fail" and "Instagram takes 5 at most" in said(found, "instagram", "hashtags")
    assert levels(found, "facebook")["bait"] == "fail" and [l["level"] for l in found["tiktok"] if l["rule"] == "bait"] == ["warn", "warn"]
    cfg.campaign.paid = True
    assert levels(monetize.check(cfg, rec), "tiktok")["paid"] == "warn"


def test_every_caption_is_written_inside_the_caps(cfg):
    meta = metadata.compose(cfg, "c1", "RawPlay", cfg.section("moments"), "Tank against a six-star chase", "Tank time")
    count = lambda text: len(monetize._HASHTAG.findall(text))  # noqa: E731
    assert count(meta["instagram"]["title"]) == 5 and count(meta["facebook"]["title"]) <= 5 and count(meta["tiktok"]["title"]) <= 5
    assert metadata.proof(cfg, meta) == []
    meta["instagram"]["title"] += " #extra"
    assert "the Instagram caption needs 3 to 5 hashtags" in metadata.proof(cfg, meta)


def test_a_platform_whose_rule_a_clip_breaks_is_not_sent_it(cfg, rec, ledger, tmp_path, monkeypatch):
    from clipper import connectors
    from test_outbox import Fake, Tok, Tube

    monkeypatch.setattr(connectors, "ALL", {"youtube": Tube, "tiktok": Tok})
    Fake.sent, Fake.plan, Fake.answers, Fake.on, Fake.got = [], [], {}, {"youtube", "tiktok"}, {}
    cfg.publish.platforms = ["youtube", "tiktok"]
    for name in ("c1.mp4", "c1.59s.mp4"):
        (tmp_path / name).write_bytes(b"video")
    rec.update(file=str(tmp_path / "c1.mp4"), status="held", graphic=[124.0])
    rec["cuts"]["youtube"]["file"] = str(tmp_path / "c1.59s.mp4")
    rec["money"] = monetize.check(cfg, rec)
    ledger.clips["c1"] = rec
    result = outbox.enqueue(cfg, ledger, rec, None)
    assert result["queued"] == ["tiktok"] and "in the first 7 seconds it earns no ads" in result["skipped"]["youtube"]
    cfg.monetize.strict = False  # the owner would rather post it anyway
    assert monetize.blocked(cfg, rec) == {} and outbox.enqueue(cfg, ledger, rec, None)["queued"] == ["youtube"]


# --- the editor ---------------------------------------------------------------------------

@pytest.fixture
def planned(cfg):
    cfg.moments.min_len, cfg.moments.max_len = 85, 95
    return clips_of(plan_with(cfg, 1))[0]  # a clip from 100 to 190 seconds, with its surroundings from 85 to 205


def test_a_clip_with_another_platforms_mark_or_unpaid_content_is_dropped(cfg, planned):
    clip = dict(planned)
    assert review.edit(cfg, clip, {**KEEP, "marks": "TikTok logo, bottom right"}) == "dropped: another platform's or channel's mark is on the picture (TikTok logo, bottom right)"
    assert clip["drop"] and clip["review"]["keep"] is False
    clip = dict(planned)
    assert "it shows torture of a hostage, which no platform pays for" in review.edit(cfg, clip, {**KEEP, "unsuitable": "torture of a hostage"})
    clip = dict(planned)
    assert review.edit(cfg, clip, {**KEEP, "marks": "None.", "unsuitable": "none"}).startswith("kept") and not clip.get("drop")
    cfg.monetize.enabled = False
    clip = dict(planned)
    assert review.edit(cfg, clip, {**KEEP, "marks": "TikTok logo"}).startswith("kept")


def test_the_editor_moves_the_start_so_a_graphic_moment_comes_after_fifteen_seconds(cfg, planned):
    clip = dict(planned)
    done = review.edit(cfg, clip, {**KEEP, "graphic": [{"at": "1:46", "what": "close-up headshot"}, {"at": "9:59", "what": "outside the clip"}], "cover": "1:46"})
    assert clip["graphic"] == [106.0]  # in seconds of the source video; the one outside what was shown is ignored
    assert (clip["start"], clip["end"]) == (90.5, 185.5) and clip["review"]["calm"] and clip["review"]["cut"] == [100.0, 190.0]
    assert "opening kept clear of a graphic moment" in done and clip["graphic"][0] - clip["start"] >= 15
    assert all(90.5 <= w["s"] and w["e"] <= 185.5 for w in clip["words"]) and clip["text"] == " ".join(w["w"] for w in clip["words"])
    assert clip["cover"] == 15.5  # as Claude chose it; the render moves it off the moment
    assert monetize.safe_cover(95.0, clip["cover"], [106.0 - 90.5]) != 15.5

    whole = dict(planned, whole=True)  # a short video used whole cannot be moved: the check then says so
    review.edit(cfg, whole, {**KEEP, "graphic": [{"at": "1:46", "what": "close-up headshot"}]})
    assert (whole["start"], whole["end"]) == (100.0, 190.0) and whole["graphic"] == [106.0]
    cfg.publish.platforms = ["youtube"]
    cfg.edits = {}
    found = monetize.check(cfg, dict(whole, review={"keep": True}, seen={}))
    assert found["youtube"][0] == {"rule": "opening", "level": "fail", "text": "graphic violence 6 seconds in: in the first 7 seconds it earns no ads"}
