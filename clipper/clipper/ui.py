"""The Clipper app: a page in your browser, served from this computer only.

    python -m clipper ui

It shows the finished clips, lets you edit the text that goes with each one and
post it to the connected accounts, starts a run and shows its progress, and
holds the settings. Nothing is reachable from other computers: the server
listens on 127.0.0.1, checks the Host header, and every action needs a token
that only the page it served knows.
"""

from __future__ import annotations

import html
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
from urllib.parse import parse_qs, unquote, urlparse
from zoneinfo import ZoneInfo

from . import check, claude, config, connectors, metadata, monetize, outbox, pipeline, publish, review, settings, signin, variants
from .connectors import tiktok
from .connectors import ConnectorError
from .config import ROOT

PAGE = Path(__file__).with_name("ui.html")
TASK = "Clipper daily run"


RETURNED = """<!doctype html><meta charset="utf-8"><title>Clipper</title>
<body style="font:16px system-ui,sans-serif;background:#0f1115;color:#e8eaed;display:grid;place-items:center;height:100vh;margin:0">
<div style="max-width:32rem;padding:24px;text-align:center"><h1 style="font-size:22px">__TITLE__</h1><p>__MESSAGE__</p>
<p style="color:#9aa0a6">You can close this tab and go back to Clipper.</p></div></body>"""


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
        self.port = 8765  # set by serve(); the platforms send the browser back to this port after a sign-in
        self.pending: dict[str, dict] = {}  # sign-ins under way: state -> what to finish them with
        self.jobs: dict[str, str] = {}  # clips Claude is editing again right now
        self.job_errors: dict[str, str] = {}
        self.posting_log: deque[str] = deque(maxlen=60)
        self.login: signin.Login | None = None  # a sign-in with Claude that is under way or just finished
        self.login_note = ""  # what the last sign-in came to, for the page
        self._refreshed = 0.0
        self._cache: dict[str, tuple[float, object]] = {}

    # --- reading ---------------------------------------------------------------

    def cfg(self) -> config.Config:
        return config.load(self.config_path)

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def accounts(self, cfg: config.Config, fresh: bool = False) -> dict:
        if fresh or self._accounts is None or time.time() - self._accounts[0] > 120:
            self._accounts = (time.time(), publish.accounts(cfg))
        return self._accounts[1]

    def cached(self, name: str, seconds: float, make):
        """A value that is a little work to get and is asked for every couple of seconds by the page."""
        held = self._cache.get(name)
        if held is None or time.time() - held[0] > seconds:
            held = (time.time(), make())
            self._cache[name] = held
        return held[1]

    def posting(self, cfg: config.Config) -> dict:
        """Every platform as the Accounts tab shows it: how it is reached, the account, and what its sign-in needs."""
        how = outbox.routes(cfg)
        platforms = []
        for name in connectors.names():
            conn = connectors.get(name, cfg)
            account = conn.account()
            platforms.append({
                "name": name, "label": conn.label, "sign_in": conn.key, "chosen": name in cfg.publish.platforms, "route": how.get(name, ""),
                "account": account.get("name", ""), "problem": account.get("problem", ""), "expires": account.get("expires", ""),
                "app": conn.app(), "redirect": conn.redirect_uri(self.port), "schedules": conn.native_schedule,
                "via_service": name in cfg.publish.prefer_service,
            })
        meta = settings.account(cfg.data_root, "meta")
        tik = settings.account(cfg.data_root, "tiktok")
        return {"platforms": platforms, "pages": meta.get("pages", []), "page": meta.get("page_id", ""), "config_id": meta.get("config_id", ""),
                "tiktok_direct": cfg.publish.tiktok_direct,
                "tiktok": {"mode": cfg.publish.tiktok_direct, "connected_for": tik.get("mode", ""), "upload_page": tiktok.UPLOAD_PAGE,
                           "audiences": [{"id": key, "label": label} for key, label in tiktok.AUDIENCES.items()
                                         if not tik.get("audiences") or key in tik["audiences"]],
                           "defaults": connectors.get("tiktok", cfg).options()}}

    def ai(self, cfg: config.Config) -> dict:
        return {
            "enabled": cfg.ai.enabled, "provider": cfg.ai.provider, "model": cfg.ai.model, "off": claude.unavailable(cfg),
            "models": [{"id": key, "label": label, "price": f"${price_in:g} in, ${price_out:g} out per million tokens"}
                       for key, (label, price_in, price_out) in claude.MODELS.items()],
            "has_key": bool(claude.api_key(cfg)), "aws_profile": cfg.ai.aws_profile, "aws_region": cfg.ai.aws_region,
            "aws_profiles": self.cached("aws", 30, claude.aws_profiles), "scout": cfg.ai.scout, "edit": cfg.ai.edit,
            "direction": cfg.ai.direction, "usage": claude.usage(cfg),
            "accounts": claude.accounts(cfg), "can_sign_in": bool(self.cached("ant", 30, signin.tool)),
            "signin": {"waiting": self.login is not None and not self.login.done, "url": self.login.url if self.login else "",
                       "note": self.login_note},
        }

    # --- Claude accounts -------------------------------------------------------

    def claude_signin(self, name: str) -> dict:
        """Start a sign-in with Claude. Returns the page to approve on; the account is saved when the approval comes back."""
        cfg = self.cfg()
        if self.login is not None and not self.login.done:
            self.login.cancel()
        taken = set(signin.profiles()) | {a.get("profile", "") for a in claude.accounts(cfg)}
        profile = next(f"clipper-{n}" for n in range(1, 1000) if f"clipper-{n}" not in taken)
        try:
            login = signin.Login(profile).start()
        except (RuntimeError, OSError) as exc:
            raise Problem(str(exc)) from exc
        self.login, self.login_note = login, ""
        deadline = time.time() + 20
        while not login.url and not login.done and time.time() < deadline:
            time.sleep(0.05)
        if not login.url:
            login.cancel()
            raise Problem(login.error or "The sign-in tool gave no page to sign in on. Try again, or use an API key.")

        def finish() -> None:
            while not login.done:
                time.sleep(0.2)
            if self.login is not login:
                return  # another sign-in was started in the meantime
            if not login.ok:
                self.login_note = login.error
            else:
                about = " \u00b7 ".join(v for v in (login.details.get("organization", ""), login.details.get("email", "")) if v)
                label = name.strip() or login.details.get("organization", "") or "Claude account"
                claude.add_account(self.cfg(), label, "login", profile=profile, detail=about[:120])
                problem = claude.test(self.cfg())
                self.login_note = f"Signed in: {label}." + (f" {problem}" if problem else "")
            self._cache.clear()

        threading.Thread(target=finish, daemon=True).start()
        return {"ok": True, "url": login.url}

    def claude_add(self, body: dict) -> str:
        """Save a Claude account that is an API key or an AWS profile, and start using it. Returns a problem found, or ""."""
        cfg = self.cfg()
        kind, name = str(body.get("kind", "")), str(body.get("name", ""))
        if kind == "key":
            key = str(body.get("api_key", "")).strip()
            if not key:
                raise Problem("Paste the key first.")
            if claude.unavailable(cfg).startswith("the anthropic package"):
                raise Problem("Claude is not installed yet. Run the installer again, then add the key.")
            cfg.ai.provider, cfg.ai.enabled = "anthropic", True  # for the test only; nothing is saved before the key works
            why = claude.test(cfg, key)
            if why:
                raise Problem(why)
            claude.add_account(self.cfg(), name, "key", key=key)
            return ""
        if kind == "bedrock":
            profile = str(body.get("aws_profile", "")).strip()
            if not profile:
                raise Problem("Choose the AWS profile to sign in with.")
            claude.add_account(cfg, name or f"Amazon Bedrock ({profile})", "bedrock", aws_profile=profile,
                               aws_region=str(body.get("aws_region") or cfg.ai.aws_region or "us-east-1").strip())
            now = self.cfg()
            return self.claude_problem(now)  # an expired AWS sign-in is said, the account is kept
        raise Problem("unknown kind of account")

    @staticmethod
    def claude_problem(cfg: config.Config) -> str:
        """What is wrong with the Claude account in use, as a sentence, or "" when Claude answers with it."""
        why = claude.unavailable(cfg)
        return f"Claude cannot be used yet: {why}." if why else claude.test(cfg)

    def claude_use(self, ident: str) -> str:
        """Switch to another saved Claude account. Returns a problem found with it, or ""."""
        claude.use_account(self.cfg(), ident)
        now = self.cfg()
        return self.claude_problem(now)

    def daily(self) -> dict:
        script = ROOT.parent / "schedule.ps1"
        if os.name != "nt" or not script.exists():
            return {"available": False, "on": False}
        found = subprocess.run(["schtasks", "/Query", "/TN", TASK], capture_output=True, text=True)
        return {"available": True, "on": found.returncode == 0}

    @staticmethod
    def _media(out: Path, video: Path) -> str:
        """The address the page plays a clip file from, or "" when the file is gone."""
        try:
            return "/media/" + video.resolve().relative_to(out.resolve()).as_posix() if video.exists() else ""
        except ValueError:
            return ""

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
            media = self._media(out, video)
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
                "score": rec.get("review", {}).get("score"), "picked": (rec.get("scout") or {}).get("why", ""),
                "seo": rec["seo"] if "seo" in rec else metadata.proof(cfg, meta),
                "posts": {name: {"state": post["state"], "link": post.get("link", ""), "views": post.get("views"),
                                 "note": post.get("error") or post.get("note", ""), "via": post.get("via", "")}
                          for name, post in (rec.get("posts") or {}).items()},
                "cover": media.replace(".mp4", ".cover.jpg") if media and pipeline.cover_of(video).exists() else "",
                "busy": self.jobs.get(rec["id"], ""), "edit_error": self.job_errors.get(rec["id"], ""),
                "versions": [{"seconds": v["seconds"], "platforms": v["platforms"], "media": self._media(out, Path(v["file"]))}
                             for v in variants.versions(rec, cfg.publish.platforms)],
                "unfit": {**monetize.blocked(cfg, rec), **rec.get("unfit", {})}, "tiktok": rec.get("tiktok", {}),
                "money": rec.get("money", {}),
            })
        latest = cfg.path("reports") / "latest.md"
        searches = [{"query": settings.search_text(s.url), "section": s.section or "moments"}
                    for s in cfg.sources if s.type == "ytdlp" and settings.search_text(s.url)]
        return {
            "clips": clips,
            "run": {"running": self.running(), "exit": self.exit_code, "log": list(self.log)[-200:],
                    "minutes": round((time.time() - self.started) / 60) if self.running() else 0},
            "accounts": self.accounts(cfg),
            "posting": {**self.cached("posting", 3, lambda: self.posting(cfg)), "log": list(self.posting_log)},
            "ai": self.cached("ai", 3, lambda: self.ai(cfg)),
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
                "tiktok_direct": cfg.publish.tiktok_direct, "youtube_notify": cfg.publish.youtube_notify,
                "tiktok_privacy_level": cfg.publish.tiktok_privacy_level or "PUBLIC_TO_EVERYONE", "tiktok_comments": cfg.publish.tiktok_comments,
                "tiktok_duet": cfg.publish.tiktok_duet, "tiktok_stitch": cfg.publish.tiktok_stitch,
                "edits": {name: {"min_len": rule.get("min_len", 0), "max_len": rule.get("max_len", 0)} for name, rule in cfg.edits.items()
                          if name in publish.PLATFORMS},
                "monetize": {"enabled": cfg.monetize.enabled, "calm_open": cfg.monetize.calm_open, "mute_profanity": cfg.monetize.mute_profanity,
                             "music": cfg.monetize.music, "strict": cfg.monetize.strict, "listens": monetize.tagger_ready(cfg)},
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

    def set_ai_key(self, key: str) -> None:
        """Save the Claude API key, after checking that it works."""
        cfg = self.cfg()
        key = key.strip()
        if not key:
            raise Problem("Paste the key first.")
        if claude.unavailable(cfg).startswith("the anthropic package"):
            raise Problem("Claude is not installed yet. Run the installer again, then add the key.")
        settings.update(cfg.data_root, {"ai": {"provider": "anthropic", "enabled": True}})
        why = claude.test(self.cfg(), key)
        if why:
            raise Problem(why)
        settings.set_secret(cfg.data_root, "anthropic_api_key", key)

    def set_ai(self, changes: dict) -> str:
        """Change how Claude is reached and what it does; returns what a test of the new settings found ("" when it works)."""
        self.change_settings({"ai": {k: v for k, v in changes.items() if k != "test"}})
        cfg = self.cfg()
        if not changes.get("test") or not cfg.ai.enabled:
            return ""
        return claude.unavailable(cfg) or claude.test(cfg)

    def connect_start(self, name: str, body: dict) -> dict:
        """Save the developer app's details and return the address of the platform's sign-in page."""
        conn = connectors.get(name, self.cfg())
        if body.get("client_id") or body.get("client_secret"):
            conn.save_app(str(body.get("client_id") or conn.app()["client_id"]), str(body.get("client_secret") or ""))
        if conn.key == "meta" and "config_id" in body:
            conn.remember(config_id=str(body["config_id"]).strip())
        if body.get("token"):  # Meta: a token made in the Graph API Explorer instead of the sign-in page
            account = conn.finish("", {"token": str(body["token"])}, {})
            return {"ok": True, "connected": account.get("name", "")}
        state = secrets.token_urlsafe(24)
        redirect = conn.redirect_uri(self.port)
        url, kept = conn.auth_url(redirect, state)
        for old in [key for key, held in self.pending.items() if time.time() - held["at"] > 900]:
            del self.pending[old]
        self.pending[state] = {"name": name, "kept": kept, "redirect": redirect, "at": time.time()}
        return {"ok": True, "url": url}

    def connect_finish(self, query: dict) -> tuple[bool, str]:
        """The platform has sent the browser back: complete the sign-in. Returns (worked, what to tell the user)."""
        waiting = self.pending.pop(query.get("state", ""), None)
        if waiting is None or time.time() - waiting["at"] > 900:
            return False, "This sign-in is no longer waiting. Go back to Clipper and press Connect again."
        conn = connectors.get(waiting["name"], self.cfg())
        try:
            account = conn.finish(waiting["redirect"], query, waiting["kept"])
        except ConnectorError as exc:
            return False, str(exc)
        self._cache.clear()
        return True, f"{'Facebook and Instagram are' if conn.key == 'meta' else conn.label + ' is'} connected: {account.get('name', '')}"

    def reedit(self, clip_id: str, instruction: str) -> None:
        """Have Claude edit a finished clip again, in the background; the card shows when it is done."""
        cfg = self.cfg()
        if self.running():
            raise Problem("Wait for the current run to finish, then edit the clip.")
        if clip_id in self.jobs:
            raise Problem("Claude is already editing this clip.")
        why = review.unavailable(cfg)
        if why:
            raise Problem(f"Claude cannot be used: {why}.")
        if clip_id not in pipeline.ledger_of(cfg).clips:
            raise Problem("not found")
        self.jobs[clip_id] = "Claude is editing this clip again"
        self.job_errors.pop(clip_id, None)

        def work() -> None:
            try:
                pipeline.reedit(cfg, pipeline.ledger_of(cfg), clip_id, instruction, self.posting_log.append)
            except (publish.PublishError, KeyError, OSError) as exc:
                self.job_errors[clip_id] = str(exc)
            finally:
                self.jobs.pop(clip_id, None)

        threading.Thread(target=work, daemon=True).start()

    def tick(self, now: datetime | None = None) -> dict:
        """Send the posts that are due and, every ten minutes, read back what became of the ones that went out."""
        cfg = self.cfg()
        ledger = pipeline.ledger_of(cfg)
        if not any(rec.get("posts") for rec in ledger.clips.values()):
            return {}
        counts = outbox.deliver(cfg, ledger, now, self.posting_log.append)
        if time.time() - self._refreshed > 600 and not counts.get("busy"):
            self._refreshed = time.time()
            outbox.refresh(cfg, ledger, now, lambda message: None)
        return counts

    def reveal(self, cfg: config.Config, rec: dict, platform: str = "") -> None:
        """Show a clip's file in the file manager, selected: the version this platform gets."""
        video = variants.view(rec, platform)[1] if platform else Path(rec.get("file", ""))
        if not video.exists():
            raise Problem("The clip file is no longer on this computer.")
        if os.name == "nt":
            subprocess.Popen(["explorer", f"/select,{video}"])  # noqa: S603, S607 - opens Explorer on the file
        else:
            raise Problem(f"The file is {video}")

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
                pipeline.post_clip(cfg, ledger, body["id"], body.get("when", "now"), body.get("platforms") or None, log=self.posting_log.append)
            elif name == "post-all":
                ready = sorted((r for r in ledger.clips.values() if pipeline.clip_state(r) == "ready" and r.get("meta")),
                               key=lambda r: -r.get("score", 0))
                for rec in ready:
                    pipeline.post_clip(cfg, ledger, rec["id"], "next", body.get("platforms") or None, log=self.posting_log.append)
                return {"ok": True, "count": len(ready)}
            elif name == "settings":
                self.change_settings(body)
            elif name == "connect":
                return {"ok": True, "accounts": self.connect(body.get("api_key", ""), body.get("profile", ""))}
            elif name == "disconnect":
                self.disconnect()
            elif name in ("ai-key", "review-key"):
                self.set_ai_key(str(body.get("api_key", "")))
            elif name in ("ai-key-remove", "review-key-remove"):
                settings.set_secret(cfg.data_root, "anthropic_api_key", "")
            elif name == "ai":
                return {"ok": True, "problem": self.set_ai(body)}
            elif name == "claude-signin":
                return self.claude_signin(str(body.get("name", "")))
            elif name == "claude-signin-cancel":
                if self.login is not None:
                    self.login.cancel()
                self.login, self.login_note = None, ""
            elif name == "claude-add":
                return {"ok": True, "problem": self.claude_add(body)}
            elif name == "claude-use":
                return {"ok": True, "problem": self.claude_use(str(body.get("id", "")))}
            elif name == "claude-test":
                return {"ok": True, "problem": self.claude_problem(cfg)}
            elif name == "claude-remove":
                claude.remove_account(cfg, str(body.get("id", "")))
            elif name == "posted-by-hand":
                pipeline.posted_by_hand(cfg, ledger, body["id"], str(body.get("platform", "")), str(body.get("link", "")))
            elif name == "reveal":
                self.reveal(cfg, ledger.clips[body["id"]], str(body.get("platform", "")))
            elif name == "connect-account":
                return self.connect_start(str(body.get("platform", "")), body)
            elif name == "disconnect-account":
                connectors.get(str(body.get("platform", "")), cfg).disconnect()
            elif name == "choose-page":
                connectors.get("facebook", cfg).choose(str(body.get("page", "")))
                settings.update(cfg.data_root, {"publish": {"facebook_page_id": str(body.get("page", ""))}})
            elif name == "retry":
                pipeline.retry_clip(cfg, ledger, body["id"], log=self.posting_log.append)
            elif name == "take-back":
                pipeline.take_back(cfg, ledger, body["id"])
            elif name == "reedit":
                self.reedit(body["id"], str(body.get("instruction", "")))
            elif name == "deliver":
                return {"ok": True, **self.tick()}
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
        except (publish.PublishError, ConnectorError) as exc:
            raise Problem(str(exc)) from exc
        finally:
            self._cache.clear()
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
            query = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
            if path.startswith("/oauth/") or (path == "/" and ("state" in query or "code" in query)):
                worked, message = app.connect_finish(query)
                self._send(200 if worked else 400, RETURNED.replace("__TITLE__", "Connected" if worked else "Not connected")
                           .replace("__MESSAGE__", html.escape(message)).encode(), "text/html; charset=utf-8")
            elif path == "/":
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
    app.port = server.server_port
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"Clipper is open at {url}  (close this window to quit)", flush=True)

    def poster() -> None:  # while the app is open, it sends the posts whose time has come
        while True:
            time.sleep(60)
            try:
                app.tick()
            except Exception as exc:  # noqa: BLE001 - a fault here must not stop the next round
                app.posting_log.append(f"posting: {type(exc).__name__}: {exc}")

    threading.Thread(target=poster, daemon=True).start()
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.stop_run()
        server.server_close()
