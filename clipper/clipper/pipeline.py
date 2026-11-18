"""The daily run: footage in, scheduled posts out.

plan     collect footage, transcribe, find moments, apply the quality gates,
         write titles, assign posting slots  ->  out/<run>/plan.json
execute  render every planned clip and hand it to the publisher

plan.json is the review surface: before `execute`, a title or hook can be
edited, a clip dropped ("drop": true) or a voiceover attached ("voiceover": path).
"""

from __future__ import annotations

import json
from dataclasses import asdict
from difflib import SequenceMatcher
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from . import captions, gates, intake, media, metadata, moments, publish, render, schedule, transcribe
from .config import Config
from .state import Ledger, now_iso
from .transcribe import Word

Log = Callable[[str], None]


def _log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def ledger_of(cfg: Config) -> Ledger:
    return Ledger(cfg.path("state") / "ledger.json")


def analyse(cfg: Config, path: Path, source_id: str, chat: list[float] | None = None) -> tuple[list[moments.Candidate], float]:
    """Transcribe one video and return its clip candidates and duration."""
    info = media.probe(path)
    if not info.has_audio:
        raise media.MediaError(f"{path.name}: no audio track")
    work = cfg.path("work")
    wav = media.extract_audio(path, work / "audio" / f"{source_id}.wav")
    words = transcribe.transcribe(cfg, wav, work / "transcripts" / f"{source_id}.json")
    return moments.find(cfg.moments, wav, words, info.duration, chat), info.duration


def plan(cfg: Config, ledger: Ledger, now: datetime | None = None, log: Log = _log) -> dict:
    now = now or datetime.now(timezone.utc)
    run_id = now.strftime("%Y%m%d-%H%M%S")
    run_dir = cfg.path("out") / run_id
    pool: list[tuple[moments.Candidate, intake.Item]] = []
    rejected: list[dict] = []
    problems: list[str] = []
    seen: list[tuple[str, intake.Item]] = []
    # Clips planned by a run that never finished are forgotten; their footage is analysed again.
    for stale in [k for k, c in ledger.clips.items() if c.get("status") == "planned"]:
        del ledger.clips[stale]

    for item in intake.collect(cfg, ledger, log):
        log(f"analysing {item.name}")
        try:
            cands, duration = analyse(cfg, item.path, item.source_id, intake.load_chat(item.chat_file))
        except (media.MediaError, transcribe.TranscribeError) as exc:
            problems.append(f"{item.name}: {exc}")
            ledger.sources[item.source_id] = {"name": item.name, "origin": item.origin, "status": "failed", "error": str(exc)[:300], "processed": now_iso()}
            continue
        kept = 0
        for cand in cands:
            reasons = gates.check(cfg, cand, item.source_id, ledger)
            if not reasons and kept >= cfg.moments.max_per_source:
                reasons = ["over the per-video limit"]
            text = gates.normalise(cand.text)
            if not reasons and any(i.source_id != item.source_id and SequenceMatcher(None, text, t).ratio() > cfg.gates.max_text_similarity for t, i in seen):
                reasons = ["same moment as another clip in this run"]
            if reasons:
                rejected.append({"source": item.name, "start": cand.start, "end": cand.end, "score": cand.score, "reasons": reasons})
                continue
            kept += 1
            seen.append((text, item))
            pool.append((cand, item))
        ledger.sources[item.source_id] = {
            "name": item.name, "origin": item.origin, "creator": item.creator, "permission": item.permission,
            "duration": round(duration, 1), "candidates": len(cands), "kept": kept, "status": "planned", "processed": now_iso(),
        }
        log(f"  {len(cands)} moment(s) found, {kept} passed the checks")

    pool.sort(key=lambda p: -p[0].score)
    slots = schedule.next_slots(cfg.schedule, now, ledger.taken_slots(), len(pool))
    clips: list[dict] = []
    for (cand, item), slot in zip(pool, slots):
        clip_id = "c" + media.text_id(f"{item.source_id}:{cand.start:.1f}")[:10]
        clip = {
            "id": clip_id, "source_id": item.source_id, "source_name": item.name, "source_path": str(item.path),
            "creator": item.creator, "start": cand.start, "end": cand.end, "peak": cand.peak, "score": cand.score,
            "text": cand.text, "words": [asdict(w) for w in cand.words],
            "meta": metadata.build(cfg, cand, clip_id, item.creator),
            "slot": slot.isoformat(), "voiceover": "", "drop": False,
        }
        clips.append(clip)
        ledger.clips[clip_id] = {
            "id": clip_id, "source_id": item.source_id, "source_name": item.name, "creator": item.creator,
            "start": cand.start, "end": cand.end, "score": cand.score, "text": gates.normalise(cand.text),
            "title": clip["meta"]["title"], "slot": clip["slot"], "status": "planned", "run": run_id, "created": now_iso(),
        }
    for cand, item in pool[len(slots):]:
        rejected.append({"source": item.name, "start": cand.start, "end": cand.end, "score": cand.score, "reasons": ["no free posting slot in the scheduling window"]})

    result = {"run": run_id, "created": now_iso(), "clips": clips, "rejected": rejected, "problems": problems}
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "plan.json").write_text(json.dumps(result, indent=1, ensure_ascii=False))
    ledger.save()
    log(f"plan {run_id}: {len(clips)} clip(s) planned, {len(rejected)} rejected, {len(problems)} problem(s)")
    return result


def latest_plan(cfg: Config) -> Path | None:
    plans = sorted(cfg.path("out").glob("*/plan.json"))
    return plans[-1] if plans else None


def render_clip(cfg: Config, clip: dict, run_dir: Path) -> Path:
    start, end = clip["start"], clip["end"]
    rel = [Word(w["w"], round(w["s"] - start, 3), round(w["e"] - start, 3)) for w in clip["words"]]
    ass = cfg.path("work") / "captions" / f"{clip['id']}.ass"
    ass.parent.mkdir(parents=True, exist_ok=True)
    ass.write_text(captions.build(cfg, rel, end - start, hook=clip["meta"].get("hook", ""), label=clip["meta"].get("label", "")))
    voice = Path(clip["voiceover"]) if clip.get("voiceover") else None
    if voice and not voice.exists():
        raise media.MediaError(f"voiceover {voice} not found")
    out = render.render(cfg, Path(clip["source_path"]), start, end, ass, run_dir / f"{clip['id']}.mp4", voice)
    got = media.probe(out)
    if abs(got.duration - (end - start)) > 1.0 or not got.has_audio:
        raise media.MediaError(f"render check failed: {got.duration:.1f}s, audio={got.has_audio}")
    return out


def _send(cfg: Config, ledger: Ledger, pub, rec: dict, clip_meta: dict, video: Path, now: datetime, log: Log) -> None:
    slot = datetime.fromisoformat(rec["slot"])
    if slot <= now:
        fresh = schedule.next_slots(cfg.schedule, now, ledger.taken_slots(), 1)
        if not fresh:
            raise publish.PublishError("no free posting slot")
        slot = fresh[0]
        rec["slot"] = slot.isoformat()
    rec["publish"] = pub.publish({"id": rec["id"], "meta": clip_meta}, video, slot)
    rec["status"] = "scheduled"
    local = slot.astimezone(ZoneInfo(cfg.schedule.timezone))
    log(f"  {rec['id']} scheduled for {local.strftime('%a %d %b %H:%M %Z')} ({pub.name})")


def execute(cfg: Config, ledger: Ledger, plan_path: Path | None = None, now: datetime | None = None, log: Log = _log) -> dict:
    now = now or datetime.now(timezone.utc)
    plan_path = plan_path or latest_plan(cfg)
    if plan_path is None:
        raise FileNotFoundError("no plan.json found; run `plan` first")
    data = json.loads(plan_path.read_text())
    run_dir = plan_path.parent
    pub = publish.publisher(cfg)
    done = failed = 0
    for clip in data["clips"]:
        rec = ledger.clips.get(clip["id"])
        if rec is None or rec.get("status") not in ("planned", "failed"):
            continue
        rec["title"] = clip["meta"]["title"]
        if clip.get("drop"):
            rec["status"] = "dropped"
            ledger.save()
            continue
        try:
            video = render_clip(cfg, clip, run_dir)
            rec["file"], rec["meta"] = str(video), clip["meta"]
            if cfg.publish.hold_for_review:
                rec["status"] = "held"
                log(f"  {rec['id']} rendered and held for review")
            else:
                _send(cfg, ledger, pub, rec, clip["meta"], video, now, log)
            done += 1
        except (media.MediaError, publish.PublishError) as exc:
            rec["status"], rec["error"] = "failed", str(exc)[:400]
            data.setdefault("problems", []).append(f"{clip['id']}: {exc}")
            log(f"  {clip['id']} FAILED: {exc}")
            failed += 1
        ledger.save()
    for src in ledger.sources.values():
        if src.get("status") == "planned":
            src["status"] = "done"
    summary = {"run": data["run"], "mode": pub.name, "clips": done, "failed": failed, "rejected": len(data.get("rejected", [])), "problems": data.get("problems", [])}
    ledger.log_run(summary)
    ledger.save()
    return summary


def approve(cfg: Config, ledger: Ledger, ids: list[str], voiceovers: dict[str, str] | None = None, now: datetime | None = None, log: Log = _log) -> int:
    """Schedule clips that were held for review; re-render first when a voiceover is supplied."""
    now = now or datetime.now(timezone.utc)
    pub = publish.publisher(cfg)
    voiceovers = voiceovers or {}
    count = 0
    for rec in ledger.clips.values():
        if rec.get("status") != "held" or (ids and rec["id"] not in ids):
            continue
        video = Path(rec["file"])
        if rec["id"] in voiceovers:
            plan_path = cfg.path("out") / rec["run"] / "plan.json"
            clip = next(c for c in json.loads(plan_path.read_text())["clips"] if c["id"] == rec["id"])
            clip["voiceover"] = voiceovers[rec["id"]]
            video = render_clip(cfg, clip, plan_path.parent)
        _send(cfg, ledger, pub, rec, rec["meta"], video, now, log)
        ledger.save()
        count += 1
    return count


def refresh(cfg: Config, ledger: Ledger, now: datetime | None = None, log: Log = _log) -> int:
    """Pull post links and view counts for scheduled clips; mark them posted once links exist."""
    now = now or datetime.now(timezone.utc)
    pub = publish.publisher(cfg)
    changed = 0
    for rec in ledger.clips.values():
        if rec.get("status") not in ("scheduled", "posted") or rec.get("publish", {}).get("mode") != "live":
            continue
        age = now - datetime.fromisoformat(rec["slot"])
        if age.total_seconds() < 0 or age.days > 30:
            continue
        got = pub.refresh(rec)
        if got.get("links"):
            rec["links"] = {**rec.get("links", {}), **got["links"]}
            rec["status"] = "posted"
        if got.get("views"):
            rec["views"] = got["views"]
        if got.get("state"):
            rec["publish"]["state"] = got["state"]
        if got.get("error"):
            rec["publish"]["status_error"] = got["error"]
        changed += bool(got)
    ledger.save()
    log(f"status refreshed for {changed} clip(s)")
    return changed
