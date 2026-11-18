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


def test_non_folder_sources_must_state_permission():
    cfg = config.Config()
    cfg.sources = [config.Source(type="ytdlp", url="https://example.com/v")]
    with pytest.raises(config.ConfigError, match="permission"):
        config.validate(cfg)


def test_chat_log_formats(tmp_path):
    f = tmp_path / "chat.json"
    f.write_text('[1.5, {"t": 2}, {"content_offset_seconds": 3.25}, {"text": "no time"}]')
    assert intake.load_chat(f) == [1.5, 2.0, 3.25]
    assert intake.load_chat(tmp_path / "none.json") is None
