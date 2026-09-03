"""The MDT panel: independent speciality subagents, conservatively merged.

The value of a consult is *disagreement*, so synthesis is most-conservative
rather than majority vote: urgency is the maximum over members, concerns are
the union, and dissent is surfaced verbatim — one surgeon seeing an
unresectable case outweighs three who did not look. The cost asymmetry runs
one way.

Containment:

* No member reaches a dose-channel tool — the panel skill's grant excludes
  them and the broker enforces it independently.
* Members run on carved budget slices that charge back to the parent, so a
  chatty member cannot starve the run.
* Members may run concurrently, but each writes into its own
  :class:`MemberScope`; scopes merge into the ledger in **convened order**, so
  evidence ids depend on the roster, not on which thread finished first — the
  audit trail is reproducible under any concurrency.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from ..state import CaseRunState, EvidenceLevel
from .toolloop import ToolLoop, schema_hint

PANEL_URGENCY_ORDER = {"routine": 0, "expedited": 1, "urgent": 2}

PERSONAS: tuple[tuple[str, str], ...] = (
    ("thoracic_surgeon",
     "You are the THORACIC SURGEON. Judge technical resectability (R0 "
     "achievability, extent of resection, N2 burden — single- vs multi-station "
     "matters), operability (PS, ppoFEV1/ppoDLCO), and whether invasive "
     "mediastinal staging is adequate before anyone commits. N3 is not "
     "surgical; say so plainly when relevant."),
    ("radiation_oncologist",
     "You are the RADIATION ONCOLOGIST. Judge definitive-RT candidacy: "
     "encompassable volume, dose (60 Gy/30 fx standard — never escalate to "
     "74 Gy), concurrent vs sequential chemo, SBRT for inoperable early stage, "
     "and consolidation timing after cCRT."),
    ("medical_oncologist",
     "You are the MEDICAL ONCOLOGIST. Judge systemic strategy: Tier-A "
     "biomarkers before commitment, driver-directed vs chemo-IO, perioperative "
     "vs adjuvant sequencing, trial stage boundaries, and consolidation agent "
     "by driver status (LAURA vs PACIFIC)."),
    ("pulmonologist",
     "You are the INTERVENTIONAL PULMONOLOGIST. Judge tissue and nodal-staging "
     "adequacy: is there a histologic diagnosis, is EBUS station mapping "
     "complete enough to trust the N descriptor, does the airway need "
     "attention, is pulmonary reserve characterised?"),
    ("palliative_care",
     "You are the PALLIATIVE CARE physician. Judge symptom burden, goals-of-"
     "care alignment, treatment-burden honesty for PS≥2 and polymetastatic "
     "disease, and whether early palliative integration is being offered as "
     "the evidence says it should be."),
)

PERSONA_TEMPLATE = """{persona}

You are one member of an NSCLC multidisciplinary panel. Run context:
audience={role}, risk mode={risk_mode}.

## The authoritative stage (computed deterministically — never re-derive it)
{staging_block}

## Hard constraints (identical for every panel member)
1. NEVER write dose numerics. You have no dose tools and no prescription role.
2. Cite the evidence_id of every tool result you rely on in `citations`.
3. You may RAISE urgency and ADD concerns; synthesis is conservative — your
   dissent will be preserved verbatim, so state it plainly.
4. Use only your granted tools; gather evidence before concluding.

## Output
When done, output ONLY a JSON object:
{schema_hint}
`urgency` is one of routine | expedited | urgent."""


class MemberScope:
    """A private evidence sink for one member; merged in convened order."""

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def add_evidence(self, level: str, source: str, summary: str,
                     payload: dict[str, Any] | None = None, *,
                     ok: bool = True, error: str | None = None,
                     source_version: str | None = None) -> str:
        # Temporary id, remapped at merge time.
        temp_id = f"TMP{len(self.records) + 1:03d}"
        self.records.append({
            "temp_id": temp_id, "level": level, "source": source,
            "summary": summary, "payload": payload or {}, "ok": ok,
            "error": error, "source_version": source_version,
        })
        return temp_id


@dataclass
class MemberResult:
    persona_id: str
    ok: bool = False
    opinion: dict[str, Any] | None = None
    mode: str = "not_run"
    scope: MemberScope = field(default_factory=MemberScope)
    citations: list[str] = field(default_factory=list)


class PanelAgent:
    """Convenes the panel, merges scopes deterministically, synthesises."""

    skill_id = "nsclc.panel"

    def __init__(self, llm: Any | None = None, *, concurrency: int = 4) -> None:
        self.llm = llm
        self.concurrency = max(1, min(int(concurrency), 8))

    _UNSET = object()

    def run(self, state: CaseRunState, tools: Any, broker_factory: Any, *,
            treatment_plan_context: Any = _UNSET) -> None:
        staging_block = _staging_block(state)
        # In a parallel wave the runner passes an explicit None: the panel
        # reviews independently, and must not read the shared outputs dict
        # that the treatment loop is writing mid-wave (a race that would also
        # leak unmapped wave temp ids into member prompts).
        if treatment_plan_context is self._UNSET:
            treatment_plan_context = state.outputs.get("treatment_plan")
        context = {
            "complaint": state.complaint[:3000],
            "facts": {k: v for k, v in state.facts.items()
                      if k not in ("tumor_board_review",)},
            "staging": state.staging,
            "treatment_plan_so_far": treatment_plan_context,
        }

        def convene(persona: tuple[str, str]) -> MemberResult:
            persona_id, persona_text = persona
            result = MemberResult(persona_id)
            loop = ToolLoop(
                self.llm, tools, broker_factory(self.skill_id), state,
                agent_name=f"Panel:{persona_id}", skill_id=self.skill_id,
                skill_spec=broker_factory.skill_spec(self.skill_id),
                max_steps=4, max_tokens=2000,
                staging_block=staging_block,
                persona_prompt=PERSONA_TEMPLATE.format(
                    persona=persona_text, role=state.role,
                    risk_mode=state.risk_mode, staging_block=staging_block,
                    schema_hint=schema_hint("PanelOpinion")),
                evidence_scope=result.scope,
            )
            outcome = loop.run(
                f"Give your {persona_id} assessment of this case.",
                context, "PanelOpinion",
            )
            result.ok = outcome.ok
            result.opinion = outcome.output
            result.mode = outcome.mode
            result.citations = outcome.citations
            return result

        if self.llm is not None and getattr(self.llm, "available", False):
            if self.concurrency > 1:
                with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
                    results = list(pool.map(convene, PERSONAS))
            else:
                results = [convene(p) for p in PERSONAS]
        else:
            results = [MemberResult(pid, mode="llm_unavailable")
                       for pid, _ in PERSONAS]

        # Merge scopes in CONVENED order — ids depend on the roster, not on
        # thread completion order.
        for result in results:
            id_map: dict[str, str] = {}
            for record in result.scope.records:
                eid = state.add_evidence(
                    record["level"], f"panel:{result.persona_id}:{record['source']}",
                    record["summary"], record["payload"], ok=record["ok"],
                    error=record["error"], source_version=record["source_version"],
                )
                id_map[record["temp_id"]] = eid
            result.citations = [id_map.get(c, c) for c in result.citations]

        state.outputs["panel"] = self._synthesise(state, results)
        state.trace("PanelAgent", "convene",
                    output_summary=f"{sum(1 for r in results if r.ok)}/{len(results)} members answered")

    # ------------------------------------------------------------- synthesis
    def _synthesise(self, state: CaseRunState, results: list[MemberResult]) -> dict[str, Any]:
        answered = [r for r in results if r.ok and isinstance(r.opinion, dict)]
        urgency = "routine"
        concerns: list[str] = []
        recommends: list[str] = []
        disagreements: list[str] = []
        members: list[dict[str, Any]] = []
        for result in results:
            opinion = result.opinion or {}
            members.append({
                "persona": result.persona_id,
                "answered": result.ok,
                "mode": result.mode,
                "opinion": opinion,
                "citations": result.citations,
            })
            if not result.ok:
                continue
            member_urgency = str(opinion.get("urgency") or "routine")
            if PANEL_URGENCY_ORDER.get(member_urgency, 0) > PANEL_URGENCY_ORDER.get(urgency, 0):
                # Conservative merge: one member's urgent outweighs the rest.
                urgency = member_urgency
            for concern in opinion.get("concerns") or []:
                if str(concern) not in concerns:
                    concerns.append(str(concern))
            for rec in opinion.get("recommend_next") or []:
                if str(rec) not in recommends:
                    recommends.append(str(rec))
            dissent = str(opinion.get("dissent") or "").strip()
            if dissent:
                disagreements.append(f"{result.persona_id}: {dissent}")
        synthesis = {
            "urgency": urgency,
            "consensus": recommends,
            "concerns": concerns,
            "disagreements": disagreements,
            "members": members,
            "answered": len(answered),
            "convened": len(results),
        }
        if answered:
            eid = state.add_evidence(
                EvidenceLevel.MODEL, "panel_synthesis",
                f"MDT panel: urgency={urgency}, {len(disagreements)} dissent(s)",
                {"urgency": urgency, "disagreements": disagreements},
            )
            synthesis["evidence_id"] = eid
        else:
            state.warn("panel convened but no member produced a valid opinion")
        return synthesis


def _staging_block(state: CaseRunState) -> str:
    if not state.staging:
        return "(not staged)"
    return (
        f"TNM: {state.staging.get('tnm')}  →  Stage "
        f"{state.staging.get('stage_group')} ({state.staging.get('edition')})"
    )
