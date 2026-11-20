"""The whole run on generated footage. Needs the speech models (./setup.sh); skipped without them."""

import json
import subprocess
from datetime import datetime, timezone

import numpy as np
import pytest
import soundfile as sf

from clipper import config, media, pipeline, report, transcribe
from clipper.state import Ledger

SAMPLE = config.Config().models_dir / transcribe.ASR_DIR / "test_wavs" / "0.wav"
pytestmark = pytest.mark.skipif(not SAMPLE.exists(), reason="speech models not installed")
NOW = datetime(2026, 11, 18, 15, 0, tzinfo=timezone.utc)


@pytest.fixture
def footage(cfg):
    """A 70 s video: a real speech sample repeated quietly, with three loud repeats in the middle."""
    speech, rate = sf.read(str(SAMPLE), dtype="float32")
    track = np.random.default_rng(5).normal(0, 0.003, rate * 70).astype(np.float32)  # a quiet game-audio bed
    for at, gain in ((2, 0.08), (12, 0.08), (24, 1.0), (32, 1.0), (40, 1.0), (52, 0.08), (61, 0.08)):
        track[at * rate : at * rate + len(speech)] += speech * gain
    wav = cfg.root / "track.wav"
    sf.write(wav, track, rate)
    video = cfg.root / "inbox" / "stream.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=70", "-i", str(wav),
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(video)], check=True)
    return video


def test_run_makes_schedules_and_never_repeats(cfg, ledger, footage):
    quiet = lambda m: None
    plan = pipeline.plan(cfg, ledger, NOW, quiet)
    assert len(plan["clips"]) == 1, plan
    clip = plan["clips"][0]
    assert clip["start"] < 26 and clip["end"] > 40  # the loud stretch
    summary = pipeline.execute(cfg, ledger, now=NOW, log=quiet)
    assert summary["clips"] == 1 and summary["failed"] == 0 and summary["mode"] == "dry_run"

    rec = Ledger(ledger.path).clips[clip["id"]]  # as a later run would read it
    assert rec["status"] == "scheduled" and rec["slot"].startswith("2026-11-18T11:30")
    out = media.probe(cfg.path("out") / plan["run"] / f"{clip['id']}.mp4")
    assert (out.width, out.height, out.has_audio, out.pixel_shape) == (1080, 1920, True, "1:1")
    assert abs(out.duration - (clip["end"] - clip["start"])) < 0.5
    outbox = json.loads((cfg.path("out") / "outbox.json").read_text())
    assert outbox[0]["args"]["scheduled_date"] == "2026-11-18T16:30:00Z"
    assert ledger.sources[clip["source_id"]]["status"] == "done"

    text = report.build(cfg, ledger, summary, NOW)
    assert clip["meta"]["title"] in text and "DRY RUN" in text

    kit = (cfg.path("out") / plan["run"] / f"{clip['id']}.txt").read_text()  # the text to post alongside the video
    assert "=== TikTok ===" in kit and clip["meta"]["tiktok"]["title"] in kit
    assert f"Title: {clip['meta']['youtube']['title']}" in kit and "POST AT   Wed 18 Nov 2026, 11:30 EST" in kit
    assert "no permission on file" not in kit  # this footage came from the local folder

    again = pipeline.plan(cfg, ledger, NOW, quiet)  # same footage, next run
    assert again["clips"] == [] and again["rejected"] == []


def test_dropped_clip_is_not_rendered(cfg, ledger, footage):
    quiet = lambda m: None
    plan = pipeline.plan(cfg, ledger, NOW, quiet)
    path = cfg.path("out") / plan["run"] / "plan.json"
    data = json.loads(path.read_text())
    data["clips"][0]["drop"] = True
    path.write_text(json.dumps(data))
    summary = pipeline.execute(cfg, ledger, path, NOW, quiet)
    assert summary["clips"] == 0
    assert ledger.clips[plan["clips"][0]["id"]]["status"] == "dropped"
    assert not list((cfg.path("out") / plan["run"]).glob("*.mp4"))


def test_interrupted_run_is_picked_up_again(cfg, ledger, footage):
    quiet = lambda m: None
    first = pipeline.plan(cfg, ledger, NOW, quiet)  # planned, then the machine goes away before execute
    second = pipeline.plan(cfg, Ledger(ledger.path), NOW, quiet)
    assert [c["id"] for c in second["clips"]] == [c["id"] for c in first["clips"]]
    assert second["clips"][0]["slot"] == first["clips"][0]["slot"]


def test_hold_for_review_then_approve_with_voiceover(cfg, ledger, footage):
    quiet = lambda m: None
    cfg.publish.hold_for_review = True
    plan = pipeline.plan(cfg, ledger, NOW, quiet)
    pipeline.execute(cfg, ledger, now=NOW, log=quiet)
    cid = plan["clips"][0]["id"]
    assert ledger.clips[cid]["status"] == "held"
    assert not (cfg.path("out") / "outbox.json").exists()

    voice = cfg.root / "take.wav"
    rate = 48000
    sf.write(voice, (0.3 * np.sin(2 * np.pi * 220 * np.arange(rate * 3) / rate)).astype(np.float32), rate)
    before = (cfg.path("out") / plan["run"] / f"{cid}.mp4").stat().st_size
    assert pipeline.approve(cfg, ledger, [cid], {cid: str(voice)}, NOW, quiet) == 1
    assert ledger.clips[cid]["status"] == "scheduled"
    assert (cfg.path("out") / plan["run"] / f"{cid}.mp4").stat().st_size != before  # re-rendered with the voiceover
    assert len(json.loads((cfg.path("out") / "outbox.json").read_text())) == 1


def test_short_clip_is_used_whole_with_its_own_title_and_credit(cfg, ledger):
    """A 20 s clip (like a Twitch clip) skips the moment finder and keeps its title, channel and section."""
    from clipper import intake

    speech, rate = sf.read(str(SAMPLE), dtype="float32")
    track = np.zeros(rate * 20, dtype=np.float32)
    track[2 * rate : 2 * rate + len(speech)] += speech
    wav = cfg.root / "short.wav"
    sf.write(wav, track, rate)
    video = cfg.root / "short.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=20", "-i", str(wav),
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(video)], check=True)
    item = intake.Item(video, "ytest", "car falls through the map glitch", "StreamerA", "", "https://site/clip/1",
                       title="car falls through the map glitch", views=5000)
    quiet = lambda m: None
    original = intake.collect
    intake.collect = lambda *a, **k: [item]
    try:
        plan = pipeline.plan(cfg, ledger, NOW, quiet)
    finally:
        intake.collect = original
    clip = plan["clips"][0]
    assert (clip["start"], round(clip["end"])) == (0.0, 20) and clip["section"] == "glitches"
    assert clip["meta"]["title"].startswith("Car falls through the map glitch") or "Car falls through the map glitch" in clip["meta"]["title"]
    assert clip["meta"]["credit"] == "Credit: StreamerA" and clip["meta"]["badge"] == "GLITCH"
    summary = pipeline.execute(cfg, ledger, now=NOW, log=quiet)
    assert summary["clips"] == 1 and ledger.clips[clip["id"]]["section"] == "glitches"
    text = report.build(cfg, ledger, summary, NOW)
    assert "| glitches |" in text and "StreamerA" in text


def test_footage_with_a_streamer_is_left_out(cfg, ledger, footage, monkeypatch):
    from clipper import presenter

    quiet = lambda m: None
    monkeypatch.setattr(presenter, "facecam", lambda *a, **k: (True, "a face stays at the bottom right in 100% of the frames"))
    plan = pipeline.plan(cfg, ledger, NOW, quiet)
    assert plan["clips"] == [] and "streamer facecam" in plan["rejected"][0]["reasons"][0]
    src = next(iter(ledger.sources.values()))
    assert src["status"] == "skipped" and src["reason"].startswith("streamer facecam")
    assert "Left out: not gameplay-only" in report.build(cfg, ledger, None, NOW)
    assert pipeline.plan(cfg, ledger, NOW, quiet)["rejected"] == []  # it is not looked at again


def test_footage_with_commentary_is_left_out(cfg, ledger, footage, monkeypatch):
    from clipper import presenter

    monkeypatch.setattr(presenter, "talk", lambda cfg, text: ["chat", "this game"])
    plan = pipeline.plan(cfg, ledger, NOW, lambda m: None)
    assert plan["clips"] == [] and "talking over the game" in plan["rejected"][0]["reasons"][0]
    cfg.filters.no_commentary = False
    ledger.sources.clear()
    assert len(pipeline.plan(cfg, ledger, NOW, lambda m: None)["clips"]) == 1  # with the filter off the same footage is used


def test_planned_clips_come_with_a_frame_strip(cfg, ledger, footage):
    plan = pipeline.plan(cfg, ledger, NOW, lambda m: None)
    sheet = plan["clips"][0]["sheet"]
    info = media.probe(__import__("pathlib").Path(sheet))
    assert info.width == 1600 and 220 <= info.height <= 230  # four 400-wide frames side by side


def test_quiet_short_guide_gives_its_last_stretch(cfg):
    """A few minutes of calm footage with no standout moment: the ending is used."""
    rate = 16000
    wav = cfg.root / "guide.wav"
    sf.write(wav, np.random.default_rng(8).normal(0, 0.01, rate * 100).astype(np.float32), rate)
    video = cfg.root / "guide.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=15:duration=100", "-i", str(wav),
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(video)], check=True)
    found = pipeline.analyse(cfg, video, "guide")
    assert not found.whole and len(found.cands) == 1
    assert (found.cands[0].start, found.cands[0].end) == (53.0, 98.0)


def test_odd_shaped_footage_still_renders_square_pixels(cfg, ledger):
    """A source whose shape does not divide evenly (1400x1080) must not leave the clip with stretched pixels."""
    from clipper import captions, render

    video = cfg.root / "odd.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=1400x1080:rate=30:duration=6", "-f", "lavfi", "-i", "sine=frequency=300:duration=6",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(video)], check=True)
    ass = cfg.root / "odd.ass"
    ass.write_text(captions.build(cfg, [], 6.0), encoding="utf-8")
    for layout in ("blur", "crop"):
        cfg.render.layout = layout
        out = media.probe(render.render(cfg, video, 0.0, 6.0, ass, cfg.root / f"odd-{layout}.mp4"))
        assert (out.width, out.height, out.pixel_shape) == (1080, 1920, "1:1"), layout
