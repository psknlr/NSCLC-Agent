"""Command-line interface for the NSCLC agent harness.

Subcommands:
  stage      Deterministically stage a TNM triple (no model).
  route      Show which protocol module a stage group maps to.
  modules    List protocol modules with provenance (sha256, size).
  axes       List the enquiry axes (the VOI layer), by tier.
  screen     Run the oncologic-emergency screen on a narrative.
  run        Run one case through the full governed pipeline.
  batch      Run every ``*.json`` case in a directory.
  selftest   Validate the staging engine (stage table + refusal table).
  eval       Run the golden-case evaluation suite.
  llm-check  Show which model/vision backends are configured.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from . import __version__
from .case import Case
from .interview.axes import AXES
from .journal import Journal
from .llm.base import LLMError
from .llm.providers import build_client, build_vision_client, describe_client
from .prompts import list_modules
from .render import audit, render
from .runner import NSCLCRunner
from .safety import emergencies
from .staging import StagingError, route, stage_from_strings
from .staging.selftest import run_selftest


def _read_case(path: str) -> Case:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return Case.from_dict(data)


def cmd_stage(args) -> int:
    try:
        result = stage_from_strings(args.t, args.n, args.m, prefix=args.prefix)
    except StagingError as exc:
        print(f"Staging refused: {exc}", file=sys.stderr)
        return 1
    routed = route(result.stage_group)
    if args.json:
        payload = result.to_dict()
        payload["module"] = routed.to_dict()
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    print(f"TNM {result.tnm}  →  Stage {result.stage_group} ({result.edition})")
    for note in result.migration_notes:
        print(f"  • migration: {note}")
    for note in result.descriptor_notes:
        print(f"  • note: {note}")
    print(f"  → module: {routed.module_key}")
    return 0


def cmd_route(args) -> int:
    print(json.dumps(route(args.stage_group).to_dict(), ensure_ascii=False, indent=2))
    return 0


def cmd_modules(args) -> int:
    for module in list_modules():
        print(f"{module.key:8s}  {module.label}")
        print(f"{'':8s}  stages: {', '.join(module.stage_groups)}  |  "
              f"{len(module.full_text):,} chars, {len(module.sections)} sections"
              f"  |  sha256:{module.sha256[:12]}  |  "
              f"min_output_tokens:{module.min_output_tokens}")
    return 0


def cmd_axes(args) -> int:
    tier = args.tier.upper() if args.tier else None
    for axis in AXES:
        if tier and axis.tier != tier:
            continue
        print(f"[{axis.tier:9s}] {axis.axis_id:24s} {axis.label}")
        print(f"{'':12s}closes: {', '.join(axis.closes)}")
        print(f"{'':12s}resolves via: {axis.resolving_test}")
    return 0


def cmd_screen(args) -> int:
    result = emergencies.screen(args.narrative)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0 if not result.emergency else 2


def _build_runner(args, *, case_base_dir: Optional[Path] = None) -> NSCLCRunner:
    try:
        llm = build_client(getattr(args, "llm_provider", None) or None,
                           model=getattr(args, "llm_model", None) or None)
        vision = build_vision_client(
            provider=getattr(args, "vision_provider", None) or None)
    except LLMError as exc:
        print(f"LLM configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    journal = None
    journal_path = getattr(args, "journal", None)
    replay_path = getattr(args, "replay", None)
    if replay_path:
        journal = Journal.load(replay_path, mode="replay")
    elif journal_path:
        journal = Journal(journal_path, mode="record")
    return NSCLCRunner(
        llm=llm, vision_llm=vision, journal=journal,
        checkpoint_dir=getattr(args, "checkpoint_dir", None),
        panel_concurrency=getattr(args, "panel_concurrency", 4) or 4,
        case_base_dir=case_base_dir,
    )


def cmd_run(args) -> int:
    if args.case:
        case = _read_case(args.case)
        base_dir = Path(args.case).resolve().parent
        if args.images:
            case.images = list(args.images)
    else:
        facts = {}
        if args.facts:
            facts = json.loads(args.facts)
        elif args.facts_file:
            facts = json.loads(Path(args.facts_file).read_text(encoding="utf-8"))
        case = Case(
            t=args.t, n=args.n, m=args.m, tnm_prefix=args.prefix,
            stage_group=args.stage_group,
            presentation=args.presentation or "",
            question=args.question or "",
            images=list(args.images or []),
            facts=facts,
        )
        base_dir = Path.cwd()
    runner = _build_runner(args, case_base_dir=base_dir)
    state = runner.run_case(
        case, role=args.role,
        allow_dose_planning=args.allow_dose_planning,
        enable_panel=args.panel,
    )
    payload = audit(state) if args.debug_state else render(state, args.role)
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0 if state.release_status not in ("failed_closed",) else 3


def cmd_batch(args) -> int:
    in_dir = Path(args.directory)
    files = sorted(in_dir.glob("*.json"))
    if not files:
        print(f"No .json case files in {in_dir}", file=sys.stderr)
        return 1
    out_dir = Path(args.out) if args.out else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
    exit_code = 0
    for path in files:
        result_path = out_dir / f"{path.stem}.result.json" if out_dir else None
        if result_path and result_path.exists() and args.resume:
            print(f"{path.name}: skipped (result exists)")
            continue
        runner = _build_runner(args, case_base_dir=path.resolve().parent)
        state = runner.run_case(
            _read_case(str(path)), role=args.role,
            allow_dose_planning=args.allow_dose_planning,
            enable_panel=args.panel,
        )
        if state.release_status == "failed_closed":
            exit_code = 3
        line = (f"{path.name}: stage="
                f"{state.staging.get('stage_group', '?') if state.staging else '?'} "
                f"module={state.routing.get('module_key')} "
                f"status={state.release_status}")
        print(line)
        if result_path:
            result_path.write_text(
                json.dumps(audit(state), ensure_ascii=False, indent=2,
                           default=str),
                encoding="utf-8")
    return exit_code


def cmd_selftest(args) -> int:
    passed, total, failures = run_selftest()
    for failure in failures:
        print(f"FAIL: {failure}")
    print(f"Staging self-test: {passed}/{total} passed "
          f"(stage table + refusal table)")
    return 0 if passed == total else 1


def cmd_eval(args) -> int:
    from eval.run_eval import run_eval  # local package, repo layout

    report = run_eval(golden_dir=args.golden)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    if args.out:
        Path(args.out).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["summary"]["all_passed"] else 1


def cmd_llm_check(args) -> int:
    try:
        llm = build_client(args.llm_provider or None, model=args.llm_model or None)
        vision = build_vision_client(provider=args.vision_provider or None)
    except LLMError as exc:
        print(f"LLM configuration error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "llm": describe_client(llm),
        "vision": describe_client(vision) if vision else {"provider": "none"},
    }, ensure_ascii=False, indent=2))
    return 0


def _add_llm_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--llm-provider", help="azure|poe|minimax|litellm|mock")
    parser.add_argument("--llm-model")
    parser.add_argument("--vision-provider",
                        help="film-reading backend (mock|poe|azure|…)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nsclc-agent",
        description="Evidence-governed, stage-verified NSCLC agent harness "
                    "(educational/research use only).",
    )
    parser.add_argument("--version", action="version",
                        version=f"nsclc-agent {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("stage", help="Deterministically stage a TNM triple")
    p.add_argument("t"); p.add_argument("n"); p.add_argument("m")
    p.add_argument("--prefix", default="c", help="c|p|yp (default c)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_stage)

    p = sub.add_parser("route", help="Show the module for a stage group")
    p.add_argument("stage_group")
    p.set_defaults(func=cmd_route)

    p = sub.add_parser("modules", help="List protocol modules")
    p.set_defaults(func=cmd_modules)

    p = sub.add_parser("axes", help="List enquiry axes (the VOI layer)")
    p.add_argument("--tier", help="RED_FLAG|STAGING|BIOMARKER|FITNESS|CONTEXT")
    p.set_defaults(func=cmd_axes)

    p = sub.add_parser("screen", help="Oncologic-emergency screen a narrative")
    p.add_argument("narrative")
    p.set_defaults(func=cmd_screen)

    p = sub.add_parser("run", help="Run one case through the governed pipeline")
    p.add_argument("--case", help="Path to a JSON case file")
    p.add_argument("--t"); p.add_argument("--n"); p.add_argument("--m")
    p.add_argument("--prefix", default="c")
    p.add_argument("--stage-group", dest="stage_group")
    p.add_argument("--presentation"); p.add_argument("--question")
    p.add_argument("--facts", help="Structured facts as inline JSON "
                   "(driver_mutations, pd_l1, ecog_ps, comorbidities…)")
    p.add_argument("--facts-file", dest="facts_file",
                   help="Structured facts from a JSON file")
    p.add_argument("--images", nargs="+")
    p.add_argument("--role", default="oncologist",
                   choices=("patient", "oncologist", "researcher"))
    p.add_argument("--allow-dose-planning", action="store_true")
    p.add_argument("--panel", action="store_true",
                   help="Convene the MDT panel")
    p.add_argument("--panel-concurrency", type=int, default=4)
    p.add_argument("--journal", help="Record every host call to this JSONL")
    p.add_argument("--replay", help="Replay a recorded journal offline")
    p.add_argument("--checkpoint-dir")
    p.add_argument("--debug-state", action="store_true",
                   help="Print the full internal state (operator-only)")
    _add_llm_flags(p)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("batch", help="Run all case files in a directory")
    p.add_argument("directory")
    p.add_argument("-o", "--out")
    p.add_argument("--resume", action="store_true",
                   help="Skip cases whose result file already exists")
    p.add_argument("--role", default="oncologist",
                   choices=("patient", "oncologist", "researcher"))
    p.add_argument("--allow-dose-planning", action="store_true")
    p.add_argument("--panel", action="store_true")
    p.add_argument("--panel-concurrency", type=int, default=4)
    _add_llm_flags(p)
    p.set_defaults(func=cmd_batch)

    p = sub.add_parser("selftest", help="Validate the staging engine")
    p.set_defaults(func=cmd_selftest)

    p = sub.add_parser("eval", help="Run the golden-case evaluation")
    p.add_argument("--golden", help="Golden case directory")
    p.add_argument("--out", help="Write the full report JSON here")
    p.set_defaults(func=cmd_eval)

    p = sub.add_parser("llm-check", help="Show configured backends")
    _add_llm_flags(p)
    p.set_defaults(func=cmd_llm_check)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
