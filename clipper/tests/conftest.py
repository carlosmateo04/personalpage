import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"

from clipper import config  # noqa: E402
from clipper.state import Ledger  # noqa: E402
from clipper.transcribe import Word  # noqa: E402


@pytest.fixture
def cfg(tmp_path):
    c = config.Config()
    c.root = tmp_path
    c.sources = [config.Source(type="folder", path="inbox")]
    (tmp_path / "inbox").mkdir()
    return c


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / "state" / "ledger.json")


def speak(text: str, start: float = 0.0, pace: float = 0.3) -> list[Word]:
    """Evenly timed words for a sentence."""
    out, t = [], start
    for w in text.split():
        out.append(Word(w, round(t, 3), round(t + pace * 0.8, 3)))
        t += pace
    return out
