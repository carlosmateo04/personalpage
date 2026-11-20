"""The whole system as one: from the "Make clips now" button to posts on four platforms.

What is real here: the app's server and its buttons, the run it starts as a separate
process, the downloader (yt-dlp) fetching over HTTP, the speech model, the loudness
finder, the gameplay-only checks, the storyboard and frame sheets, the Claude client
(scout and editor), ffmpeg rendering with captions and on-screen notes, the cover
frames, the post text and the search check, the scheduler, the outbox, and the four
posting connectors with their sign-ins and uploads.

What is stood in for, each by a local server that answers as the service's
documentation says and records what it is sent: the video site, the Claude API,
Google (sign-in and YouTube), TikTok and Meta (Facebook and Instagram).
"""

import functools
import json
import subprocess
import threading
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from clipper import cli, config, connectors, media, outbox, pipeline, settings, transcribe, ui
from clipper.connectors import meta, tiktok, youtube
from clipper.state import Ledger
from standins import GOOD_KEY, FakeClaude, FakeGoogle, FakeMeta, FakeTikTok, serve
from test_accounts import start
from test_intake import Quiet
from test_ui import TOKEN, call

SAMPLE = config.Config().models_dir / transcribe.ASR_DIR / "test_wavs" / "0.wav"
pytestmark = pytest.mark.skipif(not SAMPLE.exists(), reason="speech models not installed")
TITLE = "GTA 6 police chase no commentary"


def verdict(title: str, section: str, hook: str, tags: list[str], score: int, **more) -> dict:
    """What the editor answers for one clip."""
    return {"keep": True, "reason": "Pure gameplay.", "start": "", "end": "", "section": section, "title": title, "hook": hook,
            "summary": f"What happens: {title.lower()}.", "tags": tags, "callouts": [], "cover": "", "score": score, **more}


def pick(start_at: str, end_at: str, title: str, section: str, score: int) -> dict:
    """One moment the scout picks from the whole video."""
    return {"start": start_at, "end": end_at, "section": section, "title": title, "hook": title[:40], "why": f"Why: {title.lower()}.", "score": score}


CHASE = verdict("Six-star chase ends on the beach", "moments", "They never stood a chance", ["gta 6 police chase", "gta 6 six stars"], 90, cover="1:10")
GLITCH = verdict("Patrol car launches into the sky", "glitches", "It just took off", ["gta 6 glitch", "gta 6 flying car"], 80,
                 callouts=[{"at": "3:40", "text": "Watch the patrol car"}])
# The video is five minutes long: speech gets loud at 0:45 and there is a burst of noise at 3:35.
SCOUT = {"usable": True, "problem": "", "moments": [pick("0:30", "2:00", "A chase starts at the lights", "moments", 90),
                                                    pick("3:20", "4:50", "A patrol car takes off", "glitches", 80)]}


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A video site with one five-minute video, and stand-ins for Claude, Google, TikTok and Meta; a config that uses them."""
    speech, rate = sf.read(str(SAMPLE), dtype="float32")
    rng = np.random.default_rng(11)
    track = rng.normal(0, 0.003, rate * 300).astype(np.float32)
    for at in range(4, 290, 18):
        track[at * rate: at * rate + len(speech)] += speech * 0.07
    track[45 * rate: 45 * rate + len(speech)] += speech
    track[215 * rate: 221 * rate] += rng.normal(0, 0.35, rate * 6).astype(np.float32)
    site = tmp_path / "site"
    site.mkdir()
    sf.write(tmp_path / "track.wav", np.clip(track, -1, 1), rate)
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=15:duration=300", "-i", str(tmp_path / "track.wav"),
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", "-movflags", "+faststart",
         str(site / f"{TITLE}.mp4")], check=True)

    servers = []
    web_server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(site)))
    threading.Thread(target=web_server.serve_forever, daemon=True).start()
    servers.append(web_server)
    web = f"http://127.0.0.1:{web_server.server_port}"
    for fake in (FakeClaude, FakeGoogle, FakeTikTok, FakeMeta):
        server, fake.url = serve(fake)
        servers.append(server)
    FakeClaude.replies, FakeClaude.seen = [], []
    FakeGoogle.reset(), FakeTikTok.reset(), FakeMeta.reset()
    monkeypatch.setattr(youtube.YouTube, "TOKEN", f"{FakeGoogle.url}/token")
    monkeypatch.setattr(youtube.YouTube, "REVOKE", f"{FakeGoogle.url}/revoke")
    monkeypatch.setattr(youtube.YouTube, "API", f"{FakeGoogle.url}/youtube/v3")
    monkeypatch.setattr(youtube.YouTube, "UPLOAD", f"{FakeGoogle.url}/upload/youtube/v3/videos")
    monkeypatch.setattr(tiktok.TikTok, "API", FakeTikTok.url)
    monkeypatch.setattr(meta.Meta, "GRAPH", f"{FakeMeta.url}/v27.0")
    monkeypatch.setattr(meta.Meta, "POLL", 0)

    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("CLIPPER_DATA", str(data))
    monkeypatch.setenv("ANTHROPIC_BASE_URL", FakeClaude.url)
    # Posting times three and five hours from now, so every one is ahead and far enough for the platforms to schedule.
    now = datetime.now(timezone.utc)
    slots = sorted((now + timedelta(hours=h)).strftime("%H:%M") for h in (3, 5))

    def write_config(publish: str) -> Path:
        path = tmp_path / "config.toml"
        path.write_text(f'''
[[sources]]
type = "ytdlp"
url = "{web}/{urllib.parse.quote(TITLE)}.mp4"
creator = "RawPlay"
title_any = ["no commentary"]
title_none = ["reaction", "live"]
section = "moments"
min_views = 0
max_hours = 0.5

[moments]
min_len = 85
max_len = 95
max_per_source = 3
whole_clip_min = 80
whole_clip_max = 100

[render]
width = 540
height = 960
fps = 15
preset = "ultrafast"

[schedule]
timezone = "UTC"
slots = {json.dumps(slots)}

[publish]
{publish}
''', encoding="utf-8")
        return path

    yield type("World", (), {"data": data, "write_config": staticmethod(write_config)})
    for server in servers:
        server.shutdown()


def requests_to(fake, method: str, path: str) -> list[dict]:
    return [c for c in fake.calls if c["method"] == method and c["path"] == path]


def test_from_the_button_to_four_platforms(world):
    """Accounts connected in the app, clips made by a run, one edited again by Claude, all scheduled, and every post followed to the end."""
    app = ui.App(str(world.write_config('mode = "dry_run"\nhold_for_review = true')), TOKEN)
    server = ThreadingHTTPServer(("127.0.0.1", 0), ui.handler_for(app))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    app.url, app.port = f"http://127.0.0.1:{server.server_port}", server.server_port

    def run_to_the_end() -> dict:
        assert call(app, "/api/run", {})[0] == 200
        for _ in range(300):
            time.sleep(2)
            state = call(app, "/api/state")[1]
            if not state["run"]["running"] and state["run"]["exit"] is not None:
                return state
        raise AssertionError("the run did not finish: " + "\n".join(app.log))

    try:
        # --- 1. The accounts and Claude are set up from the Accounts tab -----------------------
        asked = start(app, {"platform": "youtube", "client_id": "client-123", "client_secret": "GOCSPX-secret"})
        FakeGoogle.challenge = asked["code_challenge"]
        assert call(app, f"/?code=good-code&state={asked['state']}", token="")[0] == 200
        asked = start(app, {"platform": "tiktok", "client_id": "aw123", "client_secret": "tt-secret"})
        FakeTikTok.challenge = asked["code_challenge"]
        assert call(app, f"/oauth/tiktok/?code=good-code&state={asked['state']}", token="")[0] == 200
        asked = start(app, {"platform": "facebook", "client_id": "app-1", "client_secret": "app-secret"})
        assert call(app, f"/oauth/meta?code=good-code&state={asked['state']}", token="")[0] == 200
        assert call(app, "/api/ai-key", {"api_key": GOOD_KEY})[0] == 200
        state = call(app, "/api/state")[1]
        assert {p["name"]: p["route"] for p in state["posting"]["platforms"]} == dict.fromkeys(("youtube", "tiktok", "instagram", "facebook"), "direct")
        assert state["ai"]["off"] == ""

        # --- 2. Make clips now: find, download, scout, check, edit, render ---------------------
        FakeClaude.replies += [dict(SCOUT), dict(CHASE), dict(GLITCH)]  # the scout once for the video, then the editor once per clip
        state = run_to_the_end()
        log = "\n".join(state["run"]["log"])
        assert state["run"]["exit"] == 0, log
        for step in ("looking for videos", f"downloading: {TITLE}", "transcribing", "Claude is looking at the whole video (57 frames)",
                     "Claude picked 2 moment(s): 0:30 A chase starts at the lights; 3:20 A patrol car takes off", "2 moment(s) found, 2 passed the checks",
                     "Claude is checking and editing the planned clips", "kept [moments, 90/100]: Six-star chase ends on the beach",
                     "kept [glitches, 80/100]: Patrol car launches into the sky (1 on-screen note(s))", "rendered and held for review", "Finished."):
            assert step in log, f"missing from the run's log: {step}\n{log}"

        clips = {c["title"]: c for c in state["clips"]}
        assert set(clips) == {"Six-star chase ends on the beach", "Patrol car launches into the sky"}
        for clip in clips.values():
            assert clip["state"] == "ready" and 85 <= clip["seconds"] <= 96 and clip["seo"] == [] and clip["creator"] == "RawPlay" and clip["posts"] == {}
            assert call(app, clip["media"], headers={"Range": "bytes=0-99"})[0] == 206 and call(app, clip["cover"])[0] == 200  # the app plays it and shows its cover
        chase, glitch = clips["Six-star chase ends on the beach"], clips["Patrol car launches into the sky"]
        assert (chase["section"], glitch["section"], chase["score"], glitch["score"]) == ("HIGHLIGHT", "GLITCH", 90, 80)
        assert chase["picked"] == "Why: a chase starts at the lights." and "What happens: patrol car launches into the sky." in glitch["description"]
        assert chase["caption"].startswith("Six-star chase ends on the beach | GTA 6 Gameplay • Credit: RawPlay #gta6highlights #gta6moments #gta6 ")

        run_dir = next((world.data / "out").glob("*/plan.json")).parent
        videos = sorted(run_dir.glob("c*.mp4"))
        assert len(videos) == 2
        for video in videos:
            info = media.probe(video)
            assert (info.width, info.height) == (540, 960) and info.has_audio and 85 <= info.duration <= 96 and info.pixel_shape == "1:1"
            kit = video.with_suffix(".txt").read_text(encoding="utf-8")
            assert "=== TikTok ===" in kit and "=== YouTube Shorts ===" in kit and "Credit: RawPlay" in kit and "Tags: gta 6 " in kit
        shown = [p.read_text(encoding="utf-8").split("[Events]")[1] for p in (world.data / "work" / "captions").glob("*.ass")]
        assert sum("Callout," in events and "WATCH THE PATROL CAR" in events for events in shown) == 1  # Claude's on-screen note is on the clip

        scout_asked, *editor_asked = [seen["body"]["messages"][0]["content"] for seen in FakeClaude.seen]
        assert [block["type"] for block in scout_asked] == ["text", "image", "text", "image", "text", "image", "text"]  # three sheets of the video, then the brief
        assert "What the loudness detector would clip:" in scout_asked[-1]["text"] and TITLE in scout_asked[-1]["text"]
        assert len(editor_asked) == 2 and all(c[0]["type"] == "image" and TITLE in c[1]["text"] for c in editor_asked)
        assert "The clip as cut now: 0:30 to 2:00 (90 seconds)." in editor_asked[0][1]["text"]
        assert state["ai"]["usage"]["requests"] == 3 and state["ai"]["usage"]["by"] == {"scout": 1, "edit": 2}

        # --- 3. One clip is edited again by Claude, with an instruction typed in the app --------
        FakeClaude.replies.append({**CHASE, "start": "0:35", "end": "2:05", "title": "The chase from the first siren", "hook": "First siren"})
        assert call(app, "/api/reedit", {"id": chase["id"], "instruction": "Start five seconds later."})[0] == 200
        assert call(app, "/api/reedit", {"id": chase["id"], "instruction": ""})[0] == 400  # already at work on it
        for _ in range(150):
            edited = next(c for c in call(app, "/api/state")[1]["clips"] if c["id"] == chase["id"])
            if not edited["busy"]:
                break
            time.sleep(1)
        assert edited["edit_error"] == "" and edited["title"] == "The chase from the first siren" and edited["seconds"] == 90 and edited["state"] == "ready"
        assert "The channel owner asks for this change to the clip: Start five seconds later." in FakeClaude.seen[-1]["body"]["messages"][0]["content"][1]["text"]
        rec = Ledger(world.data / "state" / "ledger.json").clips[chase["id"]]
        assert (rec["start"], rec["end"]) == (35.0, 125.0) and rec["review"]["cut"] == [30.0, 120.0] and abs(media.probe(Path(rec["file"])).duration - 90) < 1
        assert rec["cover"] == 35.0  # the cover Claude chose at 1:10 is now 35 seconds into the clip

        # --- 4. Schedule all: YouTube and Facebook take their clips now, with the posting time ---
        status, body, _ = call(app, "/api/post-all", {})
        assert status == 200 and body["count"] == 2
        state = call(app, "/api/state")[1]
        for clip in state["clips"]:
            assert clip["state"] == "queued" and {name: post["state"] for name, post in clip["posts"].items()} == {
                "tiktok": "waiting", "instagram": "waiting", "youtube": "scheduled", "facebook": "scheduled"}
        opened = [json.loads(c["body"]) for c in requests_to(FakeGoogle, "POST", "/upload/youtube/v3/videos")]
        assert {o["snippet"]["title"] for o in opened} == {"The chase from the first siren | GTA 6 Gameplay #shorts", "Patrol car launches into the sky | GTA 6 Glitch #shorts"}
        times = set()
        for o in opened:
            assert o["status"]["privacyStatus"] == "private" and o["snippet"]["tags"][0].startswith("gta 6 ") and "Credit: RawPlay. Original: http://127.0.0.1" in o["snippet"]["description"]
            times.add(datetime.strptime(o["status"]["publishAt"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc))
        assert len(times) == 2 and all(when > datetime.now(timezone.utc) + timedelta(hours=2) for when in times)  # two different posting times
        assert FakeGoogle.stored in [video.read_bytes() for video in videos]  # the clip itself went up, byte for byte
        finished = [c["query"] for c in requests_to(FakeMeta, "POST", "/v27.0/111/video_reels") if c["query"].get("upload_phase") == "finish"]
        assert len(finished) == 2 and all(f["video_state"] == "SCHEDULED" and " | GTA 6 " in f["description"] and "Credit: RawPlay" in f["description"] for f in finished)
        assert {datetime.fromtimestamp(int(f["scheduled_publish_time"]), timezone.utc) for f in finished} == times
        assert not requests_to(FakeTikTok, "POST", "/v2/post/publish/inbox/video/init/") and not requests_to(FakeMeta, "POST", "/v27.0/178/media")  # not their time yet

        # --- 5. Their time comes: TikTok and Instagram are sent ----------------------------------
        counts = app.tick(now=max(times) + timedelta(minutes=1))
        assert counts["sent"] == 4 and counts["failed"] == 0
        assert len(requests_to(FakeTikTok, "POST", "/v2/post/publish/inbox/video/init/")) == 2 and FakeTikTok.stored in [video.read_bytes() for video in videos]
        containers = [c["query"] for c in requests_to(FakeMeta, "POST", "/v27.0/178/media")]
        assert len(containers) == 2 and all(c["media_type"] == "REELS" and c["upload_type"] == "resumable" and "#gta6" in c["caption"] for c in containers)
        assert "35000" in {c.get("thumb_offset") for c in containers}  # Instagram is given the cover frame Claude chose
        assert len(requests_to(FakeMeta, "POST", "/v27.0/178/media_publish")) == 2

        # --- 6. The platforms report back ----------------------------------------------------------
        FakeTikTok.post_status = {"status": "SEND_TO_USER_INBOX"}
        app._refreshed = 0
        app.tick(now=max(times) + timedelta(minutes=30))
        state = call(app, "/api/state")[1]
        for clip in state["clips"]:
            posts = clip["posts"]
            assert clip["state"] == "posted" and {name: post["state"] for name, post in posts.items()} == {
                "tiktok": "draft", "instagram": "posted", "youtube": "posted", "facebook": "posted"}
            assert posts["youtube"]["views"] == 1234 and posts["youtube"]["link"] == "https://www.youtube.com/watch?v=vid123"
            assert posts["instagram"]["link"] == "https://www.instagram.com/reel/Cabc/" and posts["facebook"]["link"].startswith("https://www.facebook.com/reel/")
            assert "paste the caption and post it" in posts["tiktok"]["note"]
        assert any("scheduled on YouTube" in line for line in state["posting"]["log"]) and any("sent as a draft to TikTok" in line for line in state["posting"]["log"])

        # --- 7. Pressing the button again makes nothing twice ---------------------------------------
        uploads_before = len(requests_to(FakeGoogle, "POST", "/upload/youtube/v3/videos"))
        state = run_to_the_end()
        assert state["run"]["exit"] == 0 and len(state["clips"]) == 2 and len(FakeClaude.seen) == 4
        assert len(requests_to(FakeGoogle, "POST", "/upload/youtube/v3/videos")) == uploads_before
        report = (world.data / "reports" / "latest.md").read_text(encoding="utf-8")
        assert "## Automatic review" in report and "2 kept, 0 dropped" in report
    finally:
        app.stop_run()
        server.shutdown()


def test_hands_off_one_command_makes_the_clips_and_queues_them_and_the_poster_sends_the_rest(world, capsys):
    """With automatic posting on: `run` goes from finding the video to the platforms, and `post` sends what waits for its time."""
    path = world.write_config('mode = "live"\nhold_for_review = false')
    cfg = config.load(str(path))
    for name, app_id, secret, fake in (("youtube", "client-123", "GOCSPX-secret", FakeGoogle), ("tiktok", "aw123", "tt-secret", FakeTikTok)):
        conn = connectors.get(name, cfg)
        conn.save_app(app_id, secret)
        url, kept = conn.auth_url(conn.redirect_uri(8765), "s")
        fake.challenge = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["code_challenge"][0]
        conn.finish(conn.redirect_uri(8765), {"code": "good-code"}, kept)
    facebook = connectors.get("facebook", cfg)
    facebook.save_app("app-1", "app-secret")
    facebook.finish(facebook.redirect_uri(8765), {"code": "good-code"}, {})
    settings.set_secret(world.data, "anthropic_api_key", GOOD_KEY)
    assert outbox.routes(config.load(str(path))) == dict.fromkeys(("tiktok", "instagram", "youtube", "facebook"), "direct")

    FakeClaude.replies += [dict(SCOUT), dict(GLITCH), {**GLITCH, "keep": False, "reason": "A loading screen fills most of the clip.", "score": 0}]
    assert cli.main(["--config", str(path), "run"]) == 0
    out = capsys.readouterr().out
    assert "dropped: A loading screen fills most of the clip." in out and "(live: tiktok, instagram, youtube, facebook)" in out
    assert "scheduled on YouTube" in out and "scheduled on Facebook" in out

    ledger = Ledger(world.data / "state" / "ledger.json")
    records = sorted(ledger.clips.values(), key=lambda r: r["status"])
    assert [r["status"] for r in records] == ["dropped", "scheduled"]
    made = records[1]
    assert made["title"] == "Patrol car launches into the sky" and made["seo"] == []
    assert {name: post["state"] for name, post in made["posts"].items()} == {"tiktok": "waiting", "instagram": "waiting", "youtube": "scheduled", "facebook": "scheduled"}
    assert len(list((world.data / "out").glob("*/c*.mp4"))) == 1  # the rejected clip was never rendered
    assert len(requests_to(FakeGoogle, "POST", "/upload/youtube/v3/videos")) == 1

    assert cli.main(["--config", str(path), "post", "--log", str(world.data / "logs" / "posting.log")]) == 0  # the poster, before the posting time
    assert not requests_to(FakeTikTok, "POST", "/v2/post/publish/inbox/video/init/") and not (world.data / "logs" / "posting.log").exists()

    due = datetime.fromisoformat(made["slot"]) + timedelta(minutes=1)
    counts = outbox.deliver(config.load(str(path)), ledger, due, pipeline._log)  # the poster, once the time has come
    assert counts == {"sent": 2, "failed": 0, "waiting": 0, "busy": False}
    made = Ledger(world.data / "state" / "ledger.json").clips[made["id"]]
    assert {name: post["state"] for name, post in made["posts"].items()} == {"tiktok": "draft", "instagram": "posted", "youtube": "scheduled", "facebook": "scheduled"}
    report = (world.data / "reports" / "latest.md").read_text(encoding="utf-8")
    assert "Publishing mode: **LIVE**" in report and "Patrol car launches into the sky" in report and "1 kept, 1 dropped" in report
