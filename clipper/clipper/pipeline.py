"""The daily run: footage in, scheduled posts out.

plan     collect footage, transcribe, find moments, apply the quality gates,
         write titles, assign posting slots  ->  out/<run>/plan.json
execute  render every planned clip and hand it to the publisher

plan.json is the review surface: before `execute`, a title, hook or section can
be edited, a clip dropped ("drop": true) or a voiceover attached ("voiceover": path).
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from . import captions, gates, intake, media, metadata, moments, presenter, publish, render, report, schedule, sections, transcribe
from .config import Config
from .state import Ledger, now_iso
from .transcribe import Word

Log = Callable[[str], None]


def _log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def ledger_of(cfg: Config) -> Ledger:
    return Ledger(cfg.path("state") / "ledger.json")


@dataclass
class Analysis:
    cands: list[moments.Candidate]
    duration: float
    whole: bool  # the video is short enough to be the clip itself
    text: str  # everything said in the video


def analyse(cfg: Config, path: Path, source_id: str, chat: list[float] | None = None, views: int = 0) -> Analysis:
    """Transcribe one video and work out its clip candidates.

    A short video (a Twitch clip, say) is already somebody's highlight, so it becomes
    one candidate covering all of it. A long video goes through the moment finder.
    A video of a few minutes in which nothing stands out gives its last stretch,
    which is where a guide shows what it was leading up to.
    """
    info = media.probe(path)
    if not info.has_audio:
        raise media.MediaError(f"{path.name}: no audio track")
    work = cfg.path("work")
    wav = media.extract_audio(path, work / "audio" / f"{source_id}.wav")
    words = transcribe.transcribe(cfg, wav, work / "transcripts" / f"{source_id}.json")
    text = " ".join(w.w for w in words)
    m = cfg.moments
    if info.duration <= m.whole_clip_max:
        score = round(math.log10(views + 10), 3)  # more-watched clips rank higher
        return Analysis([moments.Candidate(0.0, round(info.duration, 2), round(info.duration / 2, 2), score, words)], info.duration, True, text)
    found = moments.find(m, wav, words, info.duration, chat, cues=sections.all_cues(cfg))
    if not found and info.duration <= 300:
        end = round(info.duration - 2, 2)
        start = round(max(0.0, end - m.max_len), 2)
        found = [moments.Candidate(start, end, round((start + end) / 2, 2), 1.0, [w for w in words if w.s >= start and w.e <= end])]
    return Analysis(found, info.duration, False, text)


def _by_turns(pool: list[dict]) -> list[dict]:
    """Best clip of each section in turn, so a day's posts cover different sections."""
    groups: dict[str, list[dict]] = {}
    for entry in sorted(pool, key=lambda e: -e["cand"].score):
        groups.setdefault(entry["section"].name, []).append(entry)
    order = sorted(groups, key=lambda name: -groups[name][0]["cand"].score)
    out: list[dict] = []
    while any(groups.values()):
        out.extend(groups[name].pop(0) for name in order if groups[name])
    return out


def plan(cfg: Config, ledger: Ledger, now: datetime | None = None, log: Log = _log) -> dict:
    now = now or datetime.now(timezone.utc)
    run_id = now.strftime("%Y%m%d-%H%M%S")
    run_dir = cfg.path("out") / run_id
    pool: list[dict] = []
    rejected: list[dict] = []
    problems: list[str] = []
    seen: list[tuple[str, str]] = []
    # Clips planned by a run that never finished are forgotten; their footage is analysed again.
    for stale in [k for k, c in ledger.clips.items() if c.get("status") == "planned"]:
        del ledger.clips[stale]

    for item in intake.collect(cfg, ledger, log):
        log(f"analysing: {item.name[:70]}")

        def leave_out(reason: str) -> None:
            ledger.sources[item.source_id] = {"name": item.name, "origin": item.origin, "creator": item.creator,
                                              "status": "skipped", "reason": reason, "processed": now_iso()}
            rejected.append({"source": item.name, "start": 0.0, "end": 0.0, "score": 0.0, "reasons": [reason]})
            log(f"  left out: {reason}")

        try:
            # Gameplay only: a video with a streamer on camera or talking over it is left out whole.
            minutes = media.probe(item.path).duration / 60
            if cfg.filters.no_facecam:
                on_camera, where = presenter.facecam(cfg, item.path, 0.0, minutes * 60)
                if on_camera:
                    leave_out(f"streamer facecam ({where})")
                    continue
            log(f"  transcribing {minutes:.0f} min of audio and looking for highlights (about {max(1, round(minutes / 6))} min)")
            found = analyse(cfg, item.path, item.source_id, intake.load_chat(item.chat_file), item.views)
        except (media.MediaError, transcribe.TranscribeError) as exc:
            problems.append(f"{item.name}: {exc}")
            ledger.sources[item.source_id] = {"name": item.name, "origin": item.origin, "status": "failed", "reason": str(exc)[:300], "processed": now_iso()}
            continue
        cands, duration, whole = found.cands, found.duration, found.whole
        said = presenter.talk(cfg, found.text) if cfg.filters.no_commentary else []
        if said:
            leave_out(f"someone is talking over the game (says: {', '.join(said[:4])})")
            continue
        kept = 0
        for cand in cands:
            reasons = gates.check(cfg, cand, item.source_id, ledger, whole)
            if not reasons and not whole:  # a passage of a long video gets its own look
                reasons = presenter.check(cfg, item.path, cand.start, cand.end, cand.text)
            if not reasons and kept >= cfg.moments.max_per_source:
                reasons = ["over the per-video limit"]
            text = gates.normalise(cand.text)
            if not reasons and len(text) >= gates.MIN_TEXT and any(
                    sid != item.source_id and len(t) >= gates.MIN_TEXT and SequenceMatcher(None, text, t).ratio() > cfg.gates.max_text_similarity
                    for t, sid in seen):
                reasons = ["same moment as another clip in this run"]
            if reasons:
                rejected.append({"source": item.name, "start": cand.start, "end": cand.end, "score": cand.score, "reasons": reasons})
                continue
            kept += 1
            seen.append((text, item.source_id))
            section = sections.classify(cfg, cand.text, item.title, item.section)
            pool.append({"cand": cand, "item": item, "section": section, "whole": whole})
        ledger.sources[item.source_id] = {
            "name": item.name, "origin": item.origin, "creator": item.creator, "permission": item.permission,
            "duration": round(duration, 1), "candidates": len(cands), "kept": kept, "status": "planned", "processed": now_iso(),
        }
        log(f"  {len(cands)} moment(s) found, {kept} passed the checks")

    pool = _by_turns(pool)
    slots = schedule.next_slots(cfg.schedule, now, ledger.taken_slots(), len(pool))
    clips: list[dict] = []
    for entry, slot in zip(pool, slots):
        cand, item, section = entry["cand"], entry["item"], entry["section"]
        clip_id = "c" + media.text_id(f"{item.source_id}:{cand.start:.1f}")[:10]
        clip = {
            "id": clip_id, "source_id": item.source_id, "source_name": item.name, "source_path": str(item.path),
            "origin": item.origin, "creator": item.creator, "permission": item.permission, "section": section.name,
            "start": cand.start, "end": cand.end, "peak": cand.peak, "score": cand.score,
            "text": cand.text, "words": [asdict(w) for w in cand.words],
            "meta": metadata.build(cfg, cand, clip_id, item.creator, section, item.title, item.origin, entry["whole"]),
            "slot": slot.isoformat(), "voiceover": "", "drop": False,
        }
        try:  # a strip of frames, so whoever reviews the plan can see what happens in the clip
            clip["sheet"] = str(media.contact_sheet(item.path, cand.start, cand.end, run_dir / f"{clip_id}.jpg"))
        except media.MediaError:
            clip["sheet"] = ""
        clips.append(clip)
        ledger.clips[clip_id] = {
            "id": clip_id, "source_id": item.source_id, "source_name": item.name, "creator": item.creator,
            "origin": item.origin, "section": section.name,
            "start": cand.start, "end": cand.end, "score": cand.score, "text": gates.normalise(cand.text),
            "title": clip["meta"]["title"], "slot": clip["slot"], "status": "planned", "run": run_id, "created": now_iso(),
        }
    for entry in pool[len(slots):]:
        cand, item = entry["cand"], entry["item"]
        rejected.append({"source": item.name, "start": cand.start, "end": cand.end, "score": cand.score, "reasons": ["no free posting slot in the scheduling window"]})

    result = {"run": run_id, "created": now_iso(), "clips": clips, "rejected": rejected, "problems": problems}
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "plan.json").write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    ledger.save()
    log(f"plan {run_id}: {len(clips)} clip(s) planned, {len(rejected)} rejected, {len(problems)} problem(s)")
    return result


def latest_plan(cfg: Config) -> Path | None:
    plans = sorted(cfg.path("out").glob("*/plan.json"))
    return plans[-1] if plans else None


def render_clip(cfg: Config, clip: dict, run_dir: Path) -> Path:
    start, end = clip["start"], clip["end"]
    meta = clip["meta"]
    section = cfg.section(clip.get("section") or meta.get("section", ""))  # the section may have been changed in plan.json
    rel = [Word(w["w"], round(w["s"] - start, 3), round(w["e"] - start, 3)) for w in clip["words"]]
    ass = cfg.path("work") / "captions" / f"{clip['id']}.ass"
    ass.parent.mkdir(parents=True, exist_ok=True)
    ass.write_text(captions.build(
        cfg, rel, end - start, hook=meta.get("hook", ""), label=meta.get("label", ""),
        badge=section.label, badge_color=section.color, credit=meta.get("credit", ""),
    ), encoding="utf-8")
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
    data = json.loads(plan_path.read_text(encoding="utf-8"))
    run_dir = plan_path.parent
    pub = publish.publisher(cfg)
    done = failed = 0
    for clip in data["clips"]:
        rec = ledger.clips.get(clip["id"])
        if rec is None or rec.get("status") not in ("planned", "failed"):
            continue
        rec["title"] = clip["meta"]["title"]
        rec["section"] = cfg.section(clip.get("section", "")).name
        if clip.get("section") != clip["meta"].get("section"):
            sec = cfg.section(clip.get("section", ""))
            clip["meta"].update(section=sec.name, badge=sec.label, badge_color=sec.color, playlist=sec.youtube_playlist_id)
        if clip.get("drop"):
            rec["status"] = "dropped"
            ledger.save()
            continue
        try:
            video = render_clip(cfg, clip, run_dir)
            rec["file"], rec["meta"] = str(video), clip["meta"]
            video.with_suffix(".txt").write_text(report.post_kit(cfg, clip, rec["slot"]), encoding="utf-8")  # the text to post with it
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
            clip = next(c for c in json.loads(plan_path.read_text(encoding="utf-8"))["clips"] if c["id"] == rec["id"])
            clip["voiceover"] = voiceovers[rec["id"]]
            video = render_clip(cfg, clip, plan_path.parent)
        _send(cfg, ledger, pub, rec, rec["meta"], video, now, log)
        ledger.save()
        count += 1
    return count


def cleanup(cfg: Config, now: datetime | None = None, log: Log = _log) -> dict:
    """Delete old downloads, working files and finished clips so the disk does not fill up.

    Only the clipper's own folders are touched: work/downloads, work/audio,
    work/transcripts, work/captions, and the dated run folders inside out/.
    """
    now = now or datetime.now(timezone.utc)
    h = cfg.housekeeping
    freed, removed = 0, 0

    def too_old(path: Path, days: int) -> bool:
        return now.timestamp() - path.stat().st_mtime > days * 86400

    work = cfg.path("work")
    for name in ("downloads", "audio", "transcripts", "captions"):
        folder = work / name
        if not folder.is_dir():
            continue
        for f in folder.iterdir():
            if f.is_file() and too_old(f, h.keep_downloads_days):
                freed += f.stat().st_size
                f.unlink()
                removed += 1
    out = cfg.path("out")
    if out.is_dir():
        for run in out.iterdir():
            if run.is_dir() and (run / "plan.json").exists() and too_old(run / "plan.json", h.keep_clips_days):
                for f in run.iterdir():
                    if f.is_file():
                        freed += f.stat().st_size
                        f.unlink()
                        removed += 1
                if not any(run.iterdir()):
                    run.rmdir()
    log(f"cleanup: {removed} old file(s) deleted, {freed / 1e6:.0f} MB freed")
    return {"files": removed, "bytes": freed}


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
