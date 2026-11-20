"""The cut each platform gets: the rules, the cut around the moment, and what a platform is shown."""

from pathlib import Path

import pytest

from clipper import config, settings, variants


def clip(start=100.0, end=190.0, **more) -> dict:
    return {"id": "c1", "start": start, "end": end, **more}


def test_the_defaults_follow_each_platforms_rule(cfg):
    assert cfg.edits == {"tiktok": {"min_len": 61, "max_len": 0}, "youtube": {"min_len": 0, "max_len": 59},
                         "facebook": {"min_len": 0, "max_len": 90}, "instagram": {"min_len": 0, "max_len": 180}}
    # A clip of a minute and a half: TikTok (over a minute), Facebook (90 at most) and Instagram take it as it is; YouTube gets 59 seconds.
    found = variants.plan(cfg, clip(peak=150.0))
    assert found["unfit"] == {} and list(found["cuts"]) == ["youtube"]
    start, end = found["cuts"]["youtube"]
    assert end - start == 59 and start <= 150.0 <= end


def test_a_short_version_is_cut_around_the_moment_the_clip_builds_to(cfg):
    cfg.edits = {"youtube": {"min_len": 0, "max_len": 60}}
    assert variants.cut_for(cfg, clip(peak=150.0), "youtube")[0] == (126.0, 186.0)  # a little under half before the moment, the rest after
    assert variants.cut_for(cfg, clip(peak=105.0), "youtube")[0] == (100.0, 160.0)  # never before the clip starts
    assert variants.cut_for(cfg, clip(peak=188.0), "youtube")[0] == (130.0, 190.0)  # never past its end
    assert variants.cut_for(cfg, clip(), "youtube")[0] == (121.0, 181.0)  # no known moment: around the middle
    assert variants.cut_for(cfg, clip(peak=999.0), "youtube")[0] == (121.0, 181.0)  # a moment outside the clip is not believed


def test_a_cut_never_goes_through_a_spoken_word(cfg):
    cfg.edits = {"youtube": {"min_len": 0, "max_len": 60}}
    words = [{"w": "watch", "s": 125.7, "e": 126.4}, {"w": "this", "s": 185.8, "e": 186.3}]
    start, end = variants.cut_for(cfg, clip(peak=150.0, words=words), "youtube")[0]
    assert (start, end) == (126.4, 185.8) and end - start <= 60  # the words are given up, the limit is not


def test_a_clip_inside_the_rule_is_sent_as_it_is_and_one_too_short_is_not_sent(cfg):
    cfg.edits = {"tiktok": {"min_len": 61, "max_len": 0}, "youtube": {"min_len": 0, "max_len": 59}, "facebook": {"min_len": 0, "max_len": 90}}
    cfg.publish.platforms = ["tiktok", "youtube", "facebook", "instagram"]
    found = variants.plan(cfg, clip(0.0, 45.0))
    assert found["cuts"] == {} and found["unfit"] == {"tiktok": "the clip is 45 seconds; tiktok is set to take 61 or more"}
    assert variants.plan(cfg, clip(0.0, 90.4))["cuts"].keys() == {"youtube"}  # a fraction over Facebook's limit is not worth a second render
    assert variants.plan(cfg, clip(0.0, 61.0))["unfit"] == {}
    cfg.edits = {}
    assert variants.plan(cfg, clip(0.0, 300.0)) == {"cuts": {}, "unfit": {}}  # no rules, one version


def test_platforms_with_the_same_cut_share_one_file(cfg, tmp_path):
    cfg.edits = {"youtube": {"min_len": 0, "max_len": 59}, "instagram": {"min_len": 0, "max_len": 59}, "facebook": {"min_len": 0, "max_len": 75}}
    found = variants.plan(cfg, clip(peak=150.0))["cuts"]
    assert found["youtube"] == found["instagram"] != found["facebook"]
    base = tmp_path / "c1.mp4"
    assert variants.name_of(base, found["youtube"]) == tmp_path / "c1.59s.mp4" and variants.name_of(base, found["facebook"]) == tmp_path / "c1.75s.mp4"
    rec = clip(file=str(base), cuts={name: {"start": cut[0], "end": cut[1], "file": str(variants.name_of(base, cut))} for name, cut in found.items()},
               unfit={"tiktok": "too short"})
    assert variants.versions(rec, ["tiktok", "instagram", "youtube", "facebook", "x"]) == [
        {"file": str(base), "seconds": 90, "platforms": ["x"]},  # the clip's own version first
        {"file": str(tmp_path / "c1.59s.mp4"), "seconds": 59, "platforms": ["instagram", "youtube"]},
        {"file": str(tmp_path / "c1.75s.mp4"), "seconds": 75, "platforms": ["facebook"]}]


def test_what_a_platform_is_shown(tmp_path):
    rec = clip(file=str(tmp_path / "c1.mp4"), cover=40.0, cuts={"youtube": {"start": 126.0, "end": 185.0, "file": str(tmp_path / "c1.59s.mp4")}})
    shown, video = variants.view(rec, "tiktok")
    assert shown is rec and video == tmp_path / "c1.mp4"
    shown, video = variants.view(rec, "youtube")
    assert (shown["start"], shown["end"], shown["cover"], video) == (126.0, 185.0, 14.0, tmp_path / "c1.59s.mp4")
    assert rec["start"] == 100.0 and rec["cover"] == 40.0  # the record is not touched
    assert variants.view(dict(rec, cover=10.0), "youtube")[0]["cover"] is None  # the chosen frame is not in this version
    assert variants.view(dict(rec, cover=None), "youtube")[0]["cover"] is None


def test_the_rules_come_from_the_config_and_the_app(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPPER_DATA", str(tmp_path))
    path = tmp_path / "config.toml"
    path.write_text('[[sources]]\ntype = "folder"\npath = "inbox"\n\n[edits.youtube]\nmax_len = 180\n\n[edits.x]\nmax_len = 140\n', encoding="utf-8")
    cfg = config.load(str(path))
    assert cfg.edits["youtube"] == {"min_len": 0, "max_len": 180} and cfg.edits["tiktok"] == {"min_len": 61, "max_len": 0} and cfg.edits["x"] == {"max_len": 140}

    settings.update(tmp_path, {"edits": {"tiktok": {"min_len": 0, "max_len": 600}, "youtube": {"min_len": 0, "max_len": 59}}})  # saved from the app
    cfg = config.load(str(path))
    assert cfg.edits["tiktok"] == {"min_len": 0, "max_len": 600} and cfg.edits["youtube"]["max_len"] == 59 and cfg.edits["facebook"]["max_len"] == 90
    with pytest.raises(ValueError, match="each platform takes min_len and max_len"):
        settings.update(tmp_path, {"edits": {"tiktok": {"fps": 60}}})

    path.write_text('[[sources]]\ntype = "folder"\npath = "inbox"\n\n[edits.facebook]\nmin_len = 120\nmax_len = 90\n', encoding="utf-8")
    settings.save(tmp_path, {})
    with pytest.raises(config.ConfigError, match="edits.facebook: min_len and max_len are seconds"):
        config.load(str(path))
