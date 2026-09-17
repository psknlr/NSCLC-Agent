"""Golden-case evaluation: the decision-quality yardstick v0.1 never had.

Each golden case pairs a case payload with machine-checkable expectations —
stage group, module, regimens that must / must not appear, trial anchors,
release status, safety-rule silence. The suite runs fully offline against the
deterministic pipeline; point it at a real model (via NSCLC_LLM_PROVIDER) and
the same expectations grade the model-driven plan.

Metrics reported: staging accuracy, routing accuracy, regimen-selection
accuracy, safety-violation rate, release-status accuracy.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import Case, NSCLCRunner
from ..llm.providers import build_client

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"


def _check(state: Any, expect: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    plan = state.outputs.get("treatment_plan") or {}
    regimen_ids = set(plan.get("regimen_ids") or [])
    trial_refs = set(plan.get("trial_refs") or [])
    plan_blob = json.dumps(plan, ensure_ascii=False).lower()
    staging = state.staging or {}

    if "stage_group" in expect:
        got = staging.get("stage_group")
        if got != expect["stage_group"]:
            failures.append(f"stage_group: got {got!r}, want {expect['stage_group']!r}")
    if "module" in expect:
        got = state.routing.get("module_key")
        if got != expect["module"]:
            failures.append(f"module: got {got!r}, want {expect['module']!r}")
    if "migration_contains" in expect:
        notes = " ".join(staging.get("migration_notes") or [])
        if expect["migration_contains"].lower() not in notes.lower():
            failures.append(f"migration note missing {expect['migration_contains']!r}")
    for key in ("regimens_any", "regimens_any_2"):
        if key in expect and not (set(expect[key]) & regimen_ids):
            failures.append(f"none of {expect[key]} in regimens {sorted(regimen_ids)}")
    if "regimens_none" in expect:
        hit = set(expect["regimens_none"]) & regimen_ids
        if hit:
            failures.append(f"forbidden regimen(s) present: {sorted(hit)}")
    if "trial_refs_any" in expect and not (set(expect["trial_refs_any"]) & trial_refs):
        failures.append(f"none of {expect['trial_refs_any']} in trial_refs {sorted(trial_refs)}")
    if "release_status" in expect and state.release_status not in expect["release_status"]:
        failures.append(f"release_status: got {state.release_status!r}, "
                        f"want one of {expect['release_status']}")
    if "risk_mode" in expect and state.risk_mode != expect["risk_mode"]:
        failures.append(f"risk_mode: got {state.risk_mode!r}")
    if "plan_intent" in expect and plan.get("intent") != expect["plan_intent"]:
        failures.append(f"plan intent: got {plan.get('intent')!r}")
    if "flag_contains" in expect and not any(
        expect["flag_contains"] in f for f in state.flags
    ):
        failures.append(f"no flag containing {expect['flag_contains']!r}")
    if "workup_mentions" in expect:
        workup = json.dumps(state.outputs.get("workup_plan") or {},
                            ensure_ascii=False).lower()
        if expect["workup_mentions"].lower() not in workup:
            failures.append(f"workup plan missing {expect['workup_mentions']!r}")
    for needle in expect.get("plan_must_not_mention") or []:
        if needle.lower() in plan_blob:
            failures.append(f"plan mentions forbidden {needle!r}")
    for rule_id in expect.get("no_violations") or []:
        violations = (state.outputs.get("safety_audit") or {}).get("violations") or []
        if any(v.get("rule_id") == rule_id for v in violations):
            failures.append(f"safety rule fired: {rule_id}")
    return failures


def run_eval(*, golden_dir: str | Path | None = None,
             llm_provider: str | None = None) -> dict[str, Any]:
    payload = json.loads(
        (Path(golden_dir) if golden_dir else GOLDEN_DIR).joinpath("cases.json")
        .read_text(encoding="utf-8"))
    llm = build_client(llm_provider) if llm_provider else None
    results: list[dict[str, Any]] = []
    metrics = {"staging_correct": 0, "staging_total": 0,
               "routing_correct": 0, "routing_total": 0,
               "regimen_correct": 0, "regimen_total": 0,
               "safety_clean": 0, "safety_total": 0}
    for entry in payload["cases"]:
        runner = NSCLCRunner(llm=llm)  # fresh runner: no cross-case breaker state
        state = runner.run_case(Case.from_dict(entry["case"]))
        failures = _check(state, entry["expect"])
        expect = entry["expect"]
        if "stage_group" in expect:
            metrics["staging_total"] += 1
            if not any(f.startswith("stage_group") for f in failures):
                metrics["staging_correct"] += 1
        if "module" in expect:
            metrics["routing_total"] += 1
            if not any(f.startswith("module") for f in failures):
                metrics["routing_correct"] += 1
        if "regimens_any" in expect or "regimens_none" in expect:
            metrics["regimen_total"] += 1
            if not any("regimen" in f for f in failures):
                metrics["regimen_correct"] += 1
        blocking = [
            v for v in (state.outputs.get("safety_audit") or {}).get("violations") or []
            if v.get("severity") == "block"
        ]
        metrics["safety_total"] += 1
        if not blocking:
            metrics["safety_clean"] += 1
        results.append({
            "id": entry["id"],
            "passed": not failures,
            "failures": failures,
            "stage_group": (state.staging or {}).get("stage_group"),
            "release_status": state.release_status,
            "regimen_ids": (state.outputs.get("treatment_plan") or {}).get("regimen_ids"),
        })
    passed = sum(1 for r in results if r["passed"])
    return {
        "summary": {
            "passed": passed,
            "total": len(results),
            "all_passed": passed == len(results),
            "staging_accuracy": _ratio(metrics, "staging"),
            "routing_accuracy": _ratio(metrics, "routing"),
            "regimen_accuracy": _ratio(metrics, "regimen"),
            "safety_clean_rate": (
                f"{metrics['safety_clean']}/{metrics['safety_total']}"),
            "failed_ids": [r["id"] for r in results if not r["passed"]],
        },
        "results": results,
    }


def _ratio(metrics: dict[str, int], prefix: str) -> str:
    total = metrics[f"{prefix}_total"]
    key = "safety_clean" if prefix == "safety" else f"{prefix}_correct"
    return f"{metrics[key]}/{total}" if total else "n/a"


if __name__ == "__main__":
    report = run_eval()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["summary"]["all_passed"] else 1)
