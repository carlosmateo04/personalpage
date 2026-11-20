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

from . import (captions, claude, gates, intake, media, metadata, moments, outbox, presenter, publish, render, report, review, schedule,
               monetize, scout, sections, transcribe, variants)
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
    words: list[Word]  # the same, word by word with timings


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
        return Analysis([moments.Candidate(0.0, round(info.duration, 2), round(info.duration / 2, 2), score, words)], info.duration, True, text, words)
    found = moments.find(m, wav, words, info.duration, chat, cues=sections.all_cues(cfg))
    if not found and info.duration <= 300:
        end = round(info.duration - 2, 2)
        start = round(max(0.0, end - m.max_len), 2)
        found = [moments.Candidate(start, end, round((start + end) / 2, 2), 1.0, [w for w in words if w.s >= start and w.e <= end])]
    return Analysis(found, info.duration, False, text, words)


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
    notes: list[str] = []  # things worth knowing that are not failures
    seen: list[tuple[str, str]] = []
    scouting = cfg.ai.scout and claude.available(cfg)  # Claude picks the moments in long videos
    editing = review.available(cfg)  # and will be shown each clip afterwards
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
        avoid: list[tuple[float, float]] = []
        if cfg.filters.no_commentary and whole:
            said = presenter.talk(cfg, found.text)
            if said:
                leave_out(f"someone is talking over the game (says: {', '.join(said[:4])})")
                continue
        elif cfg.filters.no_commentary:
            verdict, avoid = presenter.commentary(cfg, found.words, duration)
            spots = ", ".join(presenter.clock(a) for a, _ in avoid[:6])
            if verdict == "throughout":
                leave_out(f"someone is talking over the game throughout (around {spots})")
                continue
            if verdict == "parts":
                log(f"  someone talks about the game around {spots}; those parts are skipped")
        if scouting and not whole:
            try:
                picked = scout.find(cfg, item.path, item.title or item.name, item.creator, duration, found.words,
                                    cfg.path("work") / "audio" / f"{item.source_id}.wav", cands, cfg.path("work") / "boards", item.source_id, log)
            except (claude.ClaudeError, media.MediaError) as exc:
                notes.append(f"{item.name}: Claude could not look at the video ({exc}); the loudness finder was used")
                log(f"  Claude could not look at this video ({exc}); using the loudness finder")
                scouting = scouting and not getattr(exc, "fatal", False)
            else:
                if not picked.usable:
                    leave_out(f"Claude: {picked.problem or 'this video is not usable gameplay'}")
                    continue
                for line in picked.skipped:
                    log(f"  a pick was not used ({line})")
                cands = picked.cands
                log(f"  Claude picked {len(cands)} moment(s)" + (": " + "; ".join(f"{media.clock(c.start)} {c.ai['title']}" for c in cands) if cands else ""))
        kept = 0
        for cand in cands:
            reasons = gates.check(cfg, cand, item.source_id, ledger, whole)
            if not reasons and any(cand.start < b and cand.end > a for a, b in avoid):
                reasons = ["overlaps a stretch where someone talks about the game"]
            if not reasons and not whole:  # a passage of a long video gets its own look
                reasons = presenter.check(cfg, item.path, cand.start, cand.end, cand.text)
            elif not reasons and cfg.filters.no_cutscenes:  # a short video used whole was checked for faces and talk above
                barred, why = presenter.cutscene(cfg, presenter.frames(item.path, cand.start, cand.end, cfg.filters.facecam_frames))
                reasons = [f"story cutscene ({why})"] if barred else []
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
            section = cfg.section(cand.ai["section"]) if cand.ai else sections.classify(cfg, cand.text, item.title, item.section)
            pool.append({"cand": cand, "item": item, "section": section, "whole": whole, "duration": duration, "words": found.words})
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
            "meta": (metadata.compose(cfg, clip_id, item.creator, section, cand.ai["title"], cand.ai["hook"], item.origin, summary=cand.ai["why"])
                     if cand.ai else metadata.build(cfg, cand, clip_id, item.creator, section, item.title, item.origin, entry["whole"])),
            "slot": slot.isoformat(), "voiceover": "", "drop": False, "whole": entry["whole"],
        }
        if cand.ai:
            clip["scout"] = cand.ai
        # What surrounds the clip: the editor may move the cut into it.
        margin = 0.0 if entry["whole"] else cfg.ai.margin
        low, high = max(0.0, cand.start - margin), min(entry["duration"], cand.end + margin)
        clip["context"] = {"from": round(low, 2), "to": round(high, 2),
                           "words": [asdict(w) for w in entry["words"] if w.s >= low and w.e <= high]}
        try:  # frames with their times, for the editor and for anyone who looks at the plan
            count = cfg.ai.frames if editing else 8
            times = [low + (k + 0.5) * (high - low) / count for k in range(count)]
            clip["sheet"] = media.sheets(item.path, times, run_dir, clip_id, cols=4, rows=-(-count // 4))[0]["path"]
        except media.MediaError:
            clip["sheet"] = ""
        clips.append(clip)
        ledger.clips[clip_id] = {
            "id": clip_id, "source_id": item.source_id, "source_name": item.name, "creator": item.creator,
            "origin": item.origin, "section": section.name, "permission": item.permission,
            "start": cand.start, "end": cand.end, "score": cand.score, "text": gates.normalise(cand.text),
            "title": clip["meta"]["title"], "slot": clip["slot"], "status": "planned", "run": run_id, "created": now_iso(),
        }
    for entry in pool[len(slots):]:
        cand, item = entry["cand"], entry["item"]
        rejected.append({"source": item.name, "start": cand.start, "end": cand.end, "score": cand.score, "reasons": ["no free posting slot in the scheduling window"]})

    result = {"run": run_id, "created": now_iso(), "clips": clips, "rejected": rejected, "problems": problems, "notes": notes}
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "plan.json").write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    ledger.save()
    log(f"plan {run_id}: {len(clips)} clip(s) planned, {len(rejected)} rejected, {len(problems)} problem(s)")
    return result


def text_problems(cfg: Config, ledger: Ledger, rec: dict, meta: dict) -> list[str]:
    """What the search check finds in a clip's text, plus a title that a finished clip already carries.

    Three clips from one video with the same title look like the same post three times,
    to viewers and to the platforms; each clip needs its own.
    """
    problems = metadata.proof(cfg, meta)
    title = str(meta.get("title", "")).strip().lower()
    if title and any(other.get("id") != rec["id"] and other.get("status") in ("held", "scheduled", "posted")
                     and str(other.get("title", "")).strip().lower() == title for other in ledger.clips.values()):
        problems.append("another clip already has this title")
    return problems


def latest_plan(cfg: Config) -> Path | None:
    plans = sorted(cfg.path("out").glob("*/plan.json"))
    return plans[-1] if plans else None


def render_clip(cfg: Config, clip: dict, run_dir: Path, cut: list[float] | None = None) -> Path:
    """Render a clip, or with `cut` ([start, end] in the source video) a platform's shorter version of it.

    A version carries the same headline, badge and credit; the captions, the on-screen notes and the cover
    are the part of the clip's own that falls inside the cut.
    """
    out = run_dir / f"{clip['id']}.mp4"
    start, end = clip["start"], clip["end"]
    notes, at, voice = clip.get("callouts") or [], clip.get("cover"), None
    if cut:
        out = variants.name_of(out, cut)
        shift = cut[0] - start
        start, end = cut
        notes = [dict(note, at=round(note["at"] - shift, 1)) for note in notes if 0 <= note["at"] - shift <= (end - start) - captions.CALLOUT_SECONDS]
        at = at - shift if isinstance(at, (int, float)) and 0 <= at - shift <= end - start else None
    elif clip.get("voiceover"):
        voice = Path(clip["voiceover"])
        if not voice.exists():
            raise media.MediaError(f"voiceover {voice} not found")
    meta = clip["meta"]
    section = cfg.section(clip.get("section") or meta.get("section", ""))  # the section may have been changed in plan.json
    rel = [Word(w["w"], round(w["s"] - start, 3), round(w["e"] - start, 3)) for w in clip["words"] if w["s"] >= start and w["e"] <= end]
    ass = cfg.path("work") / "captions" / f"{out.stem}.ass"
    ass.parent.mkdir(parents=True, exist_ok=True)
    ass.write_text(captions.build(
        cfg, rel, end - start, hook=meta.get("hook", ""), label=meta.get("label", ""),
        badge=section.label, badge_color=section.color, credit=meta.get("credit", ""), callouts=notes,
    ), encoding="utf-8")
    # Strong language is silenced in the sound, as it is masked in the captions (see clipper/monetize.py).
    render.render(cfg, Path(clip["source_path"]), start, end, ass, out, voice, monetize.mute_spans(cfg, rel))
    got = media.probe(out)
    if abs(got.duration - (end - start)) > 1.0 or not got.has_audio:
        raise media.MediaError(f"render check failed: {got.duration:.1f}s, audio={got.has_audio}")
    # The cover: the frame the editor chose, else one a third of the way in (past the headline); never a graphic moment.
    gory = [g - start for g in clip.get("graphic") or [] if start <= g <= end] if cfg.monetize.enabled else []
    media.grab(out, monetize.safe_cover(end - start, at, gory), cover_of(out), width=720)
    return out


def money_check(cfg: Config, clip: dict, rec: dict, log: Log = _log, look: bool = True) -> None:
    """Measure a finished clip against each platform's rules for paying, and keep the result on its record."""
    if not cfg.monetize.enabled:
        rec.pop("money", None)
        return
    rec["graphic"] = clip.get("graphic") or []
    rec["strong"] = monetize.strong_words(clip.get("words") or [])
    rec["voiceover"] = bool(clip.get("voiceover"))
    if look or "seen" not in rec:
        rec["seen"] = monetize.look(cfg, clip)
    rec["money"] = monetize.check(cfg, rec)
    said = monetize.summary(rec["money"])
    if said != "every rule met":
        log(f"  {clip['id']} monetization check: {said}")


def render_versions(cfg: Config, clip: dict, run_dir: Path, rec: dict, log: Log = _log) -> None:
    """Render the cut of each platform whose rules the clip's own length does not meet, and note them on the record.

    A clip with a voiceover keeps one version: the voice was recorded for the whole clip.
    """
    for old in {entry["file"] for entry in (rec.get("cuts") or {}).values()}:
        for stale in (Path(old), cover_of(Path(old))):
            stale.unlink(missing_ok=True)
    rec.pop("cuts", None)
    rec.pop("unfit", None)
    if clip.get("voiceover"):
        return
    found = variants.plan(cfg, clip)
    made: dict[tuple, Path] = {}
    cuts: dict[str, dict] = {}
    for name, cut in found["cuts"].items():
        if tuple(cut) not in made:
            made[tuple(cut)] = render_clip(cfg, clip, run_dir, cut)
        cuts[name] = {"start": cut[0], "end": cut[1], "file": str(made[tuple(cut)])}
    if cuts:
        rec["cuts"] = cuts
        by_file: dict[str, list[str]] = {}
        for name, entry in cuts.items():
            by_file.setdefault(f"{entry['end'] - entry['start']:.0f} seconds", []).append(name)
        log(f"  {clip['id']} also cut to " + "; ".join(f"{length} for {', '.join(names)}" for length, names in by_file.items()))
    if found["unfit"]:
        rec["unfit"] = found["unfit"]


def cover_of(video: Path) -> Path:
    return video.with_name(video.stem + ".cover.jpg")


def _send(cfg: Config, ledger: Ledger, rec: dict, clip_meta: dict, video: Path, now: datetime, log: Log) -> None:
    """Give a finished clip its posting time and put it in the outbox (or, in a dry run, write down what would be sent)."""
    slot = datetime.fromisoformat(rec["slot"])
    if slot <= now:
        fresh = schedule.next_slots(cfg.schedule, now, ledger.taken_slots(), 1)
        if not fresh:
            raise publish.PublishError("no free posting slot")
        slot = fresh[0]
        rec["slot"] = slot.isoformat()
    local = slot.astimezone(ZoneInfo(cfg.schedule.timezone)).strftime("%a %d %b %H:%M %Z")
    if cfg.publish.mode == "live":
        result = outbox.enqueue(cfg, ledger, rec, slot, now=now)
        left = "".join(f"; not {name} ({why})" for name, why in result["skipped"].items())
        log(f"  {rec['id']} scheduled for {local} (live: {', '.join(result['queued'])}{left})")
    else:
        rec["publish"] = publish.DryRun(cfg).publish({"id": rec["id"], "meta": clip_meta}, video, slot)
        rec["status"] = "scheduled"
        log(f"  {rec['id']} scheduled for {local} (dry_run)")


def execute(cfg: Config, ledger: Ledger, plan_path: Path | None = None, now: datetime | None = None, log: Log = _log) -> dict:
    now = now or datetime.now(timezone.utc)
    plan_path = plan_path or latest_plan(cfg)
    if plan_path is None:
        raise FileNotFoundError("no plan.json found; run `plan` first")
    data = json.loads(plan_path.read_text(encoding="utf-8"))
    run_dir = plan_path.parent
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
        if clip.get("review"):
            rec["review"] = clip["review"]
        if clip.get("drop"):
            rec["status"] = "dropped"
            if clip.get("review") and not clip["review"].get("keep", True):
                rec["reason"] = clip["review"].get("reason", "")
                log(f"  {rec['id']} not rendered: the review dropped it")
            ledger.save()
            continue
        try:
            video = render_clip(cfg, clip, run_dir)
            render_versions(cfg, clip, run_dir, rec, log)
            rec["file"], rec["meta"] = str(video), clip["meta"]
            # The editor may have moved the cut; the ledger keeps what was actually made.
            rec.update(start=clip["start"], end=clip["end"], text=gates.normalise(clip.get("text", "")), cover=clip.get("cover"))
            if clip.get("scout"):
                rec["scout"] = clip["scout"]
            rec["seo"] = text_problems(cfg, ledger, rec, clip["meta"])  # an empty list means the title, captions and tags pass
            if clip.get("review"):
                rec["review"] = clip["review"]
            money_check(cfg, clip, rec, log)
            waits = monetize.holds(cfg, rec)
            video.with_suffix(".txt").write_text(report.post_kit(cfg, clip, rec["slot"]), encoding="utf-8")  # the text to post with it
            if cfg.publish.hold_for_review:
                rec["status"] = "held"
                log(f"  {rec['id']} rendered and held for review")
            elif rec["seo"]:
                rec["status"] = "held"  # nothing with a weak title or a broken caption is posted by itself
                log(f"  {rec['id']} rendered but not posted, its text needs a look: {'; '.join(rec['seo'])}")
            elif waits:
                rec["status"] = "held"
                log(f"  {rec['id']} rendered but not posted by itself: {waits}")
            else:
                try:
                    _send(cfg, ledger, rec, clip["meta"], video, now, log)
                except publish.PublishError as exc:  # the clip is made; it only could not be queued
                    rec["status"], rec["error"] = "held", str(exc)[:400]
                    data.setdefault("notes", []).append(f"{clip['id']} was made but not queued for posting: {exc}")
                    log(f"  {rec['id']} rendered but not queued for posting: {exc}")
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
    ledger.save()
    if cfg.publish.mode == "live":
        outbox.deliver(cfg, ledger, now, log)  # hand over what the platforms can hold until its time
    summary = {"run": data["run"], "mode": cfg.publish.mode, "clips": done, "failed": failed, "rejected": len(data.get("rejected", [])),
               "problems": data.get("problems", []), "notes": data.get("notes", [])}
    ledger.log_run(summary)
    ledger.save()
    return summary


def approve(cfg: Config, ledger: Ledger, ids: list[str], voiceovers: dict[str, str] | None = None, now: datetime | None = None, log: Log = _log) -> int:
    """Schedule clips that were held for review; re-render first when a voiceover is supplied."""
    now = now or datetime.now(timezone.utc)
    voiceovers = voiceovers or {}
    count = 0
    for rec in list(ledger.clips.values()):
        if rec.get("status") != "held" or (ids and rec["id"] not in ids):
            continue
        video = Path(rec["file"])
        if rec["id"] in voiceovers:
            plan_path = cfg.path("out") / rec["run"] / "plan.json"
            clip = next(c for c in json.loads(plan_path.read_text(encoding="utf-8"))["clips"] if c["id"] == rec["id"])
            clip["voiceover"] = voiceovers[rec["id"]]
            video = render_clip(cfg, clip, plan_path.parent)
            render_versions(cfg, clip, plan_path.parent, rec, log)
        _send(cfg, ledger, rec, rec["meta"], video, now, log)
        ledger.save()
        count += 1
    if count and cfg.publish.mode == "live":
        outbox.deliver(cfg, ledger, now, log)
    return count


# --- actions on finished clips, used by the app -------------------------------

READY = ("held", "rendered")  # rendered and waiting for the user
TIKTOK_AUDIENCES = ("PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "SELF_ONLY")


def clip_state(rec: dict) -> str:
    """What a clip is to the person looking at it: ready, queued, posted, failed or dropped."""
    status, mode = rec.get("status"), rec.get("publish", {}).get("mode")
    if status in READY or (status == "scheduled" and mode != "live"):
        return "ready"
    if status == "scheduled":
        return "queued"
    return status if status in ("posted", "failed", "dropped") else "pending"


def _kit(cfg: Config, rec: dict) -> None:
    """Rewrite the ready-to-paste text file that sits next to the clip."""
    video = Path(rec.get("file", ""))
    if rec.get("meta") and video.parent.is_dir():
        video.with_suffix(".txt").write_text(report.post_kit(cfg, rec, rec["slot"]), encoding="utf-8")


def update_clip(cfg: Config, ledger: Ledger, clip_id: str, fields: dict) -> dict:
    """Change a clip's title, caption or YouTube description (the text posted with it)."""
    rec = ledger.clips[clip_id]
    meta = rec.setdefault("meta", {})
    if "title" in fields:
        title = str(fields["title"]).strip()[:100]
        meta["title"] = rec["title"] = title
        # The YouTube title keeps the game's name and the section's search keyword after an edit.
        section = cfg.section(rec.get("section") or meta.get("section", ""))
        meta.setdefault("youtube", {})["title"] = title if "#shorts" in title.lower() else metadata.youtube_title(cfg, title, section)
    if "caption" in fields:
        caption = str(fields["caption"]).strip()[:2200]
        for platform in ("tiktok", "instagram", "facebook"):
            meta.setdefault(platform, {})["title"] = caption
    if "description" in fields:
        meta.setdefault("youtube", {})["description"] = str(fields["description"]).strip()[:5000]
    if "tiktok" in fields:  # how this clip is posted on TikTok: audience, comments, duet, stitch, disclosure
        chosen = fields["tiktok"] if isinstance(fields["tiktok"], dict) else {}
        kept = {key: bool(chosen[key]) for key in ("comments", "duet", "stitch", "own_brand", "paid") if key in chosen}
        if chosen.get("audience"):
            if chosen["audience"] not in TIKTOK_AUDIENCES:
                raise publish.PublishError("TikTok audience: choose everyone, friends, followers or only me")
            kept["audience"] = chosen["audience"]
        rec["tiktok"] = {**rec.get("tiktok", {}), **kept}
    rec["seo"] = text_problems(cfg, ledger, rec, meta)
    if cfg.monetize.enabled and "money" in rec:
        rec["money"] = monetize.check(cfg, rec)  # the hashtags and the wording are part of it
    _kit(cfg, rec)
    ledger.save()
    return rec


def posted_by_hand(cfg: Config, ledger: Ledger, clip_id: str, platform: str, link: str = "") -> dict:
    """The user posted this clip on a platform themselves: keep that on record and do not send it there."""
    rec = ledger.clips[clip_id]
    if not Path(rec.get("file", "")).name or rec.get("status") in ("planned", "dropped", "failed"):
        raise publish.PublishError("only a finished clip can be marked as posted")
    outbox.by_hand(cfg, ledger, rec, platform, link)
    return rec


def drop_clip(cfg: Config, ledger: Ledger, clip_id: str) -> dict:
    rec = ledger.clips[clip_id]
    if clip_state(rec) != "ready":
        raise publish.PublishError("only a clip that has not been sent can be dropped")
    rec["status"] = "dropped"
    ledger.save()
    return rec


def post_clip(cfg: Config, ledger: Ledger, clip_id: str, when: str = "now", platforms: list[str] | None = None,
              now: datetime | None = None, log: Log = _log) -> dict:
    """Send a ready clip to the connected accounts: straight away ("now") or at the next free posting time ("next")."""
    now = now or datetime.now(timezone.utc)
    rec = ledger.clips[clip_id]
    state = clip_state(rec)
    unsent = [name for name in cfg.publish.platforms if name not in (rec.get("posts") or {})]
    # A clip that is already out can still go to platforms it has not been sent to, when they are named
    # (posted by hand on one, say, and the others still to go). Without names, nothing is sent a second time.
    if state != "ready" and not (state in ("queued", "posted") and platforms and all(name in unsent for name in platforms)):
        raise publish.PublishError("this clip has already been sent or dropped")
    slot = None
    if when == "next":
        free = schedule.next_slots(cfg.schedule, now, ledger.taken_slots() - {rec.get("slot")}, 1)
        if not free:
            raise publish.PublishError("no free posting slot in the scheduling window")
        slot = free[0]
    rec.pop("publish", None)  # whatever a dry run wrote down
    outbox.enqueue(cfg, ledger, rec, slot, platforms, now)
    outbox.deliver(cfg, ledger, now, log, only=clip_id)
    if rec["status"] == "failed":
        raise publish.PublishError(rec.get("error") or "the clip could not be sent")
    return rec


def retry_clip(cfg: Config, ledger: Ledger, clip_id: str, now: datetime | None = None, log: Log = _log) -> dict:
    """Send a clip's failed posts again."""
    now = now or datetime.now(timezone.utc)
    rec = ledger.clips[clip_id]
    if not outbox.retry(cfg, ledger, rec, now):
        raise publish.PublishError("nothing to send again: connect the account first")
    outbox.deliver(cfg, ledger, now, log, only=clip_id)
    return rec


def take_back(cfg: Config, ledger: Ledger, clip_id: str) -> dict:
    """Take a clip out of the queue, as far as it has not been handed to a platform yet."""
    rec = ledger.clips[clip_id]
    if not outbox.take_back(cfg, ledger, rec):
        raise publish.PublishError("every post of this clip is already with its platform; change it there")
    return rec


def reedit(cfg: Config, ledger: Ledger, clip_id: str, instruction: str = "", log: Log = _log) -> dict:
    """Have Claude edit a finished clip again, with an instruction from the app, and render it anew.

    The text may have been changed by hand since; Claude starts from the clip as it is now.
    Nothing is changed unless the whole thing succeeds.
    """
    rec = ledger.clips[clip_id]
    if clip_state(rec) != "ready":
        raise publish.PublishError("only a clip that has not been sent can be edited again")
    why = review.unavailable(cfg)
    if why:
        raise publish.PublishError(f"Claude cannot be used: {why}")
    plan_path = cfg.path("out") / str(rec.get("run", "")) / "plan.json"
    data = json.loads(plan_path.read_text(encoding="utf-8")) if plan_path.exists() else {"clips": []}
    clip = next((c for c in data["clips"] if c["id"] == clip_id), None)
    if clip is None or not Path(clip["source_path"]).exists():
        raise publish.PublishError("the video this clip was cut from is no longer on this computer, so it cannot be cut again")
    draft = json.loads(json.dumps(clip))
    draft["meta"] = json.loads(json.dumps(rec["meta"]))
    draft.pop("review", None)
    try:
        verdict = review.ask(cfg, draft, instruction)
    except claude.ClaudeError as exc:
        raise publish.PublishError(f"Claude could not be asked: {exc}") from exc
    if not verdict["keep"]:
        raise publish.PublishError(f"Claude would not keep this clip ({str(verdict.get('reason', '')).strip()[:200]}). Nothing was changed.")
    done = review.edit(cfg, draft, verdict)
    try:
        video = render_clip(cfg, draft, plan_path.parent)
        render_versions(cfg, draft, plan_path.parent, rec, log)
    except media.MediaError as exc:
        raise publish.PublishError(f"the clip could not be rendered again: {str(exc)[:200]}") from exc
    rec.update(file=str(video), meta=draft["meta"], title=draft["meta"]["title"], section=cfg.section(draft.get("section", "")).name,
               start=draft["start"], end=draft["end"], text=gates.normalise(draft.get("text", "")), cover=draft.get("cover"), review=draft["review"])
    rec["seo"] = text_problems(cfg, ledger, rec, draft["meta"])
    money_check(cfg, draft, rec, log)
    _kit(cfg, rec)
    data["clips"] = [draft if c["id"] == clip_id else c for c in data["clips"]]
    plan_path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    ledger.save()
    log(f"  {clip_id} {done}")
    return rec


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
    for name in ("downloads", "audio", "transcripts", "captions", "boards"):
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
    """Ask the platforms what became of the posts: whether they are out, their links and their views."""
    return outbox.refresh(cfg, ledger, now, log)
