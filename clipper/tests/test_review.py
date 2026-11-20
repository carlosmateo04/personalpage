"""The editor: what Claude is shown of one clip, and how its verdict and edit are applied.

Run against a local stand-in for the API; what a real model says about a real clip
is not tested here.
"""

import base64
import json

import pytest

from clipper import claude, cli, metadata, review
from conftest import speak
from standins import DROP, KEEP


def plan_with(cfg, n: int = 2):
    """A plan.json with n planned clips of 90 seconds, each with its rule-based title and its surroundings, as `plan` writes it."""
    run = cfg.path("out") / "20261120-000000"
    run.mkdir(parents=True)
    clips = []
    for i in range(n):
        cid = f"c{i}"
        sheet = run / f"{cid}-01.jpg"
        sheet.write_bytes(b"\xff\xd8\xff\xe0 pretend jpeg " + bytes([i]))
        start = 100.0 + 200.0 * i
        around = speak("Wait for it.", start - 10) + speak("Pull over! Pull over now!", start + 20) + speak("That was close.", start + 97)
        inside = [w for w in around if w.s >= start and w.e <= start + 90]
        clips.append({
            "id": cid, "source_name": "GTA 6 free roam (no commentary)", "creator": "RawPlay", "origin": "https://example.com/v",
            "section": "moments", "start": start, "end": start + 90, "whole": False,
            "text": " ".join(w.w for w in inside), "words": [{"w": w.w, "s": w.s, "e": w.e} for w in inside], "sheet": str(sheet),
            "context": {"from": start - 15, "to": start + 105, "words": [{"w": w.w, "s": w.s, "e": w.e} for w in around]},
            "meta": metadata.compose(cfg, cid, "RawPlay", cfg.section("moments"), "GTA 6 Highlight", "GTA 6 Highlight", "https://example.com/v"),
            "drop": False,
        })
    path = run / "plan.json"
    path.write_text(json.dumps({"run": "20261120-000000", "clips": clips, "rejected": [], "problems": []}), encoding="utf-8")
    return path


def clips_of(path):
    return json.loads(path.read_text(encoding="utf-8"))["clips"]


@pytest.fixture
def lengths(cfg):
    cfg.moments.min_len, cfg.moments.max_len = 85, 95
    return cfg


def test_what_the_editor_is_shown(lengths, claude_api):
    cfg = lengths
    cfg.ai.direction = "Chases first."
    clip = clips_of(plan_with(cfg, 1))[0]
    clip["scout"] = {"why": "A police car leaves the pier at speed."}
    claude_api.replies.append(dict(KEEP))
    verdict = review.ask(cfg, clip, "Start a little later.")
    assert verdict["keep"] is True and verdict["section"] == "glitches"
    body = claude_api.seen[0]["body"]
    assert body["model"] == "claude-opus-5-5" and body["output_config"]["effort"] == "low"
    shape = body["output_config"]["format"]["schema"]
    assert shape["required"] == ["keep", "reason", "start", "end", "section", "title", "hook", "summary", "tags", "callouts", "cover", "score"]
    assert shape["properties"]["callouts"]["items"]["required"] == ["at", "text"] and shape["additionalProperties"] is False
    assert "pure gameplay only" in body["system"] and "Never cut into a sentence" in body["system"]
    image, text = body["messages"][0]["content"]
    assert image["type"] == "image" and base64.standard_b64decode(image["source"]["data"]).startswith(b"\xff\xd8\xff")
    for expected in ("Game: GTA 6", "Title of the source video: GTA 6 free roam (no commentary)", "Channel: RawPlay",
                     "The clip as cut now: 1:40 to 3:10 (90 seconds).",
                     "Each end may move by up to 15 seconds, and the clip must stay between 85 and 95 seconds.",
                     '- "tricks" (Trick): things like: how to', "What the channel wants: Chases first.",
                     "[1:30] Wait for it.", "[2:00] Pull over!", "[3:17] That was close.",  # what is said around the clip too
                     "Why this stretch was picked: A police car leaves the pier at speed.",
                     "The channel owner asks for this change to the clip: Start a little later."):
        assert expected in text["text"], expected
    assert claude.usage(cfg)["by"] == {"edit": 1}

    whole = {**clip, "whole": True, "words": [], "context": None}
    whole.pop("scout")
    claude_api.replies.append(dict(KEEP))
    review.ask(cfg, whole)
    said = claude_api.seen[1]["body"]["messages"][0]["content"][1]["text"]
    assert "This is a short video used whole: leave start and end as they are." in said and "(nothing is said)" in said and "asks for this change" not in said


def test_a_kept_clip_gets_claudes_text_and_a_dropped_clip_is_marked(lengths, claude_api):
    cfg = lengths
    path = plan_with(cfg, 2)
    claude_api.replies += [dict(KEEP), dict(DROP)]
    lines = []
    assert review.apply(cfg, path, lines.append) == {"kept": 1, "dropped": 1, "failed": 0}
    kept, cut = clips_of(path)
    meta = kept["meta"]
    assert meta["title"] == "Police car launches off the pier" and meta["hook"] == "It just took off"
    assert kept["section"] == "glitches" and meta["badge"] == "GLITCH" and not kept["drop"]
    assert meta["youtube"]["title"] == "Police car launches off the pier | GTA 6 Glitch #shorts"
    assert "A police car hits a ramp and flies into the sea." in meta["youtube"]["description"]
    assert "Credit: RawPlay. Original: https://example.com/v" in meta["youtube"]["description"]
    for platform in ("tiktok", "instagram", "facebook"):  # every caption names the game and what people search for
        assert meta[platform]["title"].startswith("Police car launches off the pier | GTA 6 Glitch • Credit: RawPlay #gta6glitch #gta6bugs #gta6 ")
    # Claude's search phrases lead the YouTube tags: tidied, repeats and banned terms ("casino") removed
    assert meta["youtube"]["tags"][:3] == ["gta 6 police chase", "gta 6 pier jump", "gta 6"]
    assert metadata.proof(cfg, meta) == []
    assert kept["review"] == {"by": "claude-opus-5-5", "keep": True, "reason": KEEP["reason"], "score": 84, "summary": KEEP["summary"]}
    assert (kept["start"], kept["end"], kept["callouts"], kept["cover"]) == (100.0, 190.0, [], None)  # nothing asked for, nothing moved
    assert cut["drop"] is True and cut["review"]["keep"] is False and "webcam" in cut["review"]["reason"] and cut["review"]["score"] == 0
    assert cut["meta"]["title"] == "GTA 6 Highlight"  # untouched
    assert lines == ["  c0 kept [glitches, 84/100]: Police car launches off the pier",
                     "  c1 dropped: A webcam box with a person is visible in every frame.",
                     "review: 1 kept, 1 dropped, 0 could not be reviewed"]

    review.apply(cfg, path, lines.append)  # a second pass asks nothing again
    assert len(claude_api.seen) == 2


def test_the_cut_moves_where_claude_puts_it_within_the_limits(lengths, claude_api):
    cfg = lengths
    path = plan_with(cfg, 1)
    claude_api.replies.append({**KEEP, "start": "1:49", "end": "3:20"})  # nine seconds later at the start, ten at the end
    lines = []
    review.apply(cfg, path, lines.append)
    clip = clips_of(path)[0]
    assert (clip["start"], clip["end"]) == (109.0, 200.0) and clip["review"]["cut"] == [100.0, 190.0]
    assert clip["text"] == "Pull over! Pull over now! That was close." and clip["words"][-1]["w"] == "close."  # the words inside the new cut
    assert lines[0].endswith("(cut moved to 1:49-3:20)")

    def cut(clip_changes: dict, **answer):
        clip = {**clips_of(plan_with_fresh(cfg))[0], **clip_changes}
        return review._cut(cfg, clip, {**KEEP, **answer})

    def plan_with_fresh(cfg):
        import shutil

        shutil.rmtree(cfg.path("out"))
        return plan_with(cfg, 1)

    assert cut({}, start="1:40", end="3:10") is None and cut({}, start="1:41", end="3:09") is None  # the same cut, to the second
    assert cut({}, start="", end="") is None and cut({}, start="3:00", end="2:00") is None
    assert cut({}, start="0:30", end="3:10") == (85.0, 180.0)  # no more than the margin of 15 seconds, and no longer than 95
    assert cut({}, start="1:40", end="2:10") == (100.0, 185.0)  # no shorter than 85 seconds: the end is kept where the limit allows
    assert cut({"whole": True}, start="1:49", end="3:20") is None  # a short video used whole is not trimmed
    cfg.ai.margin = 0
    assert cut({}, start="1:49", end="3:20") is None or cut({}, start="1:49", end="3:20") == (100.0, 190.0)


def test_on_screen_notes_must_fit_and_stay_clear_of_speech(lengths):
    cfg = lengths
    words = speak("Pull over! Pull over now!", 120.0)  # spoken from 2:00 to about 2:01.5
    notes = review._callouts(cfg, {"callouts": [
        {"at": "1:45", "text": "  Six stars  "},
        {"at": "2:00", "text": "Over the speech"},
        {"at": "1:47", "text": "Too soon after the first"},
        {"at": "2:30", "text": "This note is far too long to read in the time it is shown"},
        {"at": "3:09", "text": "Too near the end"},
        {"at": "0:50", "text": "Before the clip"},
        {"at": "2:40", "text": "Casino run"},
        {"at": "soon", "text": "No time"},
        {"at": "2:50", "text": "Watch the helicopter"},
        {"at": "3:00", "text": "Here it comes"},
        {"at": "2:20", "text": "A fourth is one too many"},
    ]}, 100.0, 190.0, words)
    assert notes == [{"at": 5.0, "text": "Six stars"}, {"at": 70.0, "text": "Watch the helicopter"}, {"at": 80.0, "text": "Here it comes"}]
    cfg.ai.callouts = False
    assert review._callouts(cfg, {"callouts": [{"at": "1:45", "text": "Six stars"}]}, 100.0, 190.0, []) == []


def test_notes_cover_and_score_are_written_into_the_clip(lengths, claude_api):
    cfg = lengths
    path = plan_with(cfg, 1)
    claude_api.replies.append({**KEEP, "callouts": [{"at": "1:45", "text": "Six stars"}], "cover": "2:25", "score": 250})
    lines = []
    review.apply(cfg, path, lines.append)
    clip = clips_of(path)[0]
    assert clip["callouts"] == [{"at": 5.0, "text": "Six stars"}] and clip["cover"] == 45.0 and clip["review"]["score"] == 100
    assert lines[0] == "  c0 kept [glitches, 100/100]: Police car launches off the pier (1 on-screen note(s))"
    for cover in ("0:10", "9:59", "", "soon"):  # a cover outside the clip is no cover
        assert review.edit(cfg, clips_of(path)[0] | {"review": None}, {**KEEP, "cover": cover}) and True
        probe = clips_of(path)[0]
        review.edit(cfg, probe, {**KEEP, "cover": cover})
        assert probe["cover"] is None


def test_a_rejected_key_stops_the_editing_and_the_rule_based_titles_stay(lengths, claude_api, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-wrong")
    path = plan_with(lengths, 2)
    assert review.apply(lengths, path, lambda m: None) == {"kept": 0, "dropped": 0, "failed": 1} and len(claude_api.seen) == 1  # the second clip is not tried
    data = json.loads(path.read_text(encoding="utf-8"))
    assert all(c["meta"]["title"] == "GTA 6 Highlight" and not c["drop"] and "review" not in c for c in data["clips"])
    assert "key was not accepted" in data["notes"][0] and data["problems"] == []  # worth knowing, but the run has not failed


@pytest.mark.parametrize("reply, said", [
    ({"_stop": "refusal"}, "declined"),
    ({**KEEP, "_stop": "max_tokens"}, "cut off"),
    ("this is not json", "expected form"),
    ({"keep": "yes"}, "expected form"),
    (400, "did not accept the request"),
])
def test_an_unusable_answer_leaves_that_clip_alone_and_moves_on(lengths, claude_api, reply, said):
    path = plan_with(lengths, 2)
    claude_api.replies += [reply, dict(KEEP)]
    assert review.apply(lengths, path, lambda m: None) == {"kept": 1, "dropped": 0, "failed": 1}
    first, second = clips_of(path)
    assert first["meta"]["title"] == "GTA 6 Highlight" and not first["drop"] and "review" not in first
    assert second["meta"]["title"] == "Police car launches off the pier"
    assert said in json.loads(path.read_text(encoding="utf-8"))["notes"][0]


def test_an_empty_or_unknown_answer_falls_back_sensibly(lengths, claude_api):
    path = plan_with(lengths, 1)
    claude_api.replies.append({**KEEP, "title": "  ", "hook": "", "section": "somewhere", "score": "high", "tags": "none"})
    review.apply(lengths, path, lambda m: None)
    clip = clips_of(path)[0]
    assert clip["meta"]["title"] == "GTA 6 Highlight" and clip["meta"]["hook"] == "GTA 6 Highlight" and clip["section"] == "moments"
    assert clip["review"]["score"] == 0


def test_why_the_editor_is_off(cfg, monkeypatch, capsys):
    assert "no Claude API key" in review.unavailable(cfg) and not review.available(cfg)
    path = plan_with(cfg, 1)
    before = path.read_text(encoding="utf-8")
    cli._review(cfg, path)
    assert "Claude is not editing the clips: no Claude API key" in capsys.readouterr().out and path.read_text(encoding="utf-8") == before
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-anything")
    assert review.available(cfg)
    cfg.ai.edit = False
    assert review.unavailable(cfg) == "editing by Claude is switched off in the settings"
