"""The system check: each part tried for real against local stand-ins for the outside services."""

import functools
import subprocess
import threading
import time
from http.server import ThreadingHTTPServer

import pytest

from clipper import check, config, intake, settings, transcribe, ui
from test_intake import Quiet
from test_publish import FakeApi
from standins import GOOD_KEY, KEEP, FakeClaude

SAMPLE = config.Config().models_dir / transcribe.ASR_DIR / "test_wavs" / "0.wav"
needs_models = pytest.mark.skipif(not SAMPLE.exists(), reason="speech models not installed")


def serve(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


@pytest.fixture
def bare(cfg, tmp_path, monkeypatch):
    """A config with no keys saved and a JavaScript runtime reported as present."""
    monkeypatch.setenv("CLIPPER_DATA", str(tmp_path))
    for name in ("ANTHROPIC_API_KEY", "UPLOAD_POST_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(settings, "_keyring", lambda: None)
    monkeypatch.setattr(check, "js_runtime", lambda: ("deno", "2.5.0"))
    return cfg


def by_key(results):
    return {r["key"]: (r["status"], r["detail"]) for r in results}


@needs_models
def test_every_part_is_tried_for_real_and_reported(bare, tmp_path, monkeypatch):
    from upload_post import UploadPostClient

    site = tmp_path / "site"
    site.mkdir()
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=15:duration=30",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=30", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", "-movflags", "+faststart", str(site / "GTA 6 free roam no commentary.mp4")], check=True)
    web, web_url = serve(functools.partial(Quiet, directory=str(site)))
    FakeClaude.replies, FakeClaude.seen = [dict(KEEP, keep=False, reason="A colour test pattern, not gameplay.")], []
    claude, claude_url = serve(FakeClaude)
    FakeApi.calls = []
    api, api_url = serve(FakeApi)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", claude_url)
    monkeypatch.setenv("ANTHROPIC_API_KEY", GOOD_KEY)
    monkeypatch.setenv("UPLOAD_POST_API_KEY", "test-key")
    monkeypatch.setattr(UploadPostClient, "BASE_URL", f"{api_url}/api")
    bare.sources = [config.Source(type="ytdlp", url=f"{web_url}/GTA%206%20free%20roam%20no%20commentary.mp4", title_any=["no commentary"])]
    bare.publish.profile, bare.publish.platforms = "gta6clips", ["tiktok"]
    seen = []
    try:
        results = by_key(check.run(bare, seen.append))
    finally:
        for server in (web, claude, api):
            server.shutdown()

    assert [r["key"] for r in seen if r["status"] == "running"] == [key for key, _ in check.PARTS]  # each part announces itself
    assert {key: status for key, (status, _) in results.items()} == dict.fromkeys(("tools", "lookup", "fetch", "clip", "review", "posting"), "ok")
    assert "listed 1 videos" in results["lookup"][1] and "1 pass the title, views and length rules" in results["lookup"][1]
    assert "downloaded the first 15 seconds" in results["fetch"][1]
    assert "1080x1920" in results["clip"][1] and "words heard and captioned" in results["clip"][1]
    assert "rightly dropped the test pattern: A colour test pattern, not gameplay." in results["review"][1]
    assert 'the profile "gta6clips" has tiktok connected. Nothing was posted' in results["posting"][1]
    sent = FakeClaude.seen[0]["body"]["messages"][0]["content"]
    assert sent[0]["type"] == "image" and "test pattern" in sent[1]["text"]  # a real request, with frames of the test clip
    assert all(method == "GET" for method, *_ in FakeApi.calls)  # the posting check only reads
    assert not (bare.path("work") / "check").exists()  # nothing is left behind
    assert check.summary(check.run(bare, only=["tools"])) == "all 1 parts work"


def test_parts_that_are_not_set_up_are_skipped_with_the_reason(bare):
    bare.sources = [config.Source(type="folder", path="inbox")]
    results = by_key(check.run(bare, only=["lookup", "fetch", "review", "posting"]))
    assert results["lookup"] == ("skipped", "no online source is set up; only the inbox folder is used")
    assert results["fetch"][0] == "skipped"
    assert results["review"][0] == "skipped" and "no Claude API key is saved" in results["review"][1]
    assert results["posting"][0] == "skipped" and "no Upload-Post key is saved" in results["posting"][1]


def test_a_site_refusing_this_computer_is_said_plainly(bare, monkeypatch):
    bare.sources = [config.Source(type="ytdlp", url="https://www.youtube.com/results?search_query=gta+6+no+commentary")]
    monkeypatch.setattr(intake, "listing", lambda src: ([], "ERROR: Sign in to confirm you’re not a bot."))
    results = by_key(check.run(bare, only=["lookup", "fetch"]))
    assert results["lookup"][0] == "problem" and "YouTube is refusing automated requests from this computer" in results["lookup"][1]
    assert results["fetch"][0] == "skipped"

    # listed, but the video itself is refused: the state this was built in
    monkeypatch.setattr(intake, "listing", lambda src: ([{"id": "a", "title": "GTA 6 no commentary", "url": "https://www.youtube.com/watch?v=a"}], ""))
    monkeypatch.setattr(intake, "details", lambda url: ({}, "ERROR: [youtube] a: Sign in to confirm you’re not a bot."))
    results = by_key(check.run(bare, only=["lookup", "fetch"]))
    assert results["lookup"][0] == "ok" and 'for "gta 6 no commentary"' in results["lookup"][1]
    assert results["fetch"] == ("problem", "YouTube asked to confirm this computer is not a bot. Downloads will not work from this connection for now.")


def test_a_part_that_breaks_is_reported_and_the_rest_still_run(bare, monkeypatch):
    monkeypatch.setitem(check._RUN, "lookup", lambda bench: (_ for _ in ()).throw(RuntimeError("boom")))
    results = by_key(check.run(bare, only=["lookup", "posting"]))
    assert results["lookup"] == ("problem", "RuntimeError: boom") and results["posting"][0] == "skipped"
    assert check.summary([{"status": s} for s in ("ok", "problem", "skipped")]) == "1 part(s) need attention, 1 work, 1 not set up"


def wait_for_check(app):
    for _ in range(600):
        if not app.checking:
            return app.state()["check"]
        time.sleep(0.1)
    raise AssertionError("the check did not finish")


def test_the_app_runs_the_check_as_its_own_process_and_shows_it(bare, tmp_path, monkeypatch):
    import sys

    (tmp_path / "config.toml").write_text('[[sources]]\ntype = "folder"\npath = "inbox"\n', encoding="utf-8")
    app = ui.App(str(tmp_path / "config.toml"), "t")
    go = tmp_path / "go"
    script = ("import json, os, sys, time\n"
              "print('a notice from a library', flush=True)\n"
              "print(json.dumps({'key': 'tools', 'name': 'Tools on this computer', 'status': 'running', 'detail': ''}), flush=True)\n"
              f"while not os.path.exists({str(go)!r}): time.sleep(0.05)\n"
              "print(json.dumps({'key': 'tools', 'name': 'Tools on this computer', 'status': 'ok', 'detail': 'all there', 'seconds': 0.1}), flush=True)\n")
    monkeypatch.setattr(ui.App, "check_command", lambda self: [sys.executable, "-c", script])
    assert app.state()["check"] == {"running": False, "results": [], "at": "", "summary": ""}
    app.act("check", {})
    for _ in range(200):
        if app.check_results:
            break
        time.sleep(0.05)
    state = app.state()["check"]
    assert state["running"] and state["results"][0]["status"] == "running" and state["summary"] == ""
    with pytest.raises(ui.Problem, match="already in progress"):
        app.act("check", {})
    with pytest.raises(ui.Problem, match="wait for the system check"):
        app.act("run", {})
    go.write_text("", encoding="utf-8")
    state = wait_for_check(app)
    assert not state["running"] and state["at"] and state["summary"] == "all 1 parts work"
    assert [(r["key"], r["status"]) for r in state["results"]] == [("tools", "ok")]

    # A check that dies part-way says so instead of showing a part as checking for ever.
    dies = ("import json, sys\n"
            "print(json.dumps({'key': 'clip', 'name': 'Cutting, captions and rendering', 'status': 'running', 'detail': ''}), flush=True)\n"
            "print('Fatal: out of memory', flush=True)\nsys.exit(3)\n")
    monkeypatch.setattr(ui.App, "check_command", lambda self: [sys.executable, "-c", dies])
    app.act("check", {})
    state = wait_for_check(app)
    assert state["results"] == [{"key": "clip", "name": "Cutting, captions and rendering", "status": "problem",
                                 "detail": "the check stopped before this part finished: Fatal: out of memory"}]
    monkeypatch.setattr(ui.App, "check_command", lambda self: [sys.executable, "-c", "print('no such thing')"])
    app.act("check", {})
    assert wait_for_check(app)["results"][0]["detail"] == "the check could not run. no such thing"


@needs_models
def test_the_real_check_command_feeds_the_app(bare, tmp_path):
    """The app starting the real `check --json` process, on a setup with no online source and no keys."""
    (tmp_path / "config.toml").write_text('[[sources]]\ntype = "folder"\npath = "inbox"\n', encoding="utf-8")
    app = ui.App(str(tmp_path / "config.toml"), "t")
    app.act("check", {})
    state = wait_for_check(app)
    found = {r["key"]: r["status"] for r in state["results"]}
    assert found["clip"] == "ok" and found["lookup"] == found["fetch"] == found["review"] == found["posting"] == "skipped", state
    assert [r["key"] for r in state["results"]] == [key for key, _ in check.PARTS]
    assert "not set up yet" in state["summary"] or "need attention" in state["summary"]  # the tools part depends on this machine
