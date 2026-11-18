import numpy as np
import soundfile as sf

from clipper import captions, gates, metadata, moments, pipeline, sections
from clipper.transcribe import Word
from conftest import speak


def test_clips_are_sorted_into_sections(cfg):
    pick = lambda spoken, title="", hint="": sections.classify(cfg, spoken, title, hint).name
    assert pick("there is a hidden easter egg behind the secret door") == "findings"
    assert pick("the fastest way to make money is this trick") == "tricks"
    assert pick("the car just fell through the map what a glitch") == "glitches"
    assert pick("no way he drove off the bridge") == "moments"  # nothing specific: the catch-all
    assert pick("no way he drove off the bridge", hint="tricks") == "tricks"  # the source's section breaks the tie
    assert pick("oh my god look at that", title="GTA 6 glitch launches car into space") == "glitches"  # titles count double
    assert pick("this glitch is a bug and it is broken", hint="tricks") == "glitches"  # clear content beats the hint


def test_badge_and_credit_are_drawn(cfg):
    ass = captions.build(cfg, speak("look at this"), 4.0, label="Paid partnership", badge="GLITCH", badge_color="#7CFF4F", credit="Credit: KaiCenat")
    badge = next(line for line in ass.splitlines() if ",Badge," in line)
    assert badge.endswith("GLITCH") and r"\3c&H4FFF7C&" in badge and r"\c&H000000&" in badge  # green box, dark text
    label = next(line for line in ass.splitlines() if ",Label," in line)
    assert r"\an9" in label  # the paid notice moves to the right when a badge is shown
    assert any(",Handle," in line and line.endswith("Credit: KaiCenat") for line in ass.splitlines())
    plain = captions.build(cfg, speak("look at this"), 4.0)
    assert ",Badge," not in plain.split("[Events]")[1] and ",Handle," not in plain.split("[Events]")[1]


def test_metadata_credits_the_source_and_uses_the_section(cfg):
    cfg.campaign.paid = False
    words = speak("okay chat. look at this hidden easter egg behind the door! that is crazy.")
    cand = moments.Candidate(0.0, 30.0, 15.0, 3.0, words)
    section = cfg.section("findings")
    meta = metadata.build(cfg, cand, "clip9", "KaiCenat", section, "", "https://www.twitch.tv/kaicenat/clip/abc")
    assert meta["section"] == "findings" and meta["badge"] == "NEW FINDING" and meta["credit"] == "Credit: KaiCenat"
    for platform in ("tiktok", "instagram", "facebook"):
        assert "Credit: KaiCenat" in meta[platform]["title"] and "#gta6secrets" in meta[platform]["title"]
    assert "Original: https://www.twitch.tv/kaicenat/clip/abc" in meta["youtube"]["description"]
    assert "easter egg" in meta["title"].lower()  # the sentence with the section's cue words wins
    titled = metadata.build(cfg, cand, "clip9", "KaiCenat", section, "kai finds bigfoot in gta 6.", "")
    assert "Kai finds bigfoot in gta 6" in titled["title"]  # a clip's own title is used when it has one
    assert "(GTA 6)" not in titled["title"] and titled["youtube"]["title"].count("GTA 6") == 0  # the game is not repeated


def test_short_clips_used_whole_have_their_own_limits(cfg, ledger):
    short = moments.Candidate(0.0, 12.0, 6.0, 3.0, speak("wtf is gta bro look"))
    assert gates.check(cfg, short, "s", ledger, whole=True) == []
    assert any("too short" in r for r in gates.check(cfg, short, "s", ledger))  # as a cut from a long video it would not pass
    mute = moments.Candidate(0.0, 12.0, 6.0, 3.0, speak("bro"))
    assert any("too little speech" in r for r in gates.check(cfg, mute, "s", ledger, whole=True))
    long = moments.Candidate(0.0, 90.0, 45.0, 3.0, speak("wtf is gta bro look at that"))
    assert any("too long" in r for r in gates.check(cfg, long, "s", ledger, whole=True))


def test_sections_take_turns(cfg):
    def entry(section, score):
        return {"cand": moments.Candidate(0, 20, 10, score, []), "section": cfg.section(section), "item": None, "whole": True}

    pool = [entry("moments", 5), entry("moments", 4), entry("moments", 3), entry("glitches", 2), entry("findings", 1)]
    order = [(e["section"].name, e["cand"].score) for e in pipeline._by_turns(pool)]
    assert order == [("moments", 5), ("glitches", 2), ("findings", 1), ("moments", 4), ("moments", 3)]


def test_calm_explanation_with_cue_words_is_found(cfg, tmp_path):
    """A quiet tutorial passage is picked up from its cue words alone, with no shouting."""
    rate = 16000
    wav = tmp_path / "calm.wav"
    sf.write(wav, np.random.default_rng(3).normal(0, 0.01, rate * 120).astype(np.float32), rate)
    words = []
    for t in range(0, 48, 6):
        words += speak("we are just driving around the city for now.", t, 0.4)
    words += speak("here is a trick most people miss.", 50.0, 0.4)
    words += speak("the fastest way to unlock the garage is this shortcut.", 53.5, 0.4)
    words += speak("all you have to do is park here and wait.", 58.5, 0.4)
    words += speak("that is the easiest way to do it.", 63.0, 0.4)
    for t in range(70, 116, 6):
        words += speak("okay let me check the map again.", t, 0.4)
    found = moments.find(cfg.moments, wav, words, 120.0, cues=sections.all_cues(cfg))
    assert len(found) == 1 and found[0].start <= 51 and found[0].end >= 62
    assert sections.classify(cfg, found[0].text).name == "tricks"
    assert moments.find(cfg.moments, wav, words, 120.0) == []  # without the section cues it is not a moment


def test_titles_keep_names_as_written_and_do_not_repeat_the_game(cfg):
    cand = moments.Candidate(0.0, 30.0, 15.0, 3.0, speak("okay chat look at this."))
    section = cfg.section("moments")
    for clip_id in ("a", "b", "c", "d", "e", "f"):  # every wording variant
        meta = metadata.build(cfg, cand, clip_id, "xQc", section, "xQc plays gta6 for the first time", "")
        assert "xQc plays gta6 for the first time" in meta["title"] and "XQc" not in meta["title"]
        assert "(GTA 6)" not in meta["title"] and "| GTA 6" not in meta["youtube"]["title"]
        assert not meta["title"].startswith("xQc: xQc")
    lower = metadata.build(cfg, cand, "a", "KaiCenat", section, "kai bank", "")
    assert "Kai bank" in lower["title"]
