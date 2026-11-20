"""The automatic review, against a local stand-in for the Claude API.

The stand-in pins down the requests this code sends and how it handles each kind
of answer; what a real model says about a real clip is not tested here.
"""

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from clipper import cli, metadata, review, settings

GOOD_KEY = "sk-ant-test-good"


class FakeClaude(BaseHTTPRequestHandler):
    """Answers /v1/messages with the next queued reply and remembers what it was sent."""

    replies: list = []
    seen: list = []

    def log_message(self, *args) -> None:
        pass

    def _send(self, status: int, body: dict) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _key_ok(self) -> bool:
        if GOOD_KEY in (self.headers.get("x-api-key"), (self.headers.get("Authorization") or "").removeprefix("Bearer ")):
            return True  # the SDK sends the key in one header or the other, depending on its version
        self._send(401, {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}})
        return False

    def do_GET(self) -> None:
        if self._key_ok():
            self._send(200, {"id": self.path.rsplit("/", 1)[-1], "type": "model", "display_name": "Test", "created_at": "2026-01-01T00:00:00Z"})

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).seen.append({"path": self.path, "beta": self.headers.get("anthropic-beta", ""), "body": body})
        if not self._key_ok():
            return
        reply = type(self).replies.pop(0)
        if isinstance(reply, int):
            self._send(reply, {"type": "error", "error": {"type": "invalid_request_error", "message": "no"}})
            return
        stop = reply.pop("_stop", "end_turn") if isinstance(reply, dict) else "end_turn"
        content = [{"type": "thinking", "thinking": "", "signature": "sig"}]
        if stop != "refusal":
            content.append({"type": "text", "text": reply if isinstance(reply, str) else json.dumps(reply)})
        self._send(200, {"id": "msg_1", "type": "message", "role": "assistant", "model": body["model"], "content": content,
                         "stop_reason": stop, "stop_sequence": None, "usage": {"input_tokens": 100, "output_tokens": 50}})


KEEP = {"keep": True, "reason": "Pure gameplay: a police chase that ends in a crash.", "section": "glitches",
        "title": "police car launches off the pier.", "hook": "It just took off", "summary": "A police car hits a ramp and flies into the sea."}
DROP = {"keep": False, "reason": "A webcam box with a person is visible in every frame.", "section": "moments",
        "title": "", "hook": "", "summary": ""}


@pytest.fixture
def claude(monkeypatch):
    FakeClaude.replies, FakeClaude.seen = [], []
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeClaude)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("ANTHROPIC_BASE_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("ANTHROPIC_API_KEY", GOOD_KEY)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    yield FakeClaude
    server.shutdown()


def plan_with(cfg, n: int = 2):
    """A plan.json with n planned clips and their rule-based titles, as `plan` writes it."""
    run = cfg.path("out") / "20261120-000000"
    run.mkdir(parents=True)
    clips = []
    for i in range(n):
        cid = f"c{i}"
        sheet = run / f"{cid}.jpg"
        sheet.write_bytes(b"\xff\xd8\xff\xe0 pretend jpeg " + bytes([i]))
        section = cfg.section("moments")
        clips.append({
            "id": cid, "source_name": "GTA 6 free roam (no commentary)", "creator": "RawPlay", "origin": "https://example.com/v",
            "section": "moments", "start": 100.0 * i, "end": 100.0 * i + 90, "text": "Pull over! Pull over now!", "sheet": str(sheet),
            "meta": metadata.compose(cfg, cid, "RawPlay", section, "GTA 6 Highlight", "GTA 6 Highlight", "https://example.com/v"),
            "drop": False,
        })
    path = run / "plan.json"
    path.write_text(json.dumps({"run": "20261120-000000", "clips": clips, "rejected": [], "problems": []}), encoding="utf-8")
    return path


def test_what_is_sent_for_a_clip(cfg, claude):
    path = plan_with(cfg, 1)
    clip = json.loads(path.read_text(encoding="utf-8"))["clips"][0]
    claude.replies.append(dict(KEEP))
    verdict = review.ask(cfg, clip)
    assert verdict["keep"] is True and verdict["section"] == "glitches"
    sent = claude.seen[0]
    body = sent["body"]
    assert sent["path"].startswith("/v1/messages") and "server-side-fallback-2026-07-01" in sent["beta"]
    assert body["model"] == "claude-opus-5-5" and body["fallbacks"] == "default" and "thinking" not in body
    assert body["output_config"]["effort"] == "low"
    schema = body["output_config"]["format"]
    assert schema["type"] == "json_schema" and schema["schema"]["required"] == ["keep", "reason", "section", "title", "hook", "summary"]
    assert "pure gameplay" in body["system"]
    image, text = body["messages"][0]["content"]
    assert image["type"] == "image" and image["source"]["media_type"] == "image/jpeg"
    assert base64.standard_b64decode(image["source"]["data"]).startswith(b"\xff\xd8\xff")
    assert "GTA 6 free roam (no commentary)" in text["text"] and "Pull over! Pull over now!" in text["text"] and "90 seconds" in text["text"]


def test_a_model_without_the_fallback_is_asked_plainly(cfg, claude):
    cfg.review.model = "claude-haiku-5-5"
    clip = json.loads(plan_with(cfg, 1).read_text(encoding="utf-8"))["clips"][0]
    claude.replies.append(dict(KEEP))
    review.ask(cfg, clip)
    assert "fallbacks" not in claude.seen[0]["body"] and "server-side-fallback" not in claude.seen[0]["beta"]


def test_kept_clip_gets_the_reviews_title_and_dropped_clip_is_marked(cfg, claude):
    path = plan_with(cfg, 2)
    claude.replies += [dict(KEEP), dict(DROP)]
    lines = []
    result = review.apply(cfg, path, lines.append)
    assert result == {"kept": 1, "dropped": 1, "failed": 0}
    kept, cut = json.loads(path.read_text(encoding="utf-8"))["clips"]
    meta = kept["meta"]
    assert meta["title"] == "Police car launches off the pier" and meta["hook"] == "It just took off"
    assert kept["section"] == "glitches" and meta["badge"] == "GLITCH" and not kept["drop"]
    assert meta["youtube"]["title"] == "Police car launches off the pier | GTA 6 Glitch #shorts"
    assert "A police car hits a ramp and flies into the sea." in meta["youtube"]["description"]
    assert "Credit: RawPlay. Original: https://example.com/v" in meta["youtube"]["description"]
    for platform in ("tiktok", "instagram", "facebook"):
        assert meta[platform]["title"].startswith("Police car launches off the pier • Credit: RawPlay #gta6glitch")
    assert kept["review"] == {"by": "claude-opus-5-5", "keep": True, "reason": KEEP["reason"], "summary": KEEP["summary"]}
    assert cut["drop"] is True and cut["review"]["keep"] is False and "webcam" in cut["review"]["reason"]
    assert cut["meta"]["title"] == "GTA 6 Highlight"  # untouched
    assert any("dropped by the review" in line for line in lines)

    review.apply(cfg, path, lines.append)  # a second pass asks nothing again
    assert len(claude.seen) == 2


def test_a_rejected_key_stops_the_review_and_the_rule_based_titles_stay(cfg, claude, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-wrong")
    path = plan_with(cfg, 2)
    result = review.apply(cfg, path, lambda m: None)
    assert result == {"kept": 0, "dropped": 0, "failed": 1} and len(claude.seen) == 1  # the second clip is not tried
    data = json.loads(path.read_text(encoding="utf-8"))
    assert all(c["meta"]["title"] == "GTA 6 Highlight" and not c["drop"] and "review" not in c for c in data["clips"])
    assert "key was not accepted" in data["problems"][0]


@pytest.mark.parametrize("reply, said", [
    ({"_stop": "refusal"}, "declined"),
    ({**KEEP, "_stop": "max_tokens"}, "ran out of room"),
    ("this is not json", "expected form"),
    ({"keep": "yes"}, "expected form"),
    (400, "error (400)"),
])
def test_an_unusable_answer_leaves_that_clip_alone_and_moves_on(cfg, claude, reply, said):
    path = plan_with(cfg, 2)
    claude.replies += [reply, dict(KEEP)]
    result = review.apply(cfg, path, lambda m: None)
    assert result == {"kept": 1, "dropped": 0, "failed": 1}
    first, second = json.loads(path.read_text(encoding="utf-8"))["clips"]
    assert first["meta"]["title"] == "GTA 6 Highlight" and not first["drop"] and "review" not in first
    assert second["meta"]["title"] == "Police car launches off the pier"
    assert said in json.loads(path.read_text(encoding="utf-8"))["problems"][0]


def test_an_empty_or_unknown_answer_falls_back_sensibly(cfg, claude):
    path = plan_with(cfg, 1)
    claude.replies.append({**KEEP, "title": "  ", "hook": "", "section": "somewhere"})
    review.apply(cfg, path, lambda m: None)
    clip = json.loads(path.read_text(encoding="utf-8"))["clips"][0]
    assert clip["meta"]["title"] == "GTA 6 Highlight" and clip["meta"]["hook"] == "GTA 6 Highlight" and clip["section"] == "moments"


def test_why_the_review_is_off(cfg, tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPPER_DATA", str(tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(settings, "_keyring", lambda: None)
    assert "no Claude API key" in review.unavailable(cfg) and not review.available(cfg)
    settings.set_secret(cfg.data_root, "anthropic_api_key", GOOD_KEY)
    assert review.unavailable(cfg) == "" and review.api_key(cfg) == GOOD_KEY
    cfg.review.enabled = False
    assert "switched off" in review.unavailable(cfg)


def test_run_without_a_key_says_so_and_carries_on(cfg, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLIPPER_DATA", str(tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(settings, "_keyring", lambda: None)
    path = plan_with(cfg, 1)
    before = path.read_text(encoding="utf-8")
    cli._review(cfg, path)
    assert "automatic review skipped: no Claude API key" in capsys.readouterr().out and path.read_text(encoding="utf-8") == before


def test_checking_a_key(claude):
    assert review.check_key(GOOD_KEY, "claude-opus-5-5") == ""
    assert "did not accept" in review.check_key("sk-ant-wrong", "claude-opus-5-5")


def test_frames_for_a_long_clip_come_as_a_grid(tmp_path):
    import subprocess

    from clipper import media

    video = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=10:duration=90",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(video)], check=True)
    sheet = media.probe(media.contact_sheet(video, 0, 90, tmp_path / "sheet.jpg", shots=8))
    assert sheet.width == 1600 and 440 <= sheet.height <= 460  # two rows of four 400-wide frames


def test_the_app_saves_a_working_key_and_refuses_a_bad_one(claude, tmp_path, monkeypatch):
    from clipper import ui

    monkeypatch.setenv("CLIPPER_DATA", str(tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(settings, "_keyring", lambda: None)
    (tmp_path / "config.toml").write_text('[[sources]]\ntype = "folder"\npath = "inbox"\n', encoding="utf-8")
    app = ui.App(str(tmp_path / "config.toml"), "t")
    assert app.state()["review"] == {"enabled": True, "has_key": False, "model": "claude-opus-5-5",
                                     "off": "no Claude API key is saved (add one in the app under Accounts)"}
    with pytest.raises(ui.Problem, match="did not accept"):
        app.act("review-key", {"api_key": "sk-ant-wrong"})
    with pytest.raises(ui.Problem, match="Paste the key"):
        app.act("review-key", {"api_key": "  "})
    assert not settings.get_secret(tmp_path, "anthropic_api_key")
    app.act("review-key", {"api_key": f"  {GOOD_KEY}\n"})
    state = app.state()["review"]
    assert state["has_key"] and state["off"] == "" and settings.get_secret(tmp_path, "anthropic_api_key") == GOOD_KEY
    assert GOOD_KEY not in json.dumps(app.state())  # the key never goes back to the page
    app.act("settings", {"review": {"enabled": False}})
    assert "switched off" in app.state()["review"]["off"]
    app.act("review-key-remove", {})
    assert not app.state()["review"]["has_key"]
