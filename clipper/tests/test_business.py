"""The business around the clips: money, progress to being paid, what works, the campaign desk, the rights record,
backups and updates, alerts, channels and the way to going live."""

import json
import zipfile
from datetime import date, datetime, timedelta, timezone

import pytest

from clipper import alerts, channels, config, desk, launch, learn, money, pipeline, progress, publish, rights, settings, store, upkeep
from clipper.state import Ledger

NOW = datetime(2026, 11, 20, 15, 0, tzinfo=timezone.utc)


def clip(ledger, cid: str, section: str = "moments", views: dict | None = None, days_ago: int = 5, creator: str = "RawPlay", **more) -> dict:
    """A clip that went out `days_ago` days ago, with its views per platform."""
    when = NOW - timedelta(days=days_ago)
    posts = {name: {"state": "posted", "via": "direct", "id": f"{name}-{cid}", "link": f"https://{name}.example/{cid}", "views": count, "sent": when.isoformat()}
             for name, count in (views or {}).items()}
    ledger.clips[cid] = {"id": cid, "status": "posted" if posts else "held", "title": f"Clip {cid}", "section": section, "creator": creator, "permission": "",
                         "slot": when.isoformat(), "start": 0.0, "end": 90.0, "posts": posts, "views": dict(views or {}), "file": f"{cid}.mp4", **more}
    return ledger.clips[cid]


# --- money -------------------------------------------------------------------------------

def test_money_in_and_out_with_what_is_worked_out(cfg):
    assert money.summary(cfg)["total"] == {"income": 0.0, "cost": 0.0, "net": 0.0}
    paid = money.add(cfg, "income", "  TikTok   rewards ", 41.5, "2026-11-12", "October payout")
    money.add(cfg, "cost", "Microphone", 60, "2026-10-03")
    assert paid["source"] == "TikTok rewards" and len(paid["id"]) == 10
    store.write(cfg, "ai_usage", {"2026-11": {"requests": 40, "cost": 3.2}})  # what Claude's count of requests comes to
    cfg.money.monthly_costs = [{"name": "Posting service", "amount": 24, "since": "2026-10"}]
    found = money.summary(cfg, date(2026, 11, 20))
    assert found["total"] == {"income": 41.5, "cost": 111.2, "net": -69.7}
    assert [(m["month"], m["income"], m["cost"], m["net"]) for m in found["months"]] == [("2026-11", 41.5, 27.2, 14.3), ("2026-10", 0.0, 84.0, -84.0)]
    assert found["months"][0]["by"] == {"TikTok rewards": 41.5, "Claude": -3.2, "Posting service": -24.0}
    assert found["years"] == [{"year": "2026", "income": 41.5, "cost": 111.2, "net": -69.7}]
    worked = [e for e in found["entries"] if e.get("auto")]
    assert {e["source"] for e in worked} == {"Claude", "Posting service"} and len(worked) == 3  # never stored, always worked out

    sheet = money.as_csv(cfg, "2026").splitlines()
    assert sheet[0] == "date,kind,source,amount (USD),note,worked out" and "2026-11-12,income,TikTok rewards,41.50,October payout," in sheet
    assert "2026-11-28,cost,Claude,3.20,\"40 request(s), estimated at list price\",yes" in sheet and money.as_csv(cfg, "2025").count("\n") == 1

    money.remove(cfg, paid["id"])
    assert money.summary(cfg, date(2026, 11, 20))["total"]["income"] == 0.0
    with pytest.raises(KeyError):
        money.remove(cfg, "claude:2026-11")  # what is worked out cannot be removed
    for bad, said in (({"kind": "gift"}, "income or a cost"), ({"amount": "lots"}, "has to be a number"), ({"amount": 0}, "more than zero"),
                      ({"when": "20/11/2026"}, "has to look like"), ({"source": " "}, "where the money")):
        with pytest.raises(ValueError, match=said):
            money.add(cfg, **{"kind": "income", "source": "x", "amount": 5, **bad})
    money.add(cfg, "income", "YouTube", 9.0, "2026-11-28", key="youtube:2026-11")
    money.add(cfg, "income", "YouTube", 12.25, "2026-11-28", key="youtube:2026-11")  # a keyed entry replaces itself
    assert [e["amount"] for e in money.entries(cfg) if e["id"] == "youtube:2026-11"] == [12.25]


# --- rights ------------------------------------------------------------------------------

def test_permissions_claims_and_the_block_that_follows(cfg, ledger):
    rights.permit(cfg, "RawPlay", "clip policy on their channel page, read 20 Nov 2026")
    assert rights.permission_for(cfg, "rawplay ") == "clip policy on their channel page, read 20 Nov 2026" and rights.permission_for(cfg, "Other") == ""
    with pytest.raises(ValueError):
        rights.permit(cfg, "RawPlay", " ")
    a, b = clip(ledger, "c1", views={"youtube": 10}), clip(ledger, "c2", views={"youtube": 10})
    assert rights.is_blocked(cfg, "Rockstar Games") == "on the list of channels never taken from" and rights.is_blocked(cfg, "RawPlay") == ""

    first = rights.add_event(cfg, ledger, "claim", "youtube", "c1", "Content ID: radio track")
    assert (first["channel"], first["blocked"], first["title"]) == ("RawPlay", False, "Clip c1") and a["rights"][0]["kind"] == "claim"
    second = rights.add_event(cfg, ledger, "claim", "youtube", "c2")
    assert second["blocked"] and rights.is_blocked(cfg, "RAWPLAY") == "blocked after 2 copyright claims"  # the second claim on one channel's footage
    found = rights.record(cfg)
    assert [e["clip"] for e in found["events"]] == ["c2", "c1"] and found["blocked"][0]["channel"] == "RawPlay" and found["permissions"][0]["channel"] == "RawPlay"
    rights.unblock(cfg, "RawPlay")
    assert rights.is_blocked(cfg, "RawPlay") == ""

    clip(ledger, "c3", creator="Streamer", views={"tiktok": 5})
    assert rights.add_event(cfg, ledger, "strike", "tiktok", "c3")["blocked"] and "a strike on tiktok" in rights.is_blocked(cfg, "Streamer")  # one strike is enough
    mine = clip(ledger, "c4", creator="", views={"youtube": 5}, permission="own footage")
    assert not rights.add_event(cfg, ledger, "takedown", "youtube", "c4")["blocked"] and mine["rights"]  # own footage is on record, nothing to block
    rights.remove_event(cfg, first["id"])
    assert first["id"] not in [e["id"] for e in rights.record(cfg)["events"]]
    with pytest.raises(ValueError):
        rights.add_event(cfg, ledger, "rumour", "youtube", "c1")


def test_a_post_rejected_for_copyright_goes_on_record_by_itself_and_blocked_footage_is_not_sent(cfg, ledger, tmp_path):
    rec = clip(ledger, "c1", views={"youtube": 10})
    assert rights.notice(cfg, ledger, rec, "youtube", "YouTube could not process the video (codec)") == {}  # not about rights
    event = rights.notice(cfg, ledger, rec, "youtube", "YouTube rejected the video: a copyright claim")
    assert (event["kind"], event["auto"], event["blocked"]) == ("takedown", True, True)
    assert rights.notice(cfg, ledger, rec, "youtube", "YouTube rejected the video: a copyright claim") == {}  # once

    from clipper import outbox
    waiting = clip(ledger, "c9")
    (tmp_path / "c9.mp4").write_bytes(b"video")
    waiting["file"] = str(tmp_path / "c9.mp4")
    with pytest.raises(publish.PublishError, match="nothing more of RawPlay is posted: the channel is blocked after a takedown on youtube"):
        outbox.enqueue(cfg, ledger, waiting, None)


# --- what works ----------------------------------------------------------------------------

def test_results_steer_the_ranking_once_there_is_enough_to_go_on(cfg, ledger):
    for n in range(5):
        clip(ledger, f"g{n}", "glitches", {"youtube": 9000 + n * 100, "tiktok": 1000})
        clip(ledger, f"m{n}", "moments", {"youtube": 900 + n * 10})
    clip(ledger, "t0", "tricks", {"youtube": 50_000})  # one clip says nothing about a section
    clip(ledger, "new", "moments", {"youtube": 1_000_000}, days_ago=1)  # out for a day: not counted yet
    found = learn.stats(cfg, ledger, NOW)
    assert found["clips"] == 11 and found["enough"] and [s["name"] for s in found["sections"]] == ["tricks", "glitches", "moments"]
    assert found["sections"][1] == {"name": "glitches", "clips": 5, "typical": 10200, "best": 10400} and found["top"][0]["title"] == "Clip t0"
    weights = learn.weights(cfg, ledger, NOW)
    assert set(weights) == {"glitches", "moments"} and weights["glitches"] == 1.3 and weights["moments"] == 0.75  # bounded both ways

    ledger.save()
    told = learn.brief(cfg, NOW)
    assert "What has done best on this channel so far" in told and "- Clip t0 [tricks]: 50,000" in told and "glitches 10,200 (5 clips)" in told

    class Cand:
        def __init__(self, score):
            self.score = score
    pool = [{"cand": Cand(2.0), "section": cfg.section("moments"), "id": "moment"}, {"cand": Cand(1.6), "section": cfg.section("glitches"), "id": "glitch"}]
    assert [e["id"] for e in pipeline._by_turns(pool)] == ["moment", "glitch"]
    assert [e["id"] for e in pipeline._by_turns(pool, weights)] == ["glitch", "moment"]  # 1.6 x 1.3 beats 2.0 x 0.75

    cfg.learn.enabled = False
    assert learn.weights(cfg, ledger, NOW) == {} and learn.brief(cfg, NOW) == ""


def test_too_few_results_steer_nothing(cfg, ledger):
    clip(ledger, "a", "glitches", {"youtube": 9000})
    clip(ledger, "b", "moments", {"youtube": 10})
    ledger.save()
    assert learn.weights(cfg, ledger, NOW) == {} and learn.brief(cfg, NOW) == "" and not learn.stats(cfg, ledger, NOW)["enough"]


# --- campaign desk ---------------------------------------------------------------------------

def test_the_desk_follows_each_post_owed_to_a_campaign_and_a_payout_lands_in_the_money_record(cfg, ledger):
    clip(ledger, "c1", views={"tiktok": 60_000, "youtube": 2_000})
    assert desk.rows(cfg, ledger) == [] and not desk.active(cfg)  # no campaign: nothing is owed
    cfg.campaign.name, cfg.campaign.rate_per_1k, cfg.campaign.max_payout, cfg.campaign.min_payout = "GTA 6 launch", 0.7, 35.0, 1.0
    cfg.campaign.counted_platforms = ["tiktok", "youtube"]
    rows = {r["platform"]: r for r in desk.rows(cfg, ledger)}
    assert rows["tiktok"]["estimate"] == 35.0 and rows["youtube"]["estimate"] == 1.4 and rows["tiktok"]["status"] == "to_submit"  # the cap, then the rate
    assert desk.totals(cfg, ledger) == {"posts": 2, "to_submit": 2, "estimate": 36.4, "paid": 0.0}

    desk.mark(cfg, ledger, "c1", "tiktok", "submitted")
    with pytest.raises(ValueError, match="how much was paid"):
        desk.mark(cfg, ledger, "c1", "tiktok", "paid")
    desk.mark(cfg, ledger, "c1", "tiktok", "paid", 31.5)
    desk.mark(cfg, ledger, "c1", "youtube", "rejected")
    assert desk.totals(cfg, ledger) == {"posts": 2, "to_submit": 0, "estimate": 0.0, "paid": 31.5}
    entry = next(e for e in money.entries(cfg) if e["id"] == "campaign:c1:tiktok")
    assert (entry["kind"], entry["source"], entry["amount"], entry["note"]) == ("income", "GTA 6 launch (campaign)", 31.5, "Clip c1 on tiktok")
    desk.mark(cfg, ledger, "c1", "tiktok", "approved")  # marked paid by mistake: the money entry goes with it
    assert not [e for e in money.entries(cfg) if e["id"] == "campaign:c1:tiktok"]
    with pytest.raises(KeyError):
        desk.mark(cfg, ledger, "c1", "facebook", "submitted")


# --- progress to being paid -------------------------------------------------------------------

def test_each_programmes_bar_against_the_channels_numbers(cfg, ledger):
    cfg.publish.platforms = ["tiktok", "youtube", "facebook"]
    clip(ledger, "c1", views={"youtube": 400_000, "tiktok": 30_000}, days_ago=10)
    clip(ledger, "c2", views={"youtube": 100_000, "tiktok": 90_000}, days_ago=45)  # inside YouTube's 90 days, outside TikTok's 30
    clip(ledger, "c3", views={"youtube": 999}, days_ago=120)
    cfg.progress.followers = {"tiktok": 2500, "youtube": 3}
    store.write(cfg, "progress", {"at": 0, "followers": {"youtube": 640}})  # what YouTube itself said last
    found = {p["name"]: p for p in progress.report(cfg, ledger, now=NOW, ask=False)}
    yt = found["YouTube Partner Program (ads on Shorts)"]
    assert [(g["label"], g["have"], g["need"]) for g in yt["goals"]] == [("subscribers", 640, 1000), ("views in 90 days", 500_000, 10_000_000)]
    assert yt["goals"][0]["share"] == 0.64 and yt["followers_from"] == "platform" and not yt["reached"]  # the platform's own figure wins over a typed one
    tt = found["TikTok Creator Rewards Program"]
    assert [(g["have"], g["need"]) for g in tt["goals"]] == [(2500, 10_000), (30_000, 100_000)] and tt["followers_from"] == "typed in"
    assert found["Facebook Content Monetization"]["goals"] == [] and "invitation" in found["Facebook Content Monetization"]["note"]
    assert "Instagram" not in found  # not a chosen platform
    cfg.progress.followers = {"tiktok": 12_000}
    clip(ledger, "c4", views={"tiktok": 80_000}, days_ago=3)
    assert next(p for p in progress.report(cfg, ledger, now=NOW, ask=False) if p["platform"] == "tiktok")["reached"]


# --- safety nets --------------------------------------------------------------------------------

def test_backups_are_made_kept_and_restored(cfg, ledger):
    assert upkeep.backup(cfg) is None  # nothing to back up yet
    clip(ledger, "c1")
    ledger.save()
    settings.save(cfg.data_root, {"brand": {"handle": "@leonidaclips"}})
    settings.set_secret(cfg.data_root, "upload_post_api_key", "a-secret")
    money.add(cfg, "income", "TikTok", 5, "2026-11-01")
    first = upkeep.daily(cfg, NOW)
    assert first.name == "clipper-20261120-150000.zip" and upkeep.daily(cfg, NOW + timedelta(hours=3)) is None  # one a day
    with zipfile.ZipFile(first) as z:
        assert sorted(z.namelist()) == ["settings.json", "state/ledger.json", "state/money.json"]  # the records; never the secrets
        assert b"a-secret" not in b"".join(z.read(n) for n in z.namelist())
    assert upkeep.backups(cfg)[0] == {"name": first.name, "when": "20 Nov 2026, 15:00", "kb": 1}

    ledger.clips.clear()
    ledger.save()
    settings.save(cfg.data_root, {"brand": {"handle": "@changed"}})
    assert upkeep.restore(cfg, first.name) == ["settings.json", "state/ledger.json", "state/money.json"]
    assert "c1" in Ledger(ledger.path).clips and settings.load(cfg.data_root)["brand"]["handle"] == "@leonidaclips"
    assert any("before-restore" in b["name"] for b in upkeep.backups(cfg))  # what was there was kept
    with pytest.raises(FileNotFoundError):
        upkeep.restore(cfg, "../../etc/passwd")

    evil = upkeep.folder(cfg) / "clipper-20270101-000000.zip"
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("../outside.txt", "x")
        z.writestr("state/../../outside.json", "x")
        z.writestr("state/ledger.json", "{}")
    assert upkeep.restore(cfg, evil.name) == ["state/ledger.json"] and not (cfg.data_root.parent / "outside.txt").exists()
    for day in range(1, 20):
        upkeep.backup(cfg, now=NOW + timedelta(days=day))
    assert len(upkeep.backups(cfg)) == upkeep.KEEP


def test_a_newer_clipper_is_noticed_and_the_downloader_is_kept_current(cfg, tmp_path, monkeypatch):
    assert upkeep.newest(cfg) == {"available": False, "installed": "", "latest": "", "note": "not installed by the installer"}
    version = tmp_path / "VERSION"
    version.write_text("aaaaaaa1111\ncarlos/clips\nmain\n", encoding="ascii")
    monkeypatch.setattr(upkeep, "VERSION", version)
    asked = []

    def github(url):
        asked.append(url)
        return {"sha": "bbbbbbb2222"}

    assert upkeep.newest(cfg, fetch=github) == {"available": True, "installed": "aaaaaaa", "latest": "bbbbbbb"}
    assert asked == ["https://api.github.com/repos/carlos/clips/commits/main"]
    assert upkeep.newest(cfg, fetch=github)["available"] and len(asked) == 1  # asked at most every six hours
    assert upkeep.newest(cfg, fetch=lambda url: 1 / 0, fresh=True)["available"]  # no connection: the last answer stands
    version.write_text("bbbbbbb2222\ncarlos/clips\nmain\n", encoding="ascii")
    assert not upkeep.newest(cfg, ask=False)["available"]

    ran = []

    class Done:
        returncode, stdout, stderr = 0, "", ""

    def run(cmd, **kwargs):
        ran.append(cmd)
        return Done()

    said: list[str] = []
    assert upkeep.weekly(cfg, said.append, now=1_000_000.0, run=run) and "--upgrade" in ran[0] and ran[0][-2:] == ["yt-dlp", "yt-dlp-ejs"]
    assert said == ["  the downloader is up to date"]
    assert not upkeep.weekly(cfg, said.append, now=1_000_000.0 + 3 * 86400, run=run)  # not again within the week
    assert upkeep.weekly(cfg, said.append, now=1_000_000.0 + 3 * 86400, run=run, force=True) and len(ran) == 2  # unless every download just failed
    Done.returncode, Done.stderr = 1, "No matching distribution"
    assert "could not be updated: No matching distribution" in upkeep.refresh_downloader(run)


def test_every_download_failing_is_told_from_an_ordinary_run():
    from clipper import intake

    intake.last.update(online=3, fetched=0, trouble=2)
    assert intake.downloader_failing()
    intake.last.update(online=3, fetched=1, trouble=2)
    assert not intake.downloader_failing()
    intake.last.update(online=3, fetched=0, trouble=0)  # nothing new was listed: not a fault
    assert not intake.downloader_failing()


# --- alerts ------------------------------------------------------------------------------------

def test_alerts_go_to_the_owners_topic_once_and_say_nothing_private(cfg):
    sent = []

    class Ok:
        status_code = 200

    def post(url, data=None, headers=None, timeout=None):
        sent.append((url, data.decode(), headers))
        return Ok()

    assert not alerts.send(cfg, "ready", "3 clips", post=post) and not sent  # off
    cfg.alerts.enabled = True
    assert not alerts.send(cfg, "ready", "3 clips", post=post)  # no topic saved
    settings.set_secret(cfg.data_root, "alerts_topic", "clipper-7f3k9q2m")
    assert alerts.send(cfg, "ready", "3 new clip(s) are ready.", once="run-ready:r1", post=post)
    assert sent == [("https://ntfy.sh/clipper-7f3k9q2m", "3 new clip(s) are ready.", {"Title": "Clipper: Clips are ready", "Tags": "clapper", "Priority": "default"})]
    assert not alerts.send(cfg, "ready", "3 new clip(s) are ready.", once="run-ready:r1", post=post) and len(sent) == 1  # not twice
    cfg.alerts.events = ["failed"]
    assert not alerts.send(cfg, "ready", "x", post=post)  # a kind the owner switched off
    assert alerts.send(cfg, "failed", "A post failed", post=post) and sent[-1][2]["Priority"] == "high"
    assert not alerts.send(cfg, "failed", "x", post=lambda *a, **k: 1 / 0)  # a service that cannot be reached never stops the work

    cfg.alerts.events = ["ready", "failed"]
    alerts.after_run(cfg, {"run": "r2", "clips": 2, "failed": 0, "problems": []})
    alerts.after_run(cfg, {"run": "r3", "clips": 0, "failed": 0, "problems": []})  # nothing made: no message
    assert all("@" not in text and "http" not in text for _, text, _ in sent)


def test_a_sign_in_that_is_running_out_is_announced_a_week_ahead(cfg, monkeypatch):
    said = []
    monkeypatch.setattr(alerts, "send", lambda cfg, event, text, once="", post=None: said.append((event, text, once)) or True)
    settings.set_account(cfg.data_root, "meta", {"connected": "2026-10-01T00:00:00Z", "page_id": "111", "name": "Leonida Clips",
                                                "expires": (NOW + timedelta(days=4)).strftime("%Y-%m-%dT%H:%M:%SZ")})
    alerts.signins(cfg, NOW)
    assert said[0][0] == "signin" and "The Facebook sign-in runs out in 4 day(s)" in said[0][1]
    settings.set_account(cfg.data_root, "youtube", {"connected": "2026-10-01T00:00:00Z", "name": "Leonida Clips", "problem": "the sign-in was withdrawn"})
    alerts.signins(cfg, NOW)
    assert any("YouTube needs connecting again: the sign-in was withdrawn" in text for _, text, _ in said)


# --- channels ----------------------------------------------------------------------------------

def test_a_second_channel_has_its_own_folder_settings_and_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPPER_DATA", str(tmp_path))
    settings.save(tmp_path, {"brand": {"handle": "@leonidaclips"}, "moments": {"max_len": 95}, "publish": {"mode": "live", "profile": "mine"},
                             "accounts": {"youtube": {"connected": "x"}}, "ai": {"account": "a1"}})
    settings.set_secret(tmp_path, "youtube_refresh_token", "first-channel-token")
    assert channels.listing() == [{"id": "main", "name": "@leonidaclips", "folder": str(tmp_path)}]
    made = channels.create("  RDR 3   clips ")
    assert (made["id"], made["name"]) == ("rdr-3-clips", "RDR 3 clips") and [c["id"] for c in channels.listing()] == ["main", "rdr-3-clips"]
    place = channels.folder("rdr-3-clips")
    carried = settings.load(place)
    assert carried["moments"] == {"max_len": 95} and carried["publish"] == {"mode": "dry_run", "hold_for_review": True}  # how clips are made carries over
    assert "accounts" not in carried and "ai" not in carried and carried["brand"] == {"handle": ""}  # accounts and sign-ins do not

    first, second = config.load(), config.load(data_dir=place)
    assert first.data_root == tmp_path and second.data_root == place and second.moments.max_len == 95
    assert settings.get_secret(second.data_root, "youtube_refresh_token") == ""  # nothing shared
    settings.set_secret(second.data_root, "youtube_refresh_token", "second-channel-token")
    assert settings.get_secret(first.data_root, "youtube_refresh_token") == "first-channel-token"
    assert settings._scoped(place, "youtube_refresh_token") == "rdr-3-clips/youtube_refresh_token" and settings._scoped(tmp_path, "x") == "x"
    monkeypatch.setenv("CLIPPER_DATA", "/somewhere/else")  # a loaded configuration keeps pointing at its own folder
    assert second.data_root == place and second.path("state") == place / "state"
    monkeypatch.setenv("CLIPPER_DATA", str(tmp_path))

    with pytest.raises(ValueError, match="already a channel"):
        channels.create("rdr 3 clips")
    with pytest.raises(ValueError):
        channels.create("   ")
    assert channels.create("RDR-3 clips!")["id"] == "rdr-3-clips-2"
    channels.rename("rdr-3-clips", "Red Dead")
    assert channels.listing()[1]["name"] == "Red Dead"
    left = channels.unlist("rdr-3-clips")
    assert left == place and place.is_dir() and [c["id"] for c in channels.listing()] == ["main", "rdr-3-clips-2"]  # its folder stays
    with pytest.raises(KeyError):
        channels.folder("rdr-3-clips")


def test_the_daily_commands_go_through_every_channel(tmp_path, monkeypatch, capsys):
    from clipper import cli

    monkeypatch.setenv("CLIPPER_DATA", str(tmp_path))
    monkeypatch.delenv("CLIPPER_CHANNEL", raising=False)
    (tmp_path / "inbox").mkdir()
    channels.create("Second")
    conf = tmp_path / "config.toml"
    conf.write_text('[[sources]]\ntype = "folder"\npath = "inbox"\n', encoding="utf-8")
    ran = []
    monkeypatch.setattr(cli.subprocess, "run", lambda cmd, env=None: ran.append((cmd, env)) or type("Done", (), {"returncode": 0})())
    assert cli.main(["--config", str(conf), "post"]) == 0
    assert len(ran) == 1 and ran[0][0][1:] == ["-m", "clipper", "--data", str(tmp_path / "channels" / "second"), "--config", str(conf), "post"]
    assert ran[0][1]["CLIPPER_CHANNEL"] == "second" and ran[0][1]["CLIPPER_BASE"] == str(tmp_path) and "--- channel: Second ---" in capsys.readouterr().out
    monkeypatch.setenv("CLIPPER_CHANNEL", "second")  # a channel's own process does not start the others again
    assert cli.main(["--config", str(conf), "post"]) == 0 and len(ran) == 1


# --- own gameplay --------------------------------------------------------------------------------

def test_capture_folders_are_sources_of_own_footage_and_a_file_still_recording_waits(tmp_path, monkeypatch, ledger):
    import os
    import time

    from clipper import intake

    monkeypatch.setenv("CLIPPER_DATA", str(tmp_path))
    captures = tmp_path / "Captures"
    captures.mkdir()
    settings.update(tmp_path, {"captures": [str(captures), "  "]})
    conf = tmp_path / "config.toml"  # only the inbox besides: no online source is asked in this test
    conf.write_text('[[sources]]\ntype = "folder"\npath = "inbox"\n', encoding="utf-8")
    cfg = config.load(str(conf))
    assert cfg.captures == [str(captures)]
    own = cfg.sources[0]
    assert (own.type, own.path, own.permission, own.settle) == ("folder", str(captures), "own footage", 120) and cfg.sources[-1].path == "inbox"
    done, recording = captures / "session.mp4", captures / "now.mp4"
    done.write_bytes(b"0" * 2000)
    recording.write_bytes(b"1" * 2000)
    os.utime(done, (time.time() - 600, time.time() - 600))
    said: list[str] = []
    items = intake.collect(cfg, ledger, said.append)
    assert [i.name for i in items] == ["session.mp4"] and items[0].permission == "own footage" and items[0].creator == ""
    assert any("now.mp4 is still being recorded" in line for line in said)
    with pytest.raises(ValueError):
        settings.update(tmp_path, {"captures": "C:/Videos"})


# --- going live -------------------------------------------------------------------------------------

def test_the_way_to_going_live_is_read_off_what_is_there(cfg, ledger, monkeypatch):
    cfg.publish.platforms = ["youtube", "tiktok"]
    steps = {s["key"]: s for s in launch.steps(cfg, ledger)}
    assert list(steps) == ["install", "claude", "accounts", "clip", "post", "public", "auto", "daily"]
    assert not any(s["done"] for s in steps.values()) and steps["accounts"]["detail"] == "not connected: YouTube, TikTok"
    assert launch.summary(list(steps.values())) == {"done": 0, "of": 8, "live": False, "next": "Clipper is installed and every part works here"}

    monkeypatch.setattr(launch.outbox, "routes", lambda cfg: {"youtube": "direct", "tiktok": "service"})
    monkeypatch.setattr(launch.claude, "unavailable", lambda cfg: "")
    monkeypatch.setattr(launch.transcribe, "models_present", lambda cfg: True)
    monkeypatch.setattr(launch.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    clip(ledger, "c1", views={"youtube": 10})
    steps = {s["key"]: s for s in launch.steps(cfg, ledger, daily_on=True, check_passed=True)}
    assert [k for k, s in steps.items() if s["done"]] == ["install", "claude", "accounts", "clip", "daily"]
    assert steps["post"]["detail"] == "nothing out yet on: TikTok"
    ledger.clips["c1"]["posts"]["tiktok"] = {"state": "draft", "via": "direct"}
    cfg.launch = {"seen_public": True}
    cfg.publish.mode, cfg.publish.hold_for_review = "live", False
    found = launch.steps(cfg, ledger, daily_on=True, check_passed=True)
    assert all(s["done"] for s in found) and launch.summary(found)["live"]
    assert not {s["key"]: s for s in launch.steps(cfg, ledger, daily_on=True, check_passed=False)}["install"]["done"]  # a failed check is not "works here"
