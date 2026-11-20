"""Command line: python -m clipper <command>."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from . import config, media, pipeline, publish, report, review, sections, transcribe
from .bootstrap import js_runtime
from .captions import build as build_captions
from .metadata import build as build_meta
from .transcribe import Word


def cmd_doctor(cfg, args) -> int:
    ok = True

    def line(good: bool, text: str) -> None:
        nonlocal ok
        ok = ok and good
        print(("ok   " if good else "FAIL ") + text)

    line(shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None, "ffmpeg and ffprobe installed")
    line(transcribe.models_present(cfg), f"speech models in {cfg.models_dir}")
    runtime, version = js_runtime()
    line(bool(runtime), f"JavaScript runtime for YouTube downloads: {runtime} {version}" if runtime else "JavaScript runtime for YouTube downloads (Deno 2.3+ or Node 22+)")
    line(any(cfg.fonts_dir.glob("*.ttf")), f"caption font in {cfg.fonts_dir}")
    for s in cfg.sources:
        print(f"     source: {s.type} {s.path or s.file or s.url} (section: {s.section or 'auto'}, permission: {s.permission or 'none on file'})")
    print(f"     sections: {', '.join(sec.name for sec in cfg.sections)}")
    print(f"     posting slots: {', '.join(cfg.schedule.slots)} {cfg.schedule.timezone}")
    print(f"     campaign: {cfg.campaign.name or '-'} (paid: {cfg.campaign.paid})")
    try:
        line(True, publish.publisher(cfg).check())
    except publish.PublishError as exc:
        line(False, str(exc))
    why = review.unavailable(cfg)
    print(f"     automatic review: {'off: ' + why if why else 'on, ' + cfg.review.model}")
    return 0 if ok else 1


def cmd_clip(cfg, args) -> int:
    """One-off: turn a video file into finished clips, without the ledger or posting."""
    src = Path(args.video).resolve()
    sid = media.file_id(src)
    cands = pipeline.analyse(cfg, src, sid).cands
    out_dir = Path(args.out).resolve() if args.out else cfg.path("out") / "manual" / src.stem
    for n, cand in enumerate(cands[: args.count], 1):
        clip_id = f"{src.stem}-{n:02d}"
        section = sections.classify(cfg, cand.text, "", args.section)
        meta = build_meta(cfg, cand, clip_id, args.creator, section)
        rel = [Word(w.w, round(w.s - cand.start, 3), round(w.e - cand.start, 3)) for w in cand.words]
        ass = cfg.path("work") / "captions" / f"{clip_id}.ass"
        ass.parent.mkdir(parents=True, exist_ok=True)
        ass.write_text(build_captions(cfg, rel, cand.duration, hook=meta["hook"], label=meta["label"],
                                      badge=meta["badge"], badge_color=meta["badge_color"], credit=meta["credit"]), encoding="utf-8")
        from .render import render

        out = render(cfg, src, cand.start, cand.end, ass, out_dir / f"{clip_id}.mp4")
        (out_dir / f"{clip_id}.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"{out}  [{cand.start:.0f}s-{cand.end:.0f}s, score {cand.score}]  {meta['title']}")
    if not cands:
        print("no clip-worthy moments found")
    return 0


def cmd_plan(cfg, args) -> int:
    result = pipeline.plan(cfg, pipeline.ledger_of(cfg))
    print(cfg.path("out") / result["run"] / "plan.json")
    return 0


def _finish(cfg, ledger, summary) -> int:
    path = report.write(cfg, ledger, summary)
    print(f"report: {path}")
    return 1 if summary["failed"] or summary["problems"] else 0


def cmd_execute(cfg, args) -> int:
    ledger = pipeline.ledger_of(cfg)
    summary = pipeline.execute(cfg, ledger, Path(args.plan) if args.plan else None)
    return _finish(cfg, ledger, summary)


def _review(cfg, plan_path: Path) -> None:
    """The automatic review of a plan, when a Claude API key is there; otherwise the rule-based titles stay."""
    why = review.unavailable(cfg)
    if why:
        print(f"automatic review skipped: {why}")
        return
    print(f"automatic review of the planned clips ({cfg.review.model})")
    review.apply(cfg, plan_path)


def cmd_review(cfg, args) -> int:
    plan_path = Path(args.plan) if args.plan else pipeline.latest_plan(cfg)
    if plan_path is None:
        print("no plan.json found; run `plan` first", file=sys.stderr)
        return 1
    why = review.unavailable(cfg)
    if why:
        print(f"automatic review not possible: {why}", file=sys.stderr)
        return 1
    result = review.apply(cfg, plan_path)
    return 1 if result["failed"] else 0


def cmd_run(cfg, args) -> int:
    ledger = pipeline.ledger_of(cfg)
    result = pipeline.plan(cfg, ledger)
    plan_path = cfg.path("out") / result["run"] / "plan.json"
    if result["clips"]:
        _review(cfg, plan_path)
    summary = pipeline.execute(cfg, ledger, plan_path)
    return _finish(cfg, ledger, summary)


def cmd_approve(cfg, args) -> int:
    voiceovers = dict(v.split("=", 1) for v in args.voiceover or [])
    ledger = pipeline.ledger_of(cfg)
    n = pipeline.approve(cfg, ledger, args.ids, voiceovers)
    report.write(cfg, ledger)
    print(f"{n} clip(s) scheduled")
    return 0


def cmd_status(cfg, args) -> int:
    ledger = pipeline.ledger_of(cfg)
    pipeline.refresh(cfg, ledger)
    print(f"report: {report.write(cfg, ledger)}")
    return 0


def cmd_ui(cfg, args) -> int:
    from . import ui

    ui.serve(args.config, args.port, not args.no_browser, args.token)
    return 0


def cmd_cleanup(cfg, args) -> int:
    pipeline.cleanup(cfg)
    return 0


def cmd_report(cfg, args) -> int:
    print(report.build(cfg, pipeline.ledger_of(cfg)))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="clipper", description=__doc__)
    parser.add_argument("--config", help="path to config.toml")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("setup", help="install what is needed and download the speech model (handled before this parser)")
    sub.add_parser("doctor", help="check that everything needed is in place").set_defaults(fn=cmd_doctor)
    p = sub.add_parser("clip", help="make clips from one video file (no posting)")
    p.add_argument("video")
    p.add_argument("--count", type=int, default=5)
    p.add_argument("--creator", default="")
    p.add_argument("--section", default="")
    p.add_argument("--out")
    p.set_defaults(fn=cmd_clip)
    sub.add_parser("plan", help="collect footage and plan today's clips").set_defaults(fn=cmd_plan)
    p = sub.add_parser("execute", help="render and schedule the planned clips")
    p.add_argument("--plan")
    p.set_defaults(fn=cmd_execute)
    p = sub.add_parser("review", help="have Claude check the planned clips and write their titles (needs a Claude API key)")
    p.add_argument("--plan")
    p.set_defaults(fn=cmd_review)
    sub.add_parser("run", help="plan, review, execute").set_defaults(fn=cmd_run)
    p = sub.add_parser("approve", help="schedule clips held for review")
    p.add_argument("ids", nargs="*")
    p.add_argument("--voiceover", action="append", metavar="CLIP_ID=FILE")
    p.set_defaults(fn=cmd_approve)
    sub.add_parser("status", help="refresh post links and views, rewrite the report").set_defaults(fn=cmd_status)
    sub.add_parser("cleanup", help="delete old downloads and old clips").set_defaults(fn=cmd_cleanup)
    p = sub.add_parser("ui", help="open the Clipper app in your browser")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--token", help=argparse.SUPPRESS)
    p.set_defaults(fn=cmd_ui)
    sub.add_parser("report", help="print the report").set_defaults(fn=cmd_report)
    args = parser.parse_args(argv)
    try:
        cfg = config.load(args.config)
    except config.ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    return args.fn(cfg, args)
