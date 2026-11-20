"""System check: try each part of the clipper on this computer and say, part by part, whether it works.

    python -m clipper check          (the "Check everything" button in the app does the same)

Every part is tried for real, on this computer and this internet connection:

  tools    ffmpeg, the JavaScript runtime, the caption font and the speech model are installed
  lookup   the first online source is asked what it lists, and the title, views and length rules are applied
  fetch    the first 15 seconds of one listed video are downloaded
  clip     a short test video is transcribed, captioned and rendered as a vertical clip
  review   one real request goes to Claude and must come back in the expected form (needs a key or an AWS profile; costs a cent or two)
  posting  every connected account is signed in to and read, directly or through the posting service (nothing is posted)

Nothing is added to the ledger and nothing is posted.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from . import captions, claude, connectors, intake, media, outbox, publish, render, review, settings, transcribe
from .bootstrap import js_runtime
from .config import Config

PARTS = [
    ("tools", "Tools on this computer"),
    ("lookup", "Finding videos"),
    ("fetch", "Downloading a video"),
    ("clip", "Cutting, captions and rendering"),
    ("review", "Claude, built in"),
    ("posting", "Posting connection"),
]
OK, PROBLEM, SKIPPED = "ok", "problem", "skipped"
Outcome = tuple[str, str]  # status, what was found


class _Bench:
    """What the parts hand on to each other: a scratch folder, the listed video, the test clip."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.folder = cfg.path("work") / "check"
        shutil.rmtree(self.folder, ignore_errors=True)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.candidate: dict | None = None
        self.sample: Path | None = None
        self.words: list = []


def _tools(b: _Bench) -> Outcome:
    missing = []
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        missing.append("ffmpeg")
    runtime, version = js_runtime()
    if not runtime:
        missing.append("a JavaScript runtime for YouTube downloads (Deno 2.3+ or Node 22+)")
    if not any(b.cfg.fonts_dir.glob("*.ttf")):
        missing.append("the caption font")
    if not transcribe.models_present(b.cfg):
        missing.append("the speech model")
    if missing:
        return PROBLEM, f"missing: {', '.join(missing)}. Run the installer again."
    return OK, f"ffmpeg, {runtime} {version}, the caption font and the speech model are installed"


def _site(url: str) -> str:
    host = urlparse(url).netloc.removeprefix("www.")
    return {"youtube.com": "YouTube", "youtu.be": "YouTube", "twitch.tv": "Twitch"}.get(host, host or "the source")


def _lookup(b: _Bench) -> Outcome:
    src = next((s for s in b.cfg.sources if s.type == "ytdlp"), None)
    if src is None:
        return SKIPPED, "no online source is set up; only the inbox folder is used"
    site, query = _site(src.url), settings.search_text(src.url)
    what = f'for "{query}"' if query else f"at {src.url[:80]}"
    entries, err = intake.listing(src)
    if not entries:
        if intake._refused(err):
            return PROBLEM, f"{site} is refusing automated requests from this computer (its bot check). Try again later or from another connection."
        return PROBLEM, f"{site} listed nothing {what}. {err[-200:]}".strip()
    good = [e for e in entries if intake.wanted(src, e)]
    b.candidate = {**(good[0] if good else entries[0]), "_site": site}
    if not good:
        return OK, f"{site} listed {len(entries)} videos {what}; none pass the title, views and length rules right now"
    return OK, f'{site} listed {len(entries)} videos {what}; {len(good)} pass the title, views and length rules, e.g. "{str(good[0].get("title"))[:60]}"'


def _fetch(b: _Bench) -> Outcome:
    if b.candidate is None:
        return SKIPPED, "there is no listed video to try"
    site = b.candidate["_site"]
    url = b.candidate.get("webpage_url") or b.candidate.get("url")
    title = str(b.candidate.get("title") or url)[:60]
    meta, err = intake.details(url)
    if not meta:
        if intake._refused(err):
            return PROBLEM, f"{site} asked to confirm this computer is not a bot. Downloads will not work from this connection for now."
        return PROBLEM, f'{site} would not give the details of "{title}". {err[-200:]}'.strip()
    args = ["--no-progress", "--no-playlist", "-f", "b[height<=480]/bv*[height<=480]+ba/b", "--download-sections", "*0-15",
            "--merge-output-format", "mp4", "-o", str(b.folder / "fetched.%(ext)s"), "--print", "after_move:filepath", url]
    got = intake._yt(args, timeout=300)
    path = next((Path(p.strip()) for p in reversed(got.stdout.splitlines()) if p.strip() and Path(p.strip()).exists()), None)
    if path is None:
        if intake._refused(got.stderr):
            return PROBLEM, f"{site} asked to confirm this computer is not a bot. Downloads will not work from this connection for now."
        return PROBLEM, f'the first seconds of "{title}" could not be downloaded. {got.stderr.strip()[-200:]}'.strip()
    try:
        seconds = media.probe(path).duration
    except media.MediaError as exc:
        return PROBLEM, f"the downloaded piece does not play: {str(exc)[:160]}"
    return OK, f'downloaded the first {seconds:.0f} seconds of "{title}" by {intake._channel(meta) or "an unnamed channel"} ({path.stat().st_size / 1e6:.1f} MB)'


def _clip(b: _Bench) -> Outcome:
    cfg, began = b.cfg, time.time()
    speech = cfg.models_dir / transcribe.ASR_DIR / "test_wavs" / "0.wav"
    sound = ["-i", str(speech)] if speech.exists() else ["-f", "lavfi", "-i", "sine=frequency=440:duration=8"]
    source = b.folder / "pattern.mp4"
    try:
        made = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=20", *sound,
             "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source)],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        if made.returncode != 0:
            return PROBLEM, f"ffmpeg could not make the test video: {made.stderr.strip()[-200:]}"
        length = media.probe(source).duration
        b.words = transcribe.transcribe(cfg, media.extract_audio(source, b.folder / "pattern.wav"))
        ass = b.folder / "pattern.ass"
        ass.write_text(captions.build(cfg, b.words, length, hook="System check", badge="TEST", badge_color="#FFE600",
                                      credit="Credit: system check"), encoding="utf-8")
        out = render.render(cfg, source, 0.0, length, ass, b.folder / "check.mp4")
        got = media.probe(out)
    except (media.MediaError, transcribe.TranscribeError, OSError) as exc:
        return PROBLEM, str(exc)[:300]
    b.sample = source
    if (got.width, got.height) != (cfg.render.width, cfg.render.height) or not got.has_audio:
        return PROBLEM, f"the test clip came out wrong: {got.width}x{got.height}, sound: {got.has_audio}"
    heard = f"{len(b.words)} words heard and captioned" if speech.exists() else "no speech sample to caption"
    return OK, f"made a {got.duration:.0f} second vertical test clip ({got.width}x{got.height}), {heard}, in {time.time() - began:.0f} seconds"


def _review(b: _Bench) -> Outcome:
    why = review.unavailable(b.cfg)
    if why:
        return SKIPPED, f"{why}. Titles are written by rule until then"
    clip = {"id": "check", "source_name": "Clipper system check: a colour test pattern, not gameplay", "creator": "", "start": 0.0, "end": 20.0,
            "text": " ".join(w.w for w in b.words), "sheet": ""}
    if b.sample is not None:
        try:
            clip["end"] = media.probe(b.sample).duration
            clip["sheet"] = str(media.contact_sheet(b.sample, 0.0, clip["end"], b.folder / "sheet.jpg", 4))
        except media.MediaError:
            pass
    try:
        verdict = review.ask(b.cfg, clip)
    except review.ReviewError as exc:
        return PROBLEM, f"the request to Claude failed: {exc}"
    said = "would keep" if verdict["keep"] else "rightly dropped"
    return OK, f"{claude.model_id(b.cfg)} answered in the expected form (it {said} the test pattern: {str(verdict.get('reason', ''))[:110]})"


def _posting(b: _Bench) -> Outcome:
    """Every way a clip would go out is tried as far as it can be without posting: the sign-in is used and the account read."""
    cfg = b.cfg
    how = outbox.routes(cfg)
    if not any(how.values()):
        return SKIPPED, "no account is connected (connect them in the app under Accounts). Clips are made but not posted until then"
    works: list[str] = []
    wrong: list[str] = []
    through_service = [name for name, way in how.items() if way == "service"]
    if through_service:
        found = publish.accounts(cfg)
        profile = next((p for p in found["profiles"] if p["name"] == cfg.publish.profile), None)
        if found["error"]:
            wrong.append(f"the posting service did not accept the key: {found['error']}")
        elif profile is None:
            wrong.append(f"the posting service has no profile called \"{cfg.publish.profile}\"")
        else:
            missing = [name for name in through_service if name not in profile["platforms"]]
            if missing:
                wrong.append(f"these accounts are not connected to the posting service's profile \"{profile['name']}\": {', '.join(missing)}")
            else:
                works.append(f"the posting service (profile \"{profile['name']}\": {', '.join(through_service)})")
    asked: set[str] = set()
    for name, way in how.items():
        if way != "direct":
            continue
        conn = connectors.get(name, cfg)
        try:
            if conn.key not in asked:  # Facebook and Instagram share one sign-in
                asked.add(conn.key)
                if name == "youtube":
                    conn.channel()
                elif name == "tiktok":
                    conn.access(fresh=True)
                else:
                    conn.tokens()
                    conn.pages()
        except connectors.ConnectorError as exc:
            wrong.append(f"{conn.label}: {exc}")
        else:
            works.append(f"{conn.label} ({conn.account().get('name', '')})")
    if wrong:
        return PROBLEM, "; ".join(wrong) + (f". Working: {', '.join(works)}" if works else "")
    unconnected = [name for name, way in how.items() if not way]
    return OK, f"signed in and reachable: {', '.join(works)}" + (f". Not connected: {', '.join(unconnected)}" if unconnected else "") + ". Nothing was posted"


_RUN = {"tools": _tools, "lookup": _lookup, "fetch": _fetch, "clip": _clip, "review": _review, "posting": _posting}


def run(cfg: Config, report: Callable[[dict], None] | None = None, only: list[str] | None = None) -> list[dict]:
    """Try each part in turn. `report` is called as each part starts ("running") and finishes."""
    bench = _Bench(cfg)
    results: list[dict] = []
    try:
        for key, name in PARTS:
            if only and key not in only:
                continue
            if report:
                report({"key": key, "name": name, "status": "running", "detail": ""})
            began = time.time()
            try:
                status, detail = _RUN[key](bench)
            except Exception as exc:  # noqa: BLE001 - a part that breaks is a finding, not a crash
                status, detail = PROBLEM, f"{type(exc).__name__}: {str(exc)[:240]}"
            result = {"key": key, "name": name, "status": status, "detail": detail, "seconds": round(time.time() - began, 1)}
            results.append(result)
            if report:
                report(result)
    finally:
        shutil.rmtree(bench.folder, ignore_errors=True)
    return results


def summary(results: list[dict]) -> str:
    counts = {s: sum(1 for r in results if r["status"] == s) for s in (OK, PROBLEM, SKIPPED)}
    if counts[PROBLEM]:
        return f"{counts[PROBLEM]} part(s) need attention, {counts[OK]} work, {counts[SKIPPED]} not set up"
    if counts[SKIPPED]:
        return f"{counts[OK]} part(s) work; {counts[SKIPPED]} not set up yet"
    return f"all {counts[OK]} parts work"
