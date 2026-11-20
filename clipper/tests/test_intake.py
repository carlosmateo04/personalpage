import functools
import subprocess
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

from clipper import config, intake, media


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@pytest.fixture
def web(tmp_path):
    """A local web server holding one small video."""
    site = tmp_path / "site"
    site.mkdir()
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=15:duration=12",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=12", "-c:v", "libx264", "-preset", "ultrafast",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", "-movflags", "+faststart", str(site / "vod.mp4")], check=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(site)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_folder_source_skips_processed_files(cfg, ledger, web, tmp_path):
    video = cfg.root / "inbox" / "a.mp4"
    video.write_bytes((tmp_path / "site" / "vod.mp4").read_bytes())
    (cfg.root / "inbox" / "notes.txt").write_text("not a video")
    items = intake.collect(cfg, ledger, lambda m: None)
    assert [i.name for i in items] == ["a.mp4"]
    ledger.sources[items[0].source_id] = {"status": "done"}
    assert intake.collect(cfg, ledger, lambda m: None) == []


def test_urls_source_downloads_new_links_once(cfg, ledger, web):
    (cfg.root / "urls.txt").write_text(f"# campaign files\n{web}/vod.mp4\n{web}/missing.mp4\n")
    cfg.sources = [config.Source(type="urls", file="urls.txt", creator="X", permission="Campaign: test")]
    logs = []
    items = intake.collect(cfg, ledger, logs.append)
    assert len(items) == 1 and items[0].origin == f"{web}/vod.mp4"
    assert abs(media.probe(items[0].path).duration - 12) < 0.5
    assert any("download failed" in line for line in logs)
    ledger.sources[items[0].source_id] = {"status": "done"}
    assert intake.collect(cfg, ledger, logs.append) == []  # done and failed links are not fetched again


def test_ytdlp_source_fetches_and_trims(cfg, ledger, web):
    src = config.Source(type="ytdlp", url=f"{web}/vod.mp4", creator="X", permission="Campaign: test", max_hours=5 / 3600)
    cfg.sources = [src]
    items = intake.collect(cfg, ledger, lambda m: None)
    assert len(items) == 1 and items[0].path.exists()
    assert media.probe(items[0].path).duration < 8  # only the first 5 seconds were kept
    ledger.sources[items[0].source_id] = {"status": "done"}
    assert intake.collect(cfg, ledger, lambda m: None) == []


def test_source_settings_are_checked():
    cfg = config.Config()
    cfg.sources = [config.Source(type="ytdlp")]
    with pytest.raises(config.ConfigError, match="url"):
        config.validate(cfg)
    cfg.sources = [config.Source(type="ytdlp", url="https://example.com/v", section="nope")]
    with pytest.raises(config.ConfigError, match="unknown section"):
        config.validate(cfg)
    cfg.sources = [config.Source(type="ytdlp", url="https://example.com/v", section="glitches")]
    config.validate(cfg)


class FakeSite:
    """Stands in for yt-dlp: a listing of four videos with their details."""

    def __init__(self, tmp_path):
        self.tmp, self.calls, self.bot_check = tmp_path, [], False
        self.videos = {
            "a": {"title": "bus glitch", "view_count": 9000, "duration": 30, "channel": "StreamerA", "categories": ["Grand Theft Auto VI"]},
            "b": {"title": "just chatting", "view_count": 8000, "duration": 30, "channel": "StreamerA", "categories": ["Just Chatting"]},
            "c": {"title": "few views", "view_count": 10, "duration": 30, "channel": "StreamerA", "categories": ["Grand Theft Auto VI"]},
            "d": {"title": "official trailer", "view_count": 7000, "duration": 30, "channel": "Rockstar Games", "categories": ["Grand Theft Auto VI"],
                  "license": "Creative Commons Attribution license (reuse allowed)"},
        }

    def __call__(self, args, timeout=0, tick=None):
        import json as j
        import subprocess as sp

        self.calls.append(args)
        url = args[-1]
        if "--flat-playlist" in args:
            entries = [{"id": k, "url": f"https://site/{k}", "title": v["title"], "view_count": v["view_count"], "duration": v["duration"]} for k, v in self.videos.items()]
            return sp.CompletedProcess(args, 0, j.dumps({"_type": "playlist", "entries": entries}), "")
        key = url.rsplit("/", 1)[1]
        if self.bot_check:
            return sp.CompletedProcess(args, 1, "", "ERROR: Sign in to confirm you\u2019re not a bot.")
        if "--skip-download" in args:
            return sp.CompletedProcess(args, 0, j.dumps({**self.videos[key], "id": key, "webpage_url": url}), "")
        out = self.tmp / f"{key}.mp4"
        out.write_bytes(b"video")
        return sp.CompletedProcess(args, 0, f"{out}\n", "")


def test_listed_videos_are_filtered_before_download(cfg, ledger, tmp_path, monkeypatch):
    site = FakeSite(tmp_path)
    monkeypatch.setattr(intake, "_yt", site)
    monkeypatch.setattr(intake, "PAUSE", 0)
    cfg.sources = [config.Source(type="ytdlp", url="https://site/clips", category="Grand Theft Auto VI", min_views=1000, max_items=5, section="glitches")]
    items = intake.collect(cfg, ledger, lambda m: None)
    assert [(i.title, i.creator, i.views, i.section) for i in items] == [("bus glitch", "StreamerA", 9000, "glitches")]
    assert items[0].permission == "" and items[0].origin == "https://site/a"
    downloads = [c[-1] for c in site.calls if "--print" in c]
    assert downloads == ["https://site/a"]  # only the one that passed every filter was downloaded
    reasons = {v["name"]: v["reason"] for v in ledger.sources.values()}
    assert reasons == {"just chatting": "not Grand Theft Auto VI (Just Chatting)", "official trailer": "blocked channel Rockstar Games"}
    ledger.sources[items[0].source_id] = {"status": "done"}
    site.calls.clear()
    assert intake.collect(cfg, ledger, lambda m: None) == []
    assert not any("--skip-download" in c for c in site.calls)  # nothing is looked up twice


def test_reuse_licence_is_recorded_as_the_basis(cfg, ledger, tmp_path, monkeypatch):
    site = FakeSite(tmp_path)
    site.videos = {"d": {**site.videos["d"], "channel": "SomeCreator"}}
    monkeypatch.setattr(intake, "_yt", site)
    cfg.sources = [config.Source(type="ytdlp", url="https://site/search")]
    items = intake.collect(cfg, ledger, lambda m: None)
    assert items[0].permission.startswith("Creative Commons")


def test_bot_check_stops_the_source_and_is_retried_later(cfg, ledger, tmp_path, monkeypatch):
    site = FakeSite(tmp_path)
    site.bot_check = True
    monkeypatch.setattr(intake, "_yt", site)
    monkeypatch.setattr(intake, "PAUSE", 0)
    cfg.sources = [config.Source(type="ytdlp", url="https://site/clips", max_items=5)]
    logs = []
    assert intake.collect(cfg, ledger, logs.append) == []
    assert sum("--skip-download" in c for c in site.calls) == 1  # asked once, then left alone
    assert [v["status"] for v in ledger.sources.values()] == ["blocked"]
    assert any("bot check" in line for line in logs)
    site.bot_check = False
    later = intake.collect(cfg, ledger, logs.append)  # blocked videos are tried again on a later run
    assert sorted(i.title for i in later) == ["bus glitch", "few views", "just chatting"]  # all but the blocked channel


def test_chat_log_formats(tmp_path):
    f = tmp_path / "chat.json"
    f.write_text('[1.5, {"t": 2}, {"content_offset_seconds": 3.25}, {"text": "no time"}]')
    assert intake.load_chat(f) == [1.5, 2.0, 3.25]
    assert intake.load_chat(tmp_path / "none.json") is None


def test_short_videos_are_fetched_whole_and_long_ones_trimmed():
    src = config.Source(type="ytdlp", url="https://site/x", max_hours=1)
    assert "--download-sections" not in intake.fetch_args(src, "https://site/x", __import__("pathlib").Path("/tmp"), duration=30)
    long = intake.fetch_args(src, "https://site/x", __import__("pathlib").Path("/tmp"), duration=7200)
    assert long[long.index("--download-sections") + 1] == "*0-3600"
    assert "--download-sections" in intake.fetch_args(src, "https://site/x", __import__("pathlib").Path("/tmp"))  # unknown length: play safe


def test_failed_fetches_are_retried_then_given_up(cfg, ledger, tmp_path, monkeypatch):
    import subprocess as sp

    site = FakeSite(tmp_path)
    real = site.__call__

    def broken(args, timeout=0, tick=None):
        if "--print" in args:  # every download fails
            site.calls.append(args)
            return sp.CompletedProcess(args, 1, "", "ERROR: ffmpeg exited with code 251")
        return real(args, timeout)

    monkeypatch.setattr(intake, "_yt", broken)
    monkeypatch.setattr(intake, "PAUSE", 0)
    cfg.sources = [config.Source(type="ytdlp", url="https://site/clips", max_items=5)]
    logs = []
    for run in range(1, 4):
        site.calls.clear()
        assert intake.collect(cfg, ledger, logs.append) == []
        assert sum("--print" in c for c in site.calls) == 2  # stops after two failures in a row
        statuses = sorted((v["status"], v["tries"]) for v in ledger.sources.values() if "tries" in v)
        assert statuses == [("failed" if run == 3 else "blocked", run)] * 2
    assert any("two fetches in a row failed" in line for line in logs)


def test_node_is_switched_on_for_the_downloader_when_deno_is_absent(monkeypatch):
    from clipper import bootstrap

    monkeypatch.setattr(bootstrap, "js_runtime", lambda: ("node", "22.1.0"))
    assert intake.runtime_args() == ["--js-runtimes", "node"]
    monkeypatch.setattr(bootstrap, "js_runtime", lambda: ("deno", "2.5.0"))
    assert intake.runtime_args() == []  # deno is used without asking
    monkeypatch.setattr(bootstrap, "js_runtime", lambda: ("", ""))
    assert intake.runtime_args() == []


def test_runtime_versions_are_checked(monkeypatch):
    import subprocess as sp

    from clipper import bootstrap

    def fake(found, outputs):
        monkeypatch.setattr(bootstrap.shutil, "which", lambda name: f"/bin/{name}" if name in found else None)
        monkeypatch.setattr(bootstrap.subprocess, "run", lambda cmd, **k: sp.CompletedProcess(cmd, 0, outputs[cmd[0].rsplit("/", 1)[1]], ""))

    fake({"node"}, {"node": "v22.23.5\n"})
    assert bootstrap.js_runtime() == ("node", "22.23.5")
    fake({"node"}, {"node": "v20.11.0\n"})
    assert bootstrap.js_runtime() == ("", "")  # too old for the downloader
    fake({"deno", "node"}, {"deno": "deno 2.5.1 (stable, release, x86_64)\nv8 13.5\n", "node": "v22.1.0\n"})
    assert bootstrap.js_runtime() == ("deno", "2.5.1")
    fake({"deno", "node"}, {"deno": "deno 1.46.0\n", "node": "v22.1.0\n"})
    assert bootstrap.js_runtime() == ("node", "22.1.0")  # old deno, usable node


def test_long_downloads_report_progress_and_give_up_cleanly(monkeypatch, tmp_path):
    import sys

    slow = [sys.executable, "-c", "import time; print('started', flush=True); time.sleep(30)"]
    monkeypatch.setattr(intake, "runtime_args", lambda: [])
    monkeypatch.setattr(intake, "HEARTBEAT", 0.2)
    real_popen = intake.subprocess.Popen
    monkeypatch.setattr(intake.subprocess, "Popen", lambda cmd, **kw: real_popen(slow, **{k: v for k, v in kw.items() if k != "env"}))
    ticks = []
    got = intake._yt(["ignored"], timeout=1, tick=ticks.append)
    assert got.returncode == 124 and "gave up" in got.stderr  # stopped, not raised
    assert len(ticks) >= 2 and ticks == sorted(ticks)  # it reported in while waiting

    quick = [sys.executable, "-c", "import sys; print('hello'); print('oops', file=sys.stderr); sys.exit(3)"]
    monkeypatch.setattr(intake.subprocess, "Popen", lambda cmd, **kw: real_popen(quick, **{k: v for k, v in kw.items() if k != "env"}))
    got = intake._yt(["ignored"])
    assert (got.returncode, got.stdout.strip(), got.stderr.strip()) == (3, "hello", "oops")
