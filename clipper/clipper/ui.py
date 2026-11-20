"""The Clipper app: a page in your browser, served from this computer only.

    python -m clipper ui

It shows the finished clips, lets you edit the text that goes with each one and
post it to the connected accounts, starts a run and shows its progress, and
holds the settings. Nothing is reachable from other computers: the server
listens on 127.0.0.1, checks the Host header, and every action needs a token
that only the page it served knows.
"""

from __future__ import annotations

import json
import mimetypes
import os
import secrets
import subprocess
import sys
import threading
import time
import webbrowser
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse
from zoneinfo import ZoneInfo

from . import check, claude, config, metadata, pipeline, publish, review, settings
from .config import ROOT

PAGE = Path(__file__).with_name("ui.html")
TASK = "Clipper daily run"


class Problem(Exception):
    """Something the person using the app should be told."""


class App:
    def __init__(self, config_path: str | None = None, token: str | None = None):
        self.config_path = config_path
        self.token = token or secrets.token_urlsafe(24)
        self.log: deque[str] = deque(maxlen=400)
        self.proc: subprocess.Popen | None = None
        self.started = 0.0
        self.exit_code: int | None = None
        self._accounts: tuple[float, dict] | None = None
        self._lock = threading.Lock()
        self.checking = False
        self.check_results: list[dict] = []
        self.check_at = ""

    # --- reading ---------------------------------------------------------------

    def cfg(self) -> config.Config:
        return config.load(self.config_path)

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def accounts(self, cfg: config.Config, fresh: bool = False) -> dict:
        if fresh or self._accounts is None or time.time() - self._accounts[0] > 120:
            self._accounts = (time.time(), publish.accounts(cfg))
        return self._accounts[1]

    def daily(self) -> dict:
        script = ROOT.parent / "schedule.ps1"
        if os.name != "nt" or not script.exists():
            return {"available": False, "on": False}
        found = subprocess.run(["schtasks", "/Query", "/TN", TASK], capture_output=True, text=True)
        return {"available": True, "on": found.returncode == 0}

    def state(self) -> dict:
        cfg = self.cfg()
        ledger = pipeline.ledger_of(cfg)
        tz = ZoneInfo(cfg.schedule.timezone)
        out = cfg.path("out")
        clips = []
        for rec in sorted(ledger.clips.values(), key=lambda r: r.get("created", ""), reverse=True)[:80]:
            state = pipeline.clip_state(rec)
            meta = rec.get("meta")
            if state == "pending" or not meta:
                continue
            video = Path(rec.get("file", ""))
            section = cfg.section(rec.get("section", ""))
            try:
                media = "/media/" + video.resolve().relative_to(out.resolve()).as_posix() if video.exists() else ""
            except ValueError:
                media = ""
            clips.append({
                "id": rec["id"], "state": state, "title": meta.get("title", ""),
                "caption": meta.get("tiktok", {}).get("title", ""),
                "description": meta.get("youtube", {}).get("description", ""),
                "section": section.label, "color": section.color,
                "creator": rec.get("creator", ""), "origin": rec.get("origin", ""),
                "permission": rec.get("permission", ""),
                "seconds": round(rec.get("end", 0) - rec.get("start", 0)),
                "when": datetime.fromisoformat(rec["slot"]).astimezone(tz).strftime("%a %d %b, %H:%M") if rec.get("slot") else "",
                "media": media, "links": rec.get("links", {}), "views": rec.get("views", {}),
                "sent_to": rec.get("publish", {}).get("platforms", []), "error": rec.get("error", ""),
                "review": rec.get("review", {}).get("summary") or rec.get("review", {}).get("reason", ""),
                "seo": rec["seo"] if "seo" in rec else metadata.proof(cfg, meta),
            })
        latest = cfg.path("reports") / "latest.md"
        searches = [{"query": settings.search_text(s.url), "section": s.section or "moments"}
                    for s in cfg.sources if s.type == "ytdlp" and settings.search_text(s.url)]
        return {
            "clips": clips,
            "run": {"running": self.running(), "exit": self.exit_code, "log": list(self.log)[-200:],
                    "minutes": round((time.time() - self.started) / 60) if self.running() else 0},
            "accounts": self.accounts(cfg),
            "review": {"enabled": cfg.review.enabled, "has_key": bool(review.api_key(cfg)), "model": cfg.review.model,
                       "off": review.unavailable(cfg)},
            "daily": self.daily(),
            "check": {"running": self.checking, "results": list(self.check_results), "at": self.check_at,
                      "summary": check.summary(self.check_results) if self.check_results and not self.checking else ""},
            "report": latest.read_text(encoding="utf-8") if latest.exists() else "",
            "folder": str(out),
            "settings": {
                "handle": cfg.brand.handle, "min_len": cfg.moments.min_len, "max_len": cfg.moments.max_len,
                "max_per_source": cfg.moments.max_per_source, "slots": cfg.schedule.slots, "timezone": cfg.schedule.timezone,
                "platforms": cfg.publish.platforms, "profile": cfg.publish.profile,
                "auto_post": cfg.publish.mode == "live" and not cfg.publish.hold_for_review,
                "tiktok_post_mode": cfg.publish.tiktok_post_mode, "youtube_privacy": cfg.publish.youtube_privacy,
                "no_facecam": cfg.filters.no_facecam, "no_commentary": cfg.filters.no_commentary, "no_cutscenes": cfg.filters.no_cutscenes,
                "keep_downloads_days": cfg.housekeeping.keep_downloads_days, "keep_clips_days": cfg.housekeeping.keep_clips_days,
                "progress_bar": cfg.render.progress_bar, "searches": searches,
                "sections": [{"name": s.name, "label": s.label, "color": s.color} for s in cfg.sections],
            },
        }

    # --- actions ---------------------------------------------------------------

    def start_run(self) -> None:
        with self._lock:
            if self.running():
                raise Problem("a run is already in progress")
            cmd = [sys.executable, "-m", "clipper"]
            if self.config_path:
                cmd += ["--config", self.config_path]
            env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1"}
            self.log.clear()
            self.exit_code = None
            self.started = time.time()
            self.proc = subprocess.Popen(cmd + ["run"], cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         text=True, encoding="utf-8", errors="replace", bufsize=1)
            threading.Thread(target=self._follow, args=(self.proc,), daemon=True).start()

    def _follow(self, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip()
            if line and "setPreferableTarget" not in line:  # a harmless notice from the face detector
                self.log.append(line)
        self.exit_code = proc.wait()
        self.log.append("Finished." if self.exit_code == 0 else f"Finished with problems (code {self.exit_code}).")

    def check_command(self) -> list[str]:
        cmd = [sys.executable, "-m", "clipper"]
        if self.config_path:
            cmd += ["--config", self.config_path]
        return [*cmd, "check", "--json"]

    def start_check(self) -> None:
        """Try each part of the system on this computer; the page shows the results as they come.

        It runs as a process of its own, like a run: the speech model's memory is given back
        when it ends, and a fault in one part cannot take the app down with it.
        """
        with self._lock:
            if self.checking:
                raise Problem("a check is already in progress")
            if self.running():
                raise Problem("wait for the current run to finish, then check")
            self.checking, self.check_results, self.check_at = True, [], ""
        zone = self.cfg().schedule.timezone
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1"}
        try:
            proc = subprocess.Popen(self.check_command(), cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding="utf-8", errors="replace", bufsize=1)
        except OSError as exc:
            self.checking = False
            raise Problem(f"the check could not start: {exc}") from exc

        def follow() -> None:
            last = ""
            try:
                assert proc.stdout is not None
                for line in proc.stdout:
                    line = line.strip()
                    try:
                        result = json.loads(line) if line.startswith("{") else None
                    except json.JSONDecodeError:
                        result = None
                    if isinstance(result, dict) and "key" in result and "status" in result:
                        self.check_results = [r for r in self.check_results if r["key"] != result["key"]] + [result]
                    elif line:
                        last = line  # a notice from a library, or the reason the check stopped
                proc.wait()
            finally:
                stopped = "the check stopped before this part finished" + (f": {last[-200:]}" if last else "")
                results = [r if r["status"] != "running" else {**r, "status": "problem", "detail": stopped} for r in self.check_results]
                if not results:
                    results = [{"key": "check", "name": "System check", "status": "problem", "detail": f"the check could not run. {last[-200:]}".strip()}]
                self.check_results = results
                self.check_at = datetime.now(ZoneInfo(zone)).strftime("%a %d %b, %H:%M")
                self.checking = False

        threading.Thread(target=follow, daemon=True).start()

    def stop_run(self) -> None:
        if self.running() and self.proc is not None:
            self.proc.terminate()
            self.log.append("Stopped.")

    def change_settings(self, changes: dict) -> None:
        cfg = self.cfg()
        if "auto_post" in changes:  # one switch in the app, two settings underneath
            auto = bool(changes.pop("auto_post"))
            changes.setdefault("publish", {}).update({"mode": "live" if auto else "dry_run", "hold_for_review": not auto})
        before = settings.load(cfg.data_root)
        try:
            settings.update(cfg.data_root, changes)
            self.cfg()  # refuse settings the clipper cannot run with
        except (ValueError, config.ConfigError, TypeError) as exc:
            settings.save(cfg.data_root, before)
            raise Problem(str(exc)) from exc

    def connect(self, key: str, profile: str = "") -> dict:
        cfg = self.cfg()
        key = key.strip()
        if key:
            settings.set_secret(cfg.data_root, "upload_post_api_key", key)
        found = self.accounts(cfg, fresh=True)
        if found["error"]:
            raise Problem(f"The posting service did not accept the key: {found['error']}")
        names = [p["name"] for p in found["profiles"]]
        chosen = profile if profile in names else (names[0] if len(names) == 1 else cfg.publish.profile if cfg.publish.profile in names else "")
        if chosen:
            settings.update(cfg.data_root, {"publish": {"profile": chosen}})
        return found

    def disconnect(self) -> None:
        cfg = self.cfg()
        settings.set_secret(cfg.data_root, "upload_post_api_key", "")
        settings.update(cfg.data_root, {"publish": {"mode": "dry_run", "hold_for_review": True}})
        self._accounts = None

    def set_review_key(self, key: str) -> None:
        """Save the Claude API key for the automatic review, after checking that it works."""
        cfg = self.cfg()
        key = key.strip()
        if not key:
            raise Problem("Paste the key first.")
        if claude.unavailable(cfg).startswith("the anthropic package"):
            raise Problem("Claude is not installed yet. Run the installer again, then add the key.")
        why = claude.test(cfg, key)
        if why:
            raise Problem(why)
        settings.set_secret(cfg.data_root, "anthropic_api_key", key)
        settings.update(cfg.data_root, {"review": {"enabled": True}})

    def set_daily(self, on: bool, at: str = "06:00") -> None:
        script = ROOT.parent / "schedule.ps1"
        if os.name != "nt" or not script.exists():
            raise Problem("The daily run can only be switched on from the Windows install.")
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)]
        cmd += ["-At", at] if on else ["-Off"]
        done = subprocess.run(cmd, capture_output=True, text=True)
        if done.returncode != 0:
            raise Problem((done.stderr or done.stdout).strip()[-300:] or "could not change the daily run")

    def act(self, name: str, body: dict) -> dict:
        cfg = self.cfg()
        ledger = pipeline.ledger_of(cfg)
        try:
            if name == "run":
                if self.checking:
                    raise Problem("wait for the system check to finish, then start a run")
                self.start_run()
            elif name == "check":
                self.start_check()
            elif name == "stop":
                self.stop_run()
            elif name == "clip":
                pipeline.update_clip(cfg, ledger, body["id"], body)
            elif name == "drop":
                pipeline.drop_clip(cfg, ledger, body["id"])
            elif name == "post":
                pipeline.post_clip(cfg, ledger, body["id"], body.get("when", "now"), body.get("platforms") or None)
            elif name == "post-all":
                ready = sorted((r for r in ledger.clips.values() if pipeline.clip_state(r) == "ready" and r.get("meta")),
                               key=lambda r: -r.get("score", 0))
                for rec in ready:
                    pipeline.post_clip(cfg, ledger, rec["id"], "next", body.get("platforms") or None)
                return {"ok": True, "count": len(ready)}
            elif name == "settings":
                self.change_settings(body)
            elif name == "connect":
                return {"ok": True, "accounts": self.connect(body.get("api_key", ""), body.get("profile", ""))}
            elif name == "disconnect":
                self.disconnect()
            elif name == "review-key":
                self.set_review_key(str(body.get("api_key", "")))
            elif name == "review-key-remove":
                settings.set_secret(cfg.data_root, "anthropic_api_key", "")
            elif name == "daily":
                self.set_daily(bool(body.get("on")), str(body.get("at") or "06:00"))
            elif name == "open":
                target = cfg.path("out")
                if os.name == "nt":
                    os.startfile(str(target))  # noqa: S606 - opens the clips folder in Explorer
                else:
                    raise Problem(f"The clips are in {target}")
            else:
                raise Problem("unknown action")
        except KeyError as exc:
            raise Problem(f"not found: {exc}") from exc
        except publish.PublishError as exc:
            raise Problem(str(exc)) from exc
        return {"ok": True}


def handler_for(app: App):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args) -> None:
            pass

        def _local(self) -> bool:
            host = (self.headers.get("Host") or "").split(":")[0]
            return host in ("127.0.0.1", "localhost")

        def _send(self, code: int, body: bytes, kind: str, extra: dict | None = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, payload: dict) -> None:
            self._send(code, json.dumps(payload).encode(), "application/json")

        def _file(self, path: Path) -> None:
            size = path.stat().st_size
            kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            start, end = 0, size - 1
            ranged = self.headers.get("Range", "")
            if ranged.startswith("bytes="):
                first, _, last = ranged[6:].partition("-")
                if first.isdigit():
                    start = int(first)
                    end = min(int(last), size - 1) if last.isdigit() else size - 1
                elif last.isdigit():
                    start = max(0, size - int(last))
                if start > end or start >= size:
                    self._send(416, b"", kind, {"Content-Range": f"bytes */{size}"})
                    return
            length = end - start + 1
            self.send_response(206 if ranged else 200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            if ranged:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            with path.open("rb") as fh:
                fh.seek(start)
                while length > 0:
                    block = fh.read(min(1 << 16, length))
                    if not block:
                        break
                    try:
                        self.wfile.write(block)
                    except (BrokenPipeError, ConnectionResetError):
                        return  # the browser stopped the download (seeking in the video)
                    length -= len(block)

        def do_GET(self) -> None:
            if not self._local():
                self._send(403, b"", "text/plain")
                return
            path = urlparse(self.path).path
            if path == "/":
                page = PAGE.read_text(encoding="utf-8").replace("__TOKEN__", app.token)
                self._send(200, page.encode(), "text/html; charset=utf-8")
            elif path == "/api/state":
                if self.headers.get("X-Clipper-Token") != app.token:
                    self._json(403, {"error": "reload the page"})
                    return
                try:
                    self._json(200, app.state())
                except config.ConfigError as exc:
                    self._json(500, {"error": f"settings problem: {exc}"})
            elif path.startswith("/media/"):
                out = app.cfg().path("out").resolve()
                target = (out / unquote(path[len("/media/"):])).resolve()
                if out in target.parents and target.is_file():  # only files inside the clips folder
                    self._file(target)
                else:
                    self._send(404, b"", "text/plain")
            else:
                self._send(404, b"", "text/plain")

        def do_POST(self) -> None:
            if not self._local() or self.headers.get("X-Clipper-Token") != app.token:
                self._json(403, {"error": "reload the page"})
                return
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                name = urlparse(self.path).path.removeprefix("/api/")
                self._json(200, app.act(name, body if isinstance(body, dict) else {}))
            except Problem as exc:
                self._json(400, {"error": str(exc)})
            except (json.JSONDecodeError, config.ConfigError) as exc:
                self._json(400, {"error": str(exc)})

    return Handler


def serve(config_path: str | None = None, port: int = 8765, open_browser: bool = True, token: str | None = None) -> None:
    app = App(config_path, token)
    server = None
    for candidate in range(port, port + 20):  # the next free port, if the usual one is taken
        try:
            server = ThreadingHTTPServer(("127.0.0.1", candidate), handler_for(app))
            break
        except OSError:
            continue
    if server is None:
        raise SystemExit("could not find a free port for the app")
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"Clipper is open at {url}  (close this window to quit)", flush=True)
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.stop_run()
        server.server_close()
