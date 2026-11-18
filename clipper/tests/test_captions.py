import re

from clipper import captions
from clipper.transcribe import Word
from conftest import speak


def test_time_and_colour_formats():
    assert captions.ass_time(0) == "0:00:00.00"
    assert captions.ass_time(75.239) == "0:01:15.24"
    assert captions.ass_color("#FFE600") == "&H0000E6FF"  # blue, green, red


def test_chunks_respect_word_and_width_limits(cfg):
    words = speak("no way he just drove the bus straight off the bridge")
    groups = captions.chunks(words, cfg.captions, 10.0)
    assert all(len(g.words) <= cfg.captions.max_words for g in groups)
    assert all(sum(len(w.w) for w in g.words) + len(g.words) - 1 <= cfg.captions.max_chars or len(g.words) == 1 for g in groups)
    assert [w.w for g in groups for w in g.words] == [w.w for w in words]
    assert all(a.end <= b.start + 1e-6 for a, b in zip(groups, groups[1:]))  # never two lines at once


def test_chunks_break_on_sentence_end_and_pause(cfg):
    words = [Word("Wait.", 0.0, 0.3), Word("What", 0.4, 0.6), Word("is", 0.6, 0.7), Word("that", 2.0, 2.3)]
    groups = captions.chunks(words, cfg.captions, 5.0)
    assert [[w.w for w in g.words] for g in groups] == [["Wait."], ["What", "is"], ["that"]]


def test_highlight_marks_exactly_one_word_per_event(cfg):
    words = speak("oh my god did you see that")
    ass = captions.build(cfg, words, 5.0)
    events = [line for line in ass.splitlines() if line.startswith("Dialogue") and ",Karaoke," in line]
    assert len(events) == len(words)
    assert all(line.count(r"\c&H00E6FF&") == 1 for line in events)
    assert "OH" in events[0] and "oh" not in events[0]  # upper-cased


def test_sweep_timings_cover_each_word(cfg):
    cfg.captions.mode = "sweep"
    words = speak("clip that right now", pace=0.5)
    ass = captions.build(cfg, words, 5.0)
    fills = [int(n) for n in re.findall(r"\\kf(\d+)", ass)]
    assert fills == [40, 40, 40, 40]  # 0.4 s per word, in centiseconds


def test_label_hook_and_profanity(cfg):
    words = speak("what the fuck was that.")
    ass = captions.build(cfg, words, 5.0, hook="He drove the bus off the bridge", label="Paid partnership")
    assert "Paid partnership" in ass and "HE DROVE THE BUS OFF THE" in ass
    assert "F***" in ass and "FUCK" not in ass
    assert "THAT." not in ass  # trailing full stop dropped
