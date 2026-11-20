"""The whole system as one: started from the app, from finding a video to the upload request.

What is real here: the app's server and its buttons, the run it starts as a separate
process, the downloader (yt-dlp) fetching over HTTP, the speech model, the moment
finder at clip lengths of about 1:30, the gameplay-only checks, the frame grid, the
review client, ffmpeg rendering with captions, the post text and the search check,
the scheduler and the Upload-Post client.

What is stood in for: the video site (a local web server holding generated footage),
the Claude API and the Upload-Post API (local servers that record what they are sent).
"""

import functools
import json
import subprocess
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from clipper import cli, config, media, settings, transcribe, ui
from clipper.state import Ledger
from test_intake import Quiet
from test_publish import FakeApi
from test_review import GOOD_KEY, FakeClaude
from test_ui import TOKEN, call

SAMPLE = config.Config().models_dir / transcribe.ASR_DIR / "test_wavs" / "0.wav"
pytestmark = pytest.mark.skipif(not SAMPLE.exists(), reason="speech models not installed")
TITLE = "GTA 6 police chase no commentary"


class FourAccounts(FakeApi):
    """The stand-in posting service, with all four accounts connected to the profile."""

    def do_GET(self):
        if self.path.startswith("/api/uploadposts/users"):
            FakeApi.calls.append(("GET", self.path, dict(self.headers), {}))
            self._reply({"success": True, "profiles": [{"username": "gta6clips", "social_accounts": {
                p: {"username": "leonidaclips"} for p in ("tiktok", "instagram", "youtube", "facebook")}}]})
        else:
            super().do_GET()


def verdict(title: str, section: str, hook: str, tags: list[str]) -> dict:
    return {"keep": True, "reason": "Pure gameplay.", "section": section, "title": title, "hook": hook,
            "summary": f"What happens: {title.lower()}.", "tags": tags}


CHASE = verdict("Six-star chase ends on the beach", "moments", "They never stood a chance", ["gta 6 police chase", "gta 6 six stars"])
GLITCH = verdict("Patrol car launches into the sky", "glitches", "It just took off", ["gta 6 glitch", "gta 6 flying car"])


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A video site with one five-minute video, a Claude API and a posting service, all local; and a config that uses them."""
    from upload_post import UploadPostClient

    # Five minutes of quiet game sound with quiet speech throughout, loud speech at 0:45 and a loud burst of noise at 3:35.
    speech, rate = sf.read(str(SAMPLE), dtype="float32")
    rng = np.random.default_rng(11)
    track = rng.normal(0, 0.003, rate * 300).astype(np.float32)
    for at in range(4, 290, 18):
        track[at * rate : at * rate + len(speech)] += speech * 0.07
    track[45 * rate : 45 * rate + len(speech)] += speech
    track[215 * rate : 221 * rate] += rng.normal(0, 0.35, rate * 6).astype(np.float32)
    site = tmp_path / "site"
    site.mkdir()
    sf.write(tmp_path / "track.wav", np.clip(track, -1, 1), rate)
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=15:duration=300", "-i", str(tmp_path / "track.wav"),
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", "-movflags", "+faststart",
         str(site / f"{TITLE}.mp4")], check=True)

    servers = [ThreadingHTTPServer(("127.0.0.1", 0), handler) for handler in (functools.partial(Quiet, directory=str(site)), FakeClaude, FourAccounts)]
    for server in servers:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    web, claude, api = (f"http://127.0.0.1:{s.server_port}" for s in servers)
    FakeClaude.replies, FakeClaude.seen, FakeApi.calls = [], [], []

    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("CLIPPER_DATA", str(data))
    monkeypatch.setenv("ANTHROPIC_BASE_URL", claude)
    monkeypatch.setenv("ANTHROPIC_API_KEY", GOOD_KEY)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("UPLOAD_POST_API_KEY", "test-key")
    monkeypatch.setattr(UploadPostClient, "BASE_URL", f"{api}/api")
    monkeypatch.setattr(settings, "_keyring", lambda: None)

    def write_config(publish: str, per_video: int = 3) -> Path:
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
max_per_source = {per_video}
whole_clip_min = 80
whole_clip_max = 100

[render]
width = 540
height = 960
fps = 15
preset = "ultrafast"

[publish]
{publish}
''', encoding="utf-8")
        return path

    yield type("World", (), {"data": data, "write_config": staticmethod(write_config)})
    for server in servers:
        server.shutdown()


def uploads() -> list[dict]:
    return [fields for method, path, _, fields in FakeApi.calls if (method, path) == ("POST", "/api/upload")]


def test_make_clips_now_then_post_from_the_app(world):
    """The button in the app starts a run that finds, downloads, cuts, reviews and renders; the app then posts the clips."""
    app = ui.App(str(world.write_config('mode = "dry_run"\nhold_for_review = true')), TOKEN)
    server = ThreadingHTTPServer(("127.0.0.1", 0), ui.handler_for(app))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    app.url = f"http://127.0.0.1:{server.server_port}"
    FakeClaude.replies += [dict(CHASE), dict(GLITCH), dict(CHASE), dict(CHASE)]

    def run_to_the_end() -> dict:
        assert call(app, "/api/run", {})[0] == 200
        for _ in range(300):
            time.sleep(2)
            state = call(app, "/api/state")[1]
            if not state["run"]["running"] and state["run"]["exit"] is not None:
                return state
        raise AssertionError("the run did not finish: " + "\n".join(app.log))

    try:
        # The posting service is connected from the app: the key is accepted and the only profile is chosen.
        status, body, _ = call(app, "/api/connect", {"api_key": ""})
        assert status == 200 and body["accounts"]["profiles"] == [{"name": "gta6clips", "platforms": ["facebook", "instagram", "tiktok", "youtube"]}]

        state = run_to_the_end()
        log = "\n".join(state["run"]["log"])
        assert state["run"]["exit"] == 0, log
        for step in ("looking for videos", f"downloading: {TITLE}", "transcribing", "2 moment(s) found, 2 passed the checks",
                     "automatic review of the planned clips", "kept [moments]: Six-star chase ends on the beach",
                     "kept [glitches]: Patrol car launches into the sky", "rendered and held for review", "Finished."):
            assert step in log, f"missing from the run's log: {step}\n{log}"

        # Two clips of about 1:30 are waiting in the app, with the review's titles and text that passes the search check.
        clips = {c["title"]: c for c in state["clips"]}
        assert set(clips) == {"Six-star chase ends on the beach", "Patrol car launches into the sky"}
        for clip in clips.values():
            assert clip["state"] == "ready" and 85 <= clip["seconds"] <= 96 and clip["seo"] == [] and clip["creator"] == "RawPlay"
            assert call(app, clip["media"], headers={"Range": "bytes=0-99"})[0] == 206  # the app plays it
        chase, glitch = clips["Six-star chase ends on the beach"], clips["Patrol car launches into the sky"]
        assert (chase["section"], glitch["section"]) == ("HIGHLIGHT", "GLITCH")
        assert chase["caption"].startswith("Six-star chase ends on the beach | GTA 6 Gameplay • Credit: RawPlay #gta6highlights #gta6moments #gta6 ")
        assert glitch["caption"].startswith("Patrol car launches into the sky | GTA 6 Glitch • Credit: RawPlay #gta6glitch #gta6bugs #gta6 ")
        assert "What happens: patrol car launches into the sky." in glitch["description"]

        # On disk: each clip as a vertical video of that length with sound, and its ready-to-paste text beside it.
        run_dir = next((world.data / "out").glob("*/plan.json")).parent
        videos = sorted(run_dir.glob("*.mp4"))
        assert len(videos) == 2
        for video in videos:
            info = media.probe(video)
            assert (info.width, info.height) == (540, 960) and info.has_audio and 85 <= info.duration <= 96 and info.pixel_shape == "1:1"
            kit = video.with_suffix(".txt").read_text(encoding="utf-8")
            assert "=== TikTok ===" in kit and "=== YouTube Shorts ===" in kit and "Credit: RawPlay" in kit and "Tags: gta 6 " in kit

        # The review saw frames of each clip and its words; nothing has been uploaded yet.
        asked = [seen["body"]["messages"][0]["content"] for seen in FakeClaude.seen]
        assert len(asked) == 2 and all(c[0]["type"] == "image" and TITLE in c[1]["text"] for c in asked)
        assert uploads() == []

        # "Schedule all" in the app: one upload per clip, to all four accounts, each at its own posting time.
        status, body, _ = call(app, "/api/post-all", {})
        assert status == 200 and body["count"] == 2
        sent = uploads()
        assert len(sent) == 2
        times = set()
        for fields in sent:
            assert fields["user"] == ["gta6clips"] and fields["platform[]"] == ["tiktok", "instagram", "youtube", "facebook"]
            assert fields["video"][0] > 100_000  # the clip itself, not an empty file
            when = datetime.strptime(fields["scheduled_date"][0], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            assert when > datetime.now(timezone.utc)
            times.add(when)
            for name in ("tiktok_title", "instagram_title", "facebook_title"):
                assert " | GTA 6 " in fields[name][0] and "Credit: RawPlay" in fields[name][0] and "#gta6" in fields[name][0]
            assert fields["youtube_title"][0].endswith("#shorts") and "GTA 6" in fields["youtube_title"][0]
            assert "Credit: RawPlay. Original: http://127.0.0.1" in fields["youtube_description"][0]
            assert fields["tags[]"][0].startswith("gta 6 ") and "gta 6" in fields["tags[]"]
        assert len(times) == 2  # two different slots
        assert {fields["title"][0] for fields in sent} == set(clips)
        state = call(app, "/api/state")[1]
        assert {c["state"] for c in state["clips"]} == {"queued"}
        assert {r["status"] for r in Ledger(world.data / "state" / "ledger.json").clips.values()} == {"scheduled"}

        # Pressing the button again makes nothing twice: the video is known, so no new clip, review or upload.
        state = run_to_the_end()
        assert state["run"]["exit"] == 0 and len(state["clips"]) == 2 and len(FakeClaude.seen) == 2 and len(uploads()) == 2
        report = (world.data / "reports" / "latest.md").read_text(encoding="utf-8")
        assert "## Automatic review" in report and "2 kept, 0 dropped" in report
    finally:
        app.stop_run()
        server.shutdown()


def test_hands_off_run_posts_by_itself_and_holds_back_what_the_review_rejects(world, capsys):
    """With automatic posting on, one command goes from finding the video to the scheduled uploads."""
    path = world.write_config('mode = "live"\nhold_for_review = false\nprofile = "gta6clips"')
    FakeClaude.replies += [dict(GLITCH), {"keep": False, "reason": "A loading screen fills most of the clip.", "section": "moments",
                                          "title": "", "hook": "", "summary": "", "tags": []}]
    assert cli.main(["--config", str(path), "run"]) == 0
    out = capsys.readouterr().out
    assert "dropped by the review: A loading screen fills most of the clip." in out and "scheduled for" in out and "(live)" in out

    sent = uploads()
    assert len(sent) == 1 and sent[0]["title"] == ["Patrol car launches into the sky"]
    assert sent[0]["platform[]"] == ["tiktok", "instagram", "youtube", "facebook"] and sent[0]["scheduled_date"]
    records = sorted(Ledger(world.data / "state" / "ledger.json").clips.values(), key=lambda r: r["status"])
    assert [r["status"] for r in records] == ["dropped", "scheduled"]
    assert records[1]["seo"] == [] and records[1]["publish"]["request_id"] == "req-1"
    assert len(list((world.data / "out").glob("*/*.mp4"))) == 1  # the rejected clip was never rendered
    report = (world.data / "reports" / "latest.md").read_text(encoding="utf-8")
    assert "Publishing mode: **LIVE**" in report and "Patrol car launches into the sky" in report and "1 kept, 1 dropped" in report
