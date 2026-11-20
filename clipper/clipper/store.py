"""Small JSON files in the state folder, read and written safely by more than one process."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .config import Config
from .state import Lock, _retry


def path(cfg: Config, name: str) -> Path:
    return cfg.path("state") / f"{name}.json"


def read(cfg: Config, name: str, empty: Any = None) -> Any:
    file = path(cfg, name)
    try:
        return json.loads(_retry(lambda: file.read_text(encoding="utf-8"))) if file.exists() else (empty if empty is not None else {})
    except (json.JSONDecodeError, OSError):
        return empty if empty is not None else {}


def write(cfg: Config, name: str, data: Any) -> None:
    file = path(cfg, name)
    file.parent.mkdir(parents=True, exist_ok=True)
    with Lock(file.with_suffix(".lock"), wait=10.0):
        tmp = file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        _retry(lambda: os.replace(tmp, file))
