"""Get a machine ready to run the clipper: python -m clipper setup [--install]

Uses only the standard library, so it runs before anything else is installed,
and on Windows, macOS and Linux alike. Finished steps are skipped.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tarfile
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models"
ASR = "sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8"
MODULES = {  # import name -> what to install
    "numpy": "numpy", "soundfile": "soundfile", "sherpa_onnx": "sherpa-onnx", "upload_post": "upload-post",
    "requests": "requests", "yt_dlp": "yt-dlp", "yt_dlp_ejs": "yt-dlp-ejs", "cv2": "opencv-python-headless",
}


def js_runtime() -> tuple[str, str]:
    """The JavaScript runtime yt-dlp can use for YouTube: ("deno" | "node" | "", its version)."""
    for name, flag in (("deno", "--version"), ("node", "--version")):
        exe = shutil.which(name)
        if not exe:
            continue
        out = subprocess.run([exe, flag], capture_output=True, text=True).stdout.strip().splitlines()
        version = (out[0] if out else "").replace("deno", "").replace("v", "").split()[0] if out else ""
        parts = [int(p) for p in version.split(".")[:2] if p.isdigit()]
        if name == "deno" and parts >= [2, 3]:
            return name, version
        if name == "node" and parts >= [22]:
            return name, version
    return "", ""


def models_dir() -> Path:
    config = ROOT / "config.toml"
    if config.exists():
        with config.open("rb") as fh:
            configured = tomllib.load(fh).get("paths", {}).get("models", "")
        if configured:
            return Path(configured).expanduser()
    return Path(os.environ.get("CLIPPER_MODELS", "~/.cache/clipper/models")).expanduser()


def missing_modules() -> list[str]:
    wanted = dict(MODULES)
    if os.name == "nt":
        wanted["tzdata"] = "tzdata"  # Windows has no time-zone database of its own
    return [package for name, package in wanted.items() if importlib.util.find_spec(name) is None]


def fetch(url: str, dest: Path, tries: int = 8) -> None:
    """Download with resume: a dropped connection continues where it stopped."""
    part = dest.with_name(dest.name + ".part")
    for attempt in range(1, tries + 1):
        have = part.stat().st_size if part.exists() else 0
        request = urllib.request.Request(url, headers={"Range": f"bytes={have}-"} if have else {})
        try:
            with urllib.request.urlopen(request, timeout=60) as resp:
                if have and resp.status != 206:  # the server sent the whole file again
                    have = 0
                with part.open("ab" if have else "wb") as out:
                    shutil.copyfileobj(resp, out, 1 << 20)
            part.replace(dest)
            return
        except urllib.error.HTTPError as exc:
            if exc.code == 416 and have:  # nothing left to fetch: the part file is complete
                part.replace(dest)
                return
            problem = exc
        except (urllib.error.URLError, OSError) as exc:
            problem = exc
        print(f"  download interrupted (attempt {attempt}): {problem}; resuming")
        time.sleep(attempt * 2)
    raise SystemExit(f"could not download {url}")


def ensure_models(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    if not (folder / ASR / "encoder.int8.onnx").exists():
        print("downloading the speech model (about 480 MB)")
        archive = folder / "asr.tar.bz2"
        fetch(f"{BASE}/{ASR}.tar.bz2", archive)
        with tarfile.open(archive, "r:bz2") as tar:
            tar.extractall(folder, filter="data")
        archive.unlink()
    if not (folder / "silero_vad.onnx").exists():
        fetch(f"{BASE}/silero_vad.onnx", folder / "silero_vad.onnx")


def main(argv: list[str]) -> int:
    ok = True
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            print(f"FAIL {tool} is not installed (macOS: brew install ffmpeg; Windows: winget install ffmpeg; Linux: apt-get install -y ffmpeg)")
            ok = False
    runtime, version = js_runtime()
    if runtime:
        print(f"ok   JavaScript runtime for YouTube downloads: {runtime} {version}")
    else:
        print("FAIL no JavaScript runtime: YouTube downloads need Deno 2.3+ or Node 22+ "
              "(macOS: brew install deno; Windows: winget install DenoLand.Deno; Linux: curl -fsSL https://deno.land/install.sh | sh)")
        ok = False
    missing = missing_modules()
    if missing and "--install" in argv:
        print(f"installing {', '.join(missing)}")
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", str(ROOT / "requirements.txt")], check=False)
        importlib.invalidate_caches()
        missing = missing_modules()
    if missing:
        print(f"FAIL missing Python packages: {', '.join(missing)}")
        print(f"     install them with: {sys.executable} -m pip install -r {ROOT / 'requirements.txt'}")
        print("     (if pip refuses to install system-wide, create a virtual environment first: python -m venv .venv)")
        ok = False
    ensure_models(models_dir())
    print("setup complete" if ok else "setup incomplete: fix the FAIL lines above and run it again")
    return 0 if ok else 1
