"""Clinician adjudication ledger for the golden set.

The red-team review's benchmark ask (#5): grow the golden set toward
100–200 dangerous-boundary cases, each adjudicated by **at least two**
thoracic-oncology clinicians independently, with disagreements preserved.
The clinical work is human; this module is the machinery that makes it
auditable:

* append-only JSONL, one adjudication event per line — and, unlike the
  KG curation ledger, **never last-wins**: every adjudicator's verdict on
  a case coexists, because a disagreement between two clinicians is the
  most valuable signal the ledger can hold, not a conflict to resolve;
* every event is content-hash-pinned to the exact case+expectations it
  judged — edit the case and every verdict on it is void until re-read;
* verdicts: ``agree`` (the case and its machine-checked expectations are
  clinically sound), ``disagree`` (notes required: what is wrong),
  ``needs_revision`` (notes required: what to change);
* the eval report carries coverage: how many cases hold two independent
  verdicts, where they agree, and exactly where they do not.

Identity is recorded, not authenticated — the honest list says so; the
ledger lives in git so review happens where code review happens.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

ADJUDICATION_ENV = "NSCLC_ADJUDICATION"
_DEFAULT_LEDGER = Path(__file__).resolve().parent / "golden" / "adjudications.jsonl"

VERDICTS = ("agree", "disagree", "needs_revision")


def default_ledger_path() -> Path:
    override = os.environ.get(ADJUDICATION_ENV, "").strip()
    return Path(override) if override else _DEFAULT_LEDGER


def case_content_hash(entry: dict[str, Any]) -> str:
    """Hash of exactly what an adjudicator judges: the case payload AND
    its machine-checked expectations (an audit entry's crafted plan
    included). Change either and prior verdicts are void."""
    judged = {k: entry.get(k) for k in
              ("case", "expect", "audit_plan", "audit_facts",
               "audit_staging") if k in entry}
    return hashlib.sha256(
        json.dumps(judged, sort_keys=True, ensure_ascii=False,
                   separators=(",", ":")).encode("utf-8")).hexdigest()


def load_adjudications(path: str | Path | None = None
                       ) -> dict[str, list[dict[str, Any]]]:
    """All events, grouped by case id — every one kept, none overwritten.
    Interior corruption raises; a torn final line is tolerated."""
    file_path = Path(path) if path else default_ledger_path()
    if not file_path.is_file():
        return {}
    grouped: dict[str, list[dict[str, Any]]] = {}
    lines = file_path.read_text(encoding="utf-8").splitlines()
    for line_no, line in enumerate(lines, start=1):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            if line_no == len(lines):
                break
            raise ValueError(
                f"corrupt adjudication ledger line {line_no}: {exc}") from exc
        if not isinstance(event, dict) or not event.get("case_id"):
            raise ValueError(
                f"adjudication ledger line {line_no}: not an event")
        grouped.setdefault(str(event["case_id"]), []).append(event)
    return grouped


def append_adjudication(
    path: str | Path | None,
    *,
    cases: list[dict[str, Any]],
    case_id: str,
    verdict: str,
    adjudicator: str,
    notes: str = "",
) -> dict[str, Any]:
    if verdict not in VERDICTS:
        raise ValueError(f"unknown verdict {verdict!r}; expected one of "
                         f"{VERDICTS}")
    if not adjudicator.strip():
        raise ValueError("an adjudication needs a named adjudicator")
    if verdict in ("disagree", "needs_revision") and not notes.strip():
        raise ValueError(f"a {verdict!r} verdict needs notes saying what "
                         f"is wrong / what to change")
    entry = next((c for c in cases if str(c.get("id")) == str(case_id)), None)
    if entry is None:
        raise ValueError(f"no golden case {case_id!r}")
    event = {
        "case_id": str(case_id),
        "verdict": verdict,
        "adjudicator": adjudicator.strip(),
        "adjudicated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "content_hash": case_content_hash(entry),
        "notes": notes.strip(),
    }
    target = Path(path) if path else default_ledger_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event


def adjudication_summary(
    cases: list[dict[str, Any]],
    path: str | Path | None = None,
) -> dict[str, Any]:
    """Coverage + agreement over the CURRENT golden set.

    Per case, only events whose hash still matches count (stale verdicts
    are listed as void, loudly). "Dual-adjudicated" means valid verdicts
    from at least two DISTINCT adjudicators; a disagreement is any case
    where distinct adjudicators returned different verdicts — preserved
    and named, never averaged away.
    """
    grouped = load_adjudications(path)
    per_case: dict[str, dict[str, Any]] = {}
    dual = agreements = 0
    disagreements: list[str] = []
    void_cases: list[str] = []
    for entry in cases:
        case_id = str(entry.get("id"))
        current_hash = case_content_hash(entry)
        events = grouped.get(case_id, [])
        valid = [e for e in events if e.get("content_hash") == current_hash]
        stale = len(events) - len(valid)
        by_adjudicator: dict[str, str] = {}
        for event in valid:  # a person's LATEST verdict on this content
            by_adjudicator[event["adjudicator"]] = event["verdict"]
        verdicts = sorted(set(by_adjudicator.values()))
        record = {
            "adjudicators": len(by_adjudicator),
            "verdicts": verdicts,
            "stale_events": stale,
        }
        if stale:
            void_cases.append(case_id)
        if len(by_adjudicator) >= 2:
            dual += 1
            if len(verdicts) == 1 and verdicts[0] == "agree":
                agreements += 1
            elif len(verdicts) > 1:
                disagreements.append(case_id)
        per_case[case_id] = record
    unadjudicated = sum(1 for r in per_case.values()
                        if r["adjudicators"] == 0)
    return {
        "cases": len(cases),
        "dual_adjudicated": dual,
        "dual_agreements": agreements,
        "disagreements": disagreements,
        "unadjudicated": unadjudicated,
        "void_after_case_change": void_cases,
        "per_case": per_case,
    }
