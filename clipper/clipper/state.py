"""The ledger: which footage has been processed and what happened to every clip.

One JSON file, so a fresh start picks up where the last run stopped and nothing
is clipped or posted twice.

More than one process uses it at a time: a run that is making clips, the app,
and the poster that sends what is due. Saving therefore merges, record by
record: what this process changed is written; everything it left alone is taken
from the file as it is now, so another process's changes to other clips survive.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

KINDS = ("sources", "clips")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class Lock:
    """A lock file that keeps two processes from doing the same thing at once.

        with Lock(path, wait=0) as held:
            if held: ...

    `wait` is how long to keep trying, in seconds. A lock whose owner is no longer
    running, or that is older than `stale` seconds, is taken over.
    """

    def __init__(self, path: Path, wait: float = 10.0, stale: float = 60.0):
        self.path, self.wait, self.stale = Path(path), wait, stale
        self.held = False

    def _take(self) -> bool:
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w") as fh:
            fh.write(f"{os.getpid()} {time.time():.0f}")
        return True

    def _abandoned(self) -> bool:
        try:
            pid, _, since = self.path.read_text().partition(" ")
            return not _alive(int(pid)) or time.time() - float(since) > self.stale
        except (OSError, ValueError):
            # Unreadable: being written right now, or damaged. Old enough means damaged.
            try:
                return time.time() - self.path.stat().st_mtime > min(self.stale, 5.0)
            except OSError:
                return False

    def __enter__(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.time() + self.wait
        while True:
            if self._take():
                self.held = True
                return True
            if self._abandoned():
                try:
                    self.path.unlink()
                except OSError:
                    pass
                continue
            if time.time() >= deadline:
                return False
            time.sleep(0.05)

    def __exit__(self, *exc) -> None:
        if self.held:
            try:
                self.path.unlink()
            except OSError:
                pass
            self.held = False


class Ledger:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, Any] = {"version": 1, "sources": {}, "clips": {}, "runs": []}
        self.data.update(self._read())
        self._remember()

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _remember(self) -> None:
        """What each record looked like when it was last read or written, to tell later what was changed here."""
        self._base = {kind: {key: json.dumps(value, sort_keys=True) for key, value in self.data[kind].items()} for kind in KINDS}
        self._runs = len(self.data.get("runs", []))

    @property
    def sources(self) -> dict[str, dict]:
        return self.data["sources"]

    @property
    def clips(self) -> dict[str, dict]:
        return self.data["clips"]

    def save(self) -> None:
        """Write the ledger, keeping what other processes changed in the meantime.

        Records are updated in place, so a record a caller is still holding stays the
        one in the ledger.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with Lock(self.path.with_suffix(".lock"), wait=15.0, stale=30.0):
            disk = self._read()
            for kind in KINDS:
                mine, theirs, base = self.data[kind], disk.get(kind, {}), self._base[kind]
                for key in set(mine) | set(theirs) | set(base):
                    here = json.dumps(mine[key], sort_keys=True) if key in mine else None
                    if here != base.get(key):
                        continue  # changed, added or deleted here: this process's version stands
                    if key in theirs:
                        if key in mine:
                            if json.dumps(theirs[key], sort_keys=True) != here:
                                mine[key].clear()
                                mine[key].update(theirs[key])  # changed elsewhere
                        else:
                            mine[key] = theirs[key]  # added elsewhere
                    elif key in mine:
                        del mine[key]  # deleted elsewhere
            # Runs logged elsewhere since the last read, then the ones logged here.
            elsewhere = [run for run in disk.get("runs", []) if run not in self.data.get("runs", [])]
            self.data["runs"] = (self.data.get("runs", [])[:self._runs] + elsewhere + self.data.get("runs", [])[self._runs:])[-90:]
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
            os.replace(tmp, self.path)
            self._remember()

    def taken_slots(self) -> set[str]:
        return {c["slot"] for c in self.clips.values() if c.get("slot") and c.get("status") in ("planned", "scheduled", "posted", "held")}

    def clips_of(self, source_id: str) -> list[dict]:
        return [c for c in self.clips.values() if c.get("source_id") == source_id]

    def log_run(self, summary: dict) -> None:
        self.data["runs"] = (self.data.get("runs", []) + [{"at": now_iso(), **summary}])[-90:]
