"""The ledger: which footage has been processed and what happened to every clip.

One JSON file, committed to the repository, so a fresh machine picks up where
the last run stopped and nothing is clipped or posted twice.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Ledger:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, Any] = {"version": 1, "sources": {}, "clips": {}, "runs": []}
        if path.exists():
            self.data.update(json.loads(path.read_text()))

    @property
    def sources(self) -> dict[str, dict]:
        return self.data["sources"]

    @property
    def clips(self) -> dict[str, dict]:
        return self.data["clips"]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
        os.replace(tmp, self.path)

    def taken_slots(self) -> set[str]:
        return {c["slot"] for c in self.clips.values() if c.get("slot") and c.get("status") in ("planned", "scheduled", "posted", "held")}

    def clips_of(self, source_id: str) -> list[dict]:
        return [c for c in self.clips.values() if c.get("source_id") == source_id]

    def log_run(self, summary: dict) -> None:
        self.data["runs"] = (self.data.get("runs", []) + [{"at": now_iso(), **summary}])[-90:]
