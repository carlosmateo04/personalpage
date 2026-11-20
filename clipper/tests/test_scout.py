"""The scout: what Claude is shown of a whole video, and what becomes of its picks."""

import subprocess
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from clipper import claude, media, moments, scout
from conftest import speak


def pick(start, end, score=70, title="Tank drives through the roadblock", section="moments", **more):
    return {"start": start, "end": end, "section": section, "title": title, "hook": "Straight through", "why": "A tank flattens a police roadblock.",
            "score": score, **more}


def test_times_read_both_ways():
    assert [media.clock(t) for t in (0, 59.6, 425, 3600, 3723)] == ["0:00", "1:00", "7:05", "1:00:00", "1:02:03"]
    assert [media.seconds(t) for t in ("7:05", "1:02:03", "0:07", 90, "90", " 12s ")] == [425.0, 3723.0, 7.0, 90.0, 90.0, 12.0]
    assert [media.seconds(t) for t in ("", "soon", "-3", "1:2:3:4", None, True)] == [None] * 6


def test_picks_are_fitted_to_the_clip_length_and_kept_apart(cfg):
    cfg.moments.min_len, cfg.moments.max_len, cfg.moments.max_per_source = 85, 95, 3
    words = speak("Go go go! They are right behind us.", start=335.0)
    answer = {"usable": True, "problem": "", "moments": [
        pick("5:00", "5:40", 60),                      # shorter than a clip: runs on to 85 seconds
        pick("1:00", "3:30", 90, title="six-star chase ends on the beach."),  # longer than a clip: the best part from its start
        pick("5:30", "7:00", 80, title="Overlaps the first pick but is stronger"),
        pick("9:30", "9:50", 99, title="Too close to the end of the video"),
        pick("soon", "later", 95),                     # not times
        pick("12:00", "13:00", 95),                    # past the end of the video
        pick("8:00", "8:30", 85, title="No"),          # no usable title
        pick("3:10", "3:40", "high", title="A score that is not a number counts as zero"),
    ]}
    found = scout.candidates(cfg, answer, words, 600.0, limit=5)
    assert found.usable and [(c.start, c.end, c.ai["score"]) for c in found.cands] == [(60.0, 155.0, 90), (330.0, 420.0, 80), (190.0, 275.0, 0)]
    best = found.cands[0]
    assert best.ai == {"title": "Six-star chase ends on the beach", "hook": "Straight through", "section": "moments",
                       "why": "A tank flattens a police roadblock.", "score": 90}
    assert best.score == 5.5 and found.cands[2].score == 1.0  # 0 to 100 becomes 1 to 6, the finder's own scale
    assert found.cands[0].words == [] and [w.w for w in found.cands[1].words][:3] == ["Go", "go", "go!"]  # each cut carries the words inside it
    reasons = " | ".join(found.skipped)
    for expected in ("5:00-5:40: overlaps a stronger pick", "9:30-9:50: too close to the end to make a full clip", "soon-later: not a stretch of this video",
                     "12:00-13:00: not a stretch of this video", "8:00-8:30: no usable title"):
        assert expected in reasons, expected
    assert len(scout.candidates(cfg, answer, words, 600.0, limit=1).cands) == 1


def test_a_pick_does_not_cut_through_a_spoken_word(cfg):
    cfg.moments.min_len, cfg.moments.max_len = 20, 30
    words = speak("one two three four five six seven eight nine ten", start=99.0, pace=0.5)  # a word runs from 100.0 to 100.4
    found = scout.candidates(cfg, {"usable": True, "problem": "", "moments": [pick("1:40", "2:05")]}, words, 300.0, limit=3)
    start, end = found.cands[0].start, found.cands[0].end
    assert not any(w.s < start < w.e or w.s < end < w.e for w in words) and 20 <= end - start <= 31


def test_a_video_that_cannot_be_used_gives_no_picks(cfg):
    found = scout.candidates(cfg, {"usable": False, "problem": "A webcam box is on screen throughout.", "moments": [pick("1:00", "2:30")]}, [], 600.0, 3)
    assert not found.usable and found.problem == "A webcam box is on screen throughout." and found.cands == []


@pytest.fixture
def video(tmp_path):
    """A minute of test picture with a tone that gets loud at 0:30."""
    rate = 16000
    tone = (np.sin(np.arange(rate * 60) * 2 * np.pi * 330 / rate) * 0.02).astype(np.float32)
    tone[30 * rate:33 * rate] *= 30
    sf.write(tmp_path / "tone.wav", tone, rate)
    path = tmp_path / "GTA 6 tank rampage.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=10:duration=60", "-i", str(tmp_path / "tone.wav"),
                    "-c:v", "libx264", "-preset", "ultrafast", "-g", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)], check=True)
    return path


def test_a_storyboard_shows_the_whole_video_with_the_time_on_each_frame(video, tmp_path):
    import cv2

    boards = media.storyboard(video, 60.0, 5.0, tmp_path / "boards", "v", cols=5, rows=2)
    assert [(b["frames"], b["from"], b["to"]) for b in boards] == [(10, 0.0, 45.0), (2, 50.0, 55.0)]  # one frame per five seconds of a minute
    first = cv2.imread(boards[0]["path"]).astype(int)
    assert first.shape[:2] == (540, 2400)  # two rows of five 480 by 270 frames
    tiles = [first[r * 270:(r + 1) * 270, c * 480:(c + 1) * 480] for r in range(2) for c in range(5)]
    assert all(abs(a - b).mean() > 2 for a, b in zip(tiles, tiles[1:]))  # ten different moments
    assert all(tile[2:20, 2:8].mean() < 40 for tile in tiles)  # the black label box in each top-left corner
    assert [p.name for p in sorted((tmp_path / "boards").iterdir())] == ["v-01.jpg", "v-02.jpg"]  # nothing else is left behind

    exact = media.sheets(video, [10.0, 20.0, 30.0, 40.0, 50.0], tmp_path / "boards", "clip", cols=4, rows=2)  # frames fetched one by one
    assert len(exact) == 1 and exact[0]["frames"] == 5 and cv2.imread(exact[0]["path"]).shape[:2] == (540, 1920)
    with pytest.raises(media.MediaError):
        media.sheets(tmp_path / "missing.mp4", [1.0], tmp_path / "boards", "none")


def test_what_the_scout_is_shown_and_what_comes_back(cfg, claude_api, video, tmp_path):
    cfg.moments.min_len, cfg.moments.max_len, cfg.moments.max_per_source = 20, 30, 2
    cfg.ai.direction = "Tanks, please."
    wav = media.extract_audio(video, tmp_path / "v.wav")
    words = speak("Hold on, here they come!", start=29.0)
    detector = [moments.Candidate(25.0, 50.0, 31.0, 2.4)]
    claude_api.replies.append({"usable": True, "problem": "", "moments": [pick("0:28", "0:50", 88)]})
    lines = []
    found = scout.find(cfg, video, "GTA 6 tank rampage (no commentary)", "RawPlay", 60.0, words, wav, detector, tmp_path / "boards", "v", lines.append)
    assert [(c.start, c.end, c.ai["title"]) for c in found.cands] == [(28.0, 50.0, "Tank drives through the roadblock")]
    assert [w.w for w in found.cands[0].words] == ["Hold", "on,", "here", "they", "come!"]
    assert lines == ["  Claude is looking at the whole video (12 frames)"]

    body = claude_api.seen[0]["body"]
    assert body["output_config"]["effort"] == "medium" and "pure gameplay only" in body["system"]
    assert body["output_config"]["format"]["schema"]["properties"]["moments"]["items"]["properties"]["section"]["enum"] == ["findings", "tricks", "glitches", "moments"]
    label, image, brief = body["messages"][0]["content"]
    assert label == {"type": "text", "text": "Frames from 0:00 to 0:55:"} and image["type"] == "image"
    text = brief["text"]
    for expected in ("Video title: GTA 6 tank rampage (no commentary)", "Channel: RawPlay", "Game: GTA 6", "Length: 1:00. The sheets above show one frame every 5 seconds.",
                     "[0:29] Hold on, here they come!", "What the loudness detector would clip: 0:25-0:50", '- "glitches" (Glitch): things like: glitch',
                     "What the channel wants: Tanks, please.", "Each clip must run between 20 and 30 seconds. Pick at most 4 moments."):
        assert expected in text, expected
    assert "Where the sound stands out (time, strength): 0:3" in text  # the burst at half a minute
    assert claude.usage(cfg)["by"] == {"scout": 1}
    assert not list((tmp_path / "boards").glob("*.jpg"))  # the sheets were only for the request

    assert scout.said([]) == "(nothing is said)"
    assert [scout.step_for(cfg, d) for d in (60, 571.7, 1800)] == [5.0, 6.0, 18.0]
