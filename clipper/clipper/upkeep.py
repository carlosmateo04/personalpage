"""Safety nets: backups of the records, a check for a newer Clipper, and keeping the downloader current.

  backup    settings.json and everything in the state folder (the ledger, the money and rights records,
            the counts) go into one zip under backups/, once a day and before anything is restored. The
            newest 14 are kept. Keys and sign-ins are not in it: they live in the computer's credential
            store and are not copied anywhere.
  updates   the installer notes which version of the code it installed; this asks GitHub (the public
            page of the code, nothing about this computer is sent) whether there is a newer one.
  downloader  video sites change, and the downloader (yt-dlp) is updated every few days to keep up.
            It is brought up to date once a week, and at once when a run could fetch nothing.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import store
from .config import ROOT, Config

KEEP = 14
VERSION = ROOT / "VERSION"  # written by the installer: the commit, the repository and the branch, one per line
PACKAGES = ["yt-dlp", "yt-dlp-ejs"]


# --- backups ---------------------------------------------------------------------------

def folder(cfg: Config) -> Path:
    return cfg.data_root / "backups"


def _files(cfg: Config) -> list[tuple[Path, str]]:
    found = []
    if (cfg.data_root / "settings.json").exists():
        found.append((cfg.data_root / "settings.json", "settings.json"))
    state = cfg.path("state")
    if state.is_dir():
        found += [(p, f"state/{p.name}") for p in sorted(state.glob("*.json"))]
    return found


def backup(cfg: Config, label: str = "", now: datetime | None = None) -> Path | None:
    """Zip the records. Returns the file, or None when there is nothing to back up yet."""
    files = _files(cfg)
    if not files:
        return None
    now = now or datetime.now(timezone.utc)
    folder(cfg).mkdir(parents=True, exist_ok=True)
    path = folder(cfg) / f"clipper-{now.strftime('%Y%m%d-%H%M%S')}{'-' + label if label else ''}.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for file, name in files:
            z.write(file, name)
    for old in sorted(folder(cfg).glob("clipper-*.zip"), reverse=True)[KEEP:]:
        old.unlink(missing_ok=True)
    return path


def backups(cfg: Config) -> list[dict[str, Any]]:
    out = []
    for path in sorted(folder(cfg).glob("clipper-*.zip"), reverse=True) if folder(cfg).is_dir() else []:
        stamp = path.stem.split("-")[1:3]
        try:
            when = datetime.strptime("-".join(stamp), "%Y%m%d-%H%M%S").strftime("%d %b %Y, %H:%M")
        except ValueError:
            when = path.stem
        out.append({"name": path.name, "when": when, "kb": max(1, path.stat().st_size // 1024)})
    return out


def daily(cfg: Config, now: datetime | None = None) -> Path | None:
    """One backup a day: made when none of today's exists yet."""
    now = now or datetime.now(timezone.utc)
    today = now.strftime("%Y%m%d")
    if folder(cfg).is_dir() and any(p.name.startswith(f"clipper-{today}") for p in folder(cfg).glob("clipper-*.zip")):
        return None
    return backup(cfg, now=now)


def restore(cfg: Config, name: str) -> list[str]:
    """Put the records back as they were in a backup. What is there now is backed up first. Returns the files restored."""
    path = folder(cfg) / Path(name).name
    if not path.is_file() or not path.name.startswith("clipper-"):
        raise FileNotFoundError(name)
    backup(cfg, "before-restore")
    done = []
    with zipfile.ZipFile(path) as z:
        for member in z.namelist():
            if member != "settings.json" and not (member.startswith("state/") and member.endswith(".json") and member.count("/") == 1):
                continue  # only the records, and only where they belong
            target = cfg.data_root / member if member == "settings.json" else cfg.path("state") / Path(member).name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(member))
            done.append(member)
    return done


# --- updates ---------------------------------------------------------------------------

def installed() -> dict[str, str]:
    """The version the installer noted, or empty when the code was not installed by it."""
    try:
        commit, repo, branch = (VERSION.read_text(encoding="utf-8").split("\n") + ["", "", ""])[:3]
    except OSError:
        return {}
    return {"commit": commit.strip(), "repo": repo.strip(), "branch": branch.strip()} if commit.strip() and repo.strip() else {}


def newest(cfg: Config, fresh: bool = False, fetch=None, ask: bool = True) -> dict[str, Any]:
    """Whether a newer Clipper is out: {"available", "installed", "latest", "checked"}. Asked at most every six hours."""
    here = installed()
    if not here:
        return {"available": False, "installed": "", "latest": "", "note": "not installed by the installer"}
    seen = store.read(cfg, "update")
    if ask and (fresh or time.time() - float(seen.get("at", 0)) > 6 * 3600):
        try:
            if fetch is None:
                import requests

                def fetch(url):
                    return requests.get(url, headers={"User-Agent": "clipper-update-check", "Accept": "application/vnd.github+json"}, timeout=15).json()
            latest = str(fetch(f"https://api.github.com/repos/{here['repo']}/commits/{here['branch']}").get("sha", ""))
            if latest:
                seen = {"at": time.time(), "latest": latest}
                store.write(cfg, "update", seen)
        except Exception:  # noqa: BLE001 - no connection is not a fault of the app
            pass
    latest = str(seen.get("latest", ""))
    return {"available": bool(latest) and latest != here["commit"], "installed": here["commit"][:7], "latest": latest[:7]}


def update_command() -> list[str] | None:
    """What brings Clipper up to date on this computer: the installer run again. None where there is no such script."""
    script = ROOT.parent / "update.ps1"
    return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)] if script.exists() else None


# --- the downloader ----------------------------------------------------------------------

def refresh_downloader(run=subprocess.run) -> str:
    """Bring the downloader up to date. Returns what happened, in a few words."""
    uv = shutil.which("uv")
    cmd = [uv, "pip", "install", "--python", sys.executable, "--upgrade", *PACKAGES] if uv else [sys.executable, "-m", "pip", "install", "--upgrade", *PACKAGES]
    try:
        done = run(cmd, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"the downloader could not be updated ({type(exc).__name__})"
    if done.returncode != 0:
        return "the downloader could not be updated: " + (done.stderr or done.stdout or "").strip()[-160:]
    return "the downloader is up to date"


def weekly(cfg: Config, log=print, now: float | None = None, run=subprocess.run, force: bool = False) -> bool:
    """Update the downloader when a week has passed since the last time, or now when `force`. Returns whether it was tried."""
    now = now or time.time()
    seen = store.read(cfg, "upkeep")
    if not force and now - float(seen.get("downloader", 0)) < 7 * 86400:
        return False
    log("  " + refresh_downloader(run))
    store.write(cfg, "upkeep", {**seen, "downloader": now})
    return True
