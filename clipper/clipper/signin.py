"""Signing in to Claude from the app, with Anthropic's own sign-in tool.

`ant auth login` is the official way to sign in to the Claude API without a key:
it opens platform.claude.com in the browser, the user approves there, and a
profile is stored on this computer that the Anthropic SDK reads by name. The app
only starts that tool and waits for it; no password, code or token passes
through the app, and the profile's secret file is never opened here.

Several sign-ins can sit side by side, each under its own profile name, which
is what lets the app switch between accounts.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

ANT = "ant"
LOGIN_SECONDS = 330  # the tool itself gives up after five minutes
_URL = re.compile(r"https://[^\s\"'<>]+")
_FACT = re.compile(r"^\s*(organi[sz]ation|email|workspace|account)\b[^:]*:\s*(.+?)\s*$", re.I)


def config_dir() -> Path:
    """Where the sign-in profiles live (the same rule as the tool and the SDK)."""
    if os.environ.get("ANTHROPIC_CONFIG_DIR"):
        return Path(os.environ["ANTHROPIC_CONFIG_DIR"])
    if os.name == "nt" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "Anthropic"
    return Path.home() / ".config" / "anthropic"


def profiles() -> list[str]:
    """The names of the sign-ins on this computer."""
    folder = config_dir() / "configs"
    return sorted(p.stem for p in folder.glob("*.json")) if folder.is_dir() else []


def exists(profile: str) -> bool:
    return bool(profile) and (config_dir() / "configs" / f"{profile}.json").is_file()


def facts(profile: str) -> dict[str, str]:
    """What the profile's settings file says about it: the organisation and workspace it is bound to. Not the secret file."""
    try:
        data = json.loads((config_dir() / "configs" / f"{profile}.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {key: str(data[key]) for key in ("organization_id", "workspace_id") if isinstance(data, dict) and data.get(key)}


def tool() -> str:
    """The path of Anthropic's `ant`, or "" when it is not installed. Apache's build tool of the same name does not count."""
    found = shutil.which(ANT)
    if not found:
        return ""
    try:
        said = subprocess.run([found, "--version"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return ""
    return "" if "apache" in (said.stdout + said.stderr).lower() else found


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Login:
    """One sign-in under way: the tool is running and waiting for the approval in the browser."""

    def __init__(self, profile: str):
        self.profile = profile
        self.url = ""  # the page to approve on, once the tool has printed it
        self.lines: list[str] = []
        self.details: dict[str, str] = {}  # organisation, email and workspace, as the tool reports them
        self.done = False
        self.ok = False
        self.error = ""
        self.proc: subprocess.Popen | None = None

    def start(self) -> "Login":
        ant = tool()
        if not ant:
            raise RuntimeError("Anthropic's sign-in tool (ant) is not installed on this computer. Run the installer again, or use an API key.")
        env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}  # a key would shadow the sign-in
        self.proc = subprocess.Popen(
            [ant, "auth", "login", "--profile", self.profile, "--no-browser", "--callback-port", str(free_port())],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", env=env)
        threading.Thread(target=self._follow, daemon=True).start()
        return self

    def _follow(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        started = time.time()
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            self.lines.append(line)
            found = _URL.search(line)
            if found and not self.url:
                self.url = found.group(0)
            fact = _FACT.match(line)
            if fact:
                self.details[fact.group(1).lower().replace("organisation", "organization")] = fact.group(2)
        try:
            code = self.proc.wait(timeout=max(1.0, LOGIN_SECONDS - (time.time() - started)))
        except subprocess.TimeoutExpired:
            self.proc.kill()
            code = -1
        self.ok = code == 0 and exists(self.profile)
        if not self.ok:
            said = " ".join(self.lines[-3:]).lower()
            if "timed out" in said or code == -1:
                self.error = "Nobody approved the sign-in in time. Press Sign in again and approve in the browser tab."
            else:
                self.error = "The sign-in did not complete" + (f": {self.lines[-1][:200]}" if self.lines else ".")
        self.done = True

    def cancel(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()


def logout(profile: str) -> None:
    """Remove a sign-in from this computer."""
    ant = tool()
    if ant and exists(profile):
        subprocess.run([ant, "--profile", profile, "auth", "logout"], capture_output=True, text=True, timeout=60)
