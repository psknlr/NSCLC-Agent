"""Model-driven tool-calling loop, contained by the control plane.

This is what makes execution autonomous rather than merely templated: the
model sees the tool schemas its *skill* permits, chooses which to call and
with what arguments, reads the observations, and decides when it has enough
to answer. The containment is what makes that acceptable clinically:

* **Only the skill's tools are visible.** Schemas are filtered to
  ``allowed_tools − forbidden_tools`` — the model cannot even name a tool it
  may not use, and every call still passes the broker.
* **Observations are evidence.** Each tool result lands in the ledger at the
  grade the tool declares, and its evidence id goes back to the model so the
  answer can cite it.
* **The output is a contract.** The final message must validate against the
  skill's schema; a formatting miss gets ONE repair turn (the evidence already
  gathered should not be discarded over a missing brace), a safety miss gets
  none.
* **No doses, ever.** A model output carrying a dose numeric is discarded
  without a retry — re-prompting a dose leak invites a reworded dose leak.
* **Truncation is a failure, not a result.** ``finish_reason == "length"`` is
  surfaced as ``output_truncated``; it can never be mistaken for an answer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .. import schemas
from ..llm.base import LLMError, ToolSpec
from ..safety.rules import DOSE_RE
from ..state import CaseRunState

MAX_STEPS = 8
MAX_OBSERVATION_CHARS = 6000
MAX_REPAIRS = 1

SYSTEM_TEMPLATE = """You are **{agent}** in an NSCLC clinical decision-support system.

Run context: audience={role}, risk mode={risk_mode}.

## Your skill: {skill_id}
{skill_description}

{skill_instructions}

## The authoritative stage
{staging_block}

## Available tools
You may only call the tools listed; the system re-checks authorisation before
execution. Gather evidence through tools BEFORE answering — do not assert from
memory what a tool can verify.

## Hard constraints
1. **Never write dose numerics** (mg, mg/m², AUC, Gy…). Doses are attached by
   a separate deterministic channel; you reference regimens by regimen_id.
2. Every consequential claim must cite the `evidence_id` values returned by
   your tool calls, in the `citations` field.
3. Never re-derive or override the stage group above.
4. State uncertainty honestly; do not fill gaps with speculation.

## Output format
When you have gathered enough evidence, STOP calling tools and output a single
JSON object (no code fences) with this shape:
{schema_hint}

`citations` is the array of evidence_id strings you relied on."""


@dataclass
class LoopStep:
    step: int
    kind: str  # tool_call | final | error
    tool: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    ok: bool = True
    summary: str = ""
    evidence_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step, "kind": self.kind, "tool": self.tool,
            "arguments": self.arguments, "ok": self.ok,
            "summary": self.summary, "evidence_id": self.evidence_id,
        }


@dataclass
class ToolLoopResult:
    ok: bool = False
    output: dict[str, Any] | None = None
    steps: list[LoopStep] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)
    mode: str = "not_run"
    error: str = ""
    repairs: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok, "mode": self.mode, "error": self.error,
            "steps": [s.to_dict() for s in self.steps],
            "evidence_ids": self.evidence_ids, "citations": self.citations,
            "repairs": self.repairs,
        }


def serialise_observation(observation: dict[str, Any]) -> str:
    """Serialise a tool observation, shrinking it *structurally* if large.

    Slicing the JSON string would hand the model malformed JSON; bulky
    payloads are summarised into counts and previews instead.
    """
    text = json.dumps(observation, ensure_ascii=False)
    if len(text) <= MAX_OBSERVATION_CHARS:
        return text
    trimmed = dict(observation)
    trimmed["data"] = _shrink(observation.get("data"), MAX_OBSERVATION_CHARS // 2)
    trimmed["_truncated"] = "large result summarised structurally"
    text = json.dumps(trimmed, ensure_ascii=False)
    if len(text) <= MAX_OBSERVATION_CHARS:
        return text
    return json.dumps({
        "evidence_id": observation.get("evidence_id"),
        "ok": observation.get("ok", True),
        "summary": str(observation.get("summary", ""))[:400],
        "_truncated": "result too large; narrow the query for detail",
    }, ensure_ascii=False)


def _shrink(value: Any, budget: int) -> Any:
    if isinstance(value, list):
        preview = [_shrink(v, budget // 4) for v in value[:2]]
        return {"count": len(value), "preview": preview} if len(value) > 2 else preview
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if len(json.dumps(out, ensure_ascii=False)) > budget:
                out["_omitted"] = "more fields omitted"
                break
            out[key] = _shrink(item, budget // 2)
        return out
    if isinstance(value, str) and len(value) > 400:
        return value[:400] + "…"
    return value


def schema_hint(schema_name: str) -> str:
    schema = schemas.SCHEMAS.get(schema_name) or {}
    fields = {
        name: (("<required " if required else "<optional ")
               + "/".join(t.__name__ for t in types) + ">")
        for name, (required, types) in schema.items()
    }
    fields["citations"] = "<list[str] of evidence_id>"
    return json.dumps(fields, ensure_ascii=False, indent=2)


class ToolLoop:
    """Runs one agent's turn as a bounded ReAct loop."""

    #: Failure modes worth one more turn. ``dose_in_output`` is deliberately
    #: absent; ``output_truncated`` too — a bigger answer needs a bigger
    #: ceiling, not a retry at the same one.
    REPAIRABLE = ("invalid_output", "schema_violation")

    def __init__(
        self,
        llm: Any,
        tools: Any,
        broker: Any,
        state: CaseRunState,
        *,
        agent_name: str,
        skill_id: str,
        skill_spec: Any | None = None,
        max_steps: int = MAX_STEPS,
        max_repairs: int = MAX_REPAIRS,
        max_tokens: int = 6000,
        staging_block: str = "",
        persona_prompt: str = "",
        evidence_scope: Any | None = None,
    ) -> None:
        self.llm = llm
        self.tools = tools
        self.broker = broker
        self.state = state
        self.agent_name = agent_name
        self.skill_id = skill_id
        self.skill_spec = skill_spec
        self.max_steps = max_steps
        self.max_repairs = max_repairs
        self.max_tokens = max_tokens
        self.staging_block = staging_block
        #: Optional full replacement system prompt (the MDT panel installs one
        #: per member persona). The safety clauses live inside the persona
        #: template itself so a replacement cannot drop them accidentally.
        self.persona_prompt = persona_prompt
        #: Where evidence is recorded. Defaults to the shared state; the panel
        #: passes a MemberScope so concurrent members do not interleave ids.
        self.evidence_scope = evidence_scope if evidence_scope is not None else state

    @property
    def available(self) -> bool:
        return self.llm is not None and bool(getattr(self.llm, "available", False))

    def allowed_tool_specs(self) -> list[ToolSpec]:
        from ..tools import tool_specs

        if self.skill_spec is None:
            return []
        allowed = self.skill_spec.tool_names()
        return [spec for spec in tool_specs() if spec.name in allowed]

    # ------------------------------------------------------------------- run
    def run(self, objective: str, context: dict[str, Any], schema_name: str) -> ToolLoopResult:
        result = ToolLoopResult()
        specs = self.allowed_tool_specs()
        if not self.available:
            result.mode, result.error = "llm_unavailable", "no model configured"
            return result
        if not specs:
            result.mode, result.error = "no_tools_for_skill", f"{self.skill_id} grants no tools"
            return result

        messages = [
            {"role": "system", "content": self._system_prompt(schema_name)},
            {"role": "user", "content": json.dumps(
                {"objective": objective, **context}, ensure_ascii=False)[:16000]},
        ]

        for step in range(1, self.max_steps + 1):
            if not self.state.budget.reserve_llm():
                result.mode, result.error = "llm_budget_exhausted", "LLM budget exhausted"
                return self._finish(result)
            try:
                response = self.llm.chat(
                    messages, tools=specs, temperature=0.0,
                    max_tokens=self.max_tokens,
                )
            except (LLMError, Exception) as exc:  # noqa: BLE001 - never break the run
                result.steps.append(LoopStep(
                    step, "error", summary=f"{type(exc).__name__}: {exc}"[:200], ok=False))
                result.mode, result.error = "llm_error", type(exc).__name__
                return self._finish(result)
            self.state.budget.charge_llm_tokens(response.total_tokens)

            if not response.tool_calls:
                if response.truncated:
                    result.steps.append(LoopStep(
                        step, "final", ok=False, summary="reply truncated at max_tokens"))
                    result.mode = "output_truncated"
                    result.error = (
                        f"final message hit the {self.max_tokens}-token ceiling "
                        f"— raise max_tokens; a truncated plan is not a plan"
                    )
                    return self._finish(result)
                finalized = self._finalize(result, response.text, schema_name, step)
                if finalized.ok or not self._may_repair(finalized, step):
                    return finalized
                # One bounded repair turn: a formatting miss should not discard
                # the evidence the model already gathered.
                messages.append({"role": "assistant", "content": response.text or ""})
                messages.append({"role": "user",
                                 "content": self._repair_prompt(finalized, schema_name)})
                result.repairs += 1
                continue

            messages.append(self._assistant_message(response))
            for call in response.tool_calls:
                observation, loop_step = self._execute(call, step)
                result.steps.append(loop_step)
                if loop_step.evidence_id:
                    result.evidence_ids.append(loop_step.evidence_id)
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id or f"call_{step}",
                    "name": call.name,
                    "content": serialise_observation(observation),
                })

        result.mode = "max_steps_exceeded"
        result.error = f"no conclusion within {self.max_steps} steps"
        return self._finish(result)

    # ---------------------------------------------------------------- repair
    def _may_repair(self, result: ToolLoopResult, step: int) -> bool:
        return (
            result.mode in self.REPAIRABLE
            and result.repairs < self.max_repairs
            and step < self.max_steps
        )

    def _repair_prompt(self, result: ToolLoopResult, schema_name: str) -> str:
        problem = (
            "Your previous reply was not a JSON object."
            if result.mode == "invalid_output"
            else f"Your previous reply violated the output contract: {result.error}"
        )
        return (
            f"{problem}\n\nOutput ONLY one JSON object — no code fences, no "
            f"prose before or after. The evidence you already gathered stands; "
            f"just structure it into these fields:\n{schema_hint(schema_name)}"
        )

    # --------------------------------------------------------------- helpers
    def _system_prompt(self, schema_name: str) -> str:
        if self.persona_prompt:
            return self.persona_prompt
        spec = self.skill_spec
        return SYSTEM_TEMPLATE.format(
            agent=self.agent_name,
            role=self.state.role,
            risk_mode=self.state.risk_mode,
            skill_id=self.skill_id,
            skill_description=getattr(spec, "description", "") or "(no description)",
            skill_instructions=getattr(spec, "instructions", "") or "",
            staging_block=self.staging_block or "(not yet staged)",
            schema_hint=schema_hint(schema_name),
        )

    @staticmethod
    def _assistant_message(response: Any) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": response.text or None,
            "tool_calls": [
                {
                    "id": call.id or f"call_{index}",
                    "type": "function",
                    "function": {"name": call.name,
                                 "arguments": json.dumps(call.arguments, ensure_ascii=False)},
                }
                for index, call in enumerate(response.tool_calls)
            ],
        }

    def _execute(self, call: Any, step: int) -> tuple[dict[str, Any], LoopStep]:
        from ..tools import TOOL_NAMES

        arguments = call.arguments if isinstance(call.arguments, dict) else {}
        if call.name not in TOOL_NAMES:
            observation = {
                "error": f"unknown tool {call.name!r}",
                "available": sorted(s.name for s in self.allowed_tool_specs()),
            }
            return observation, LoopStep(step, "tool_call", call.name, arguments,
                                         False, "unknown_tool")

        result = self.tools.call(self.broker, call.name, **arguments)
        if result.recoverable:
            # The model called a real tool the wrong way — a prompting problem,
            # not a clinical event. Return the schema so it can retry; keep it
            # out of the ledger entirely.
            spec = next(
                (s for s in self.allowed_tool_specs() if s.name == call.name), None)
            observation = {
                "ok": False, "recoverable": True,
                "error": result.error or result.summary,
                "hint": "arguments were invalid; retry per the parameters schema",
                "parameters": spec.parameters if spec else {},
            }
            return observation, LoopStep(step, "tool_call", call.name, arguments,
                                         False, f"recoverable: {result.summary[:120]}")

        evidence_id = self.evidence_scope.add_evidence(
            result.resolved_level(), call.name, result.summary,
            payload=result.data, ok=result.ok, error=result.error,
            source_version=result.source_version,
        )
        observation = {
            "evidence_id": evidence_id,
            "ok": result.ok,
            "summary": result.summary,
            "evidence_level": result.resolved_level(),
            "data": result.data,
        }
        if not result.ok:
            observation["error"] = result.error or result.summary
        return observation, LoopStep(step, "tool_call", call.name, arguments,
                                     result.ok, result.summary[:160], evidence_id)

    def _finalize(self, result: ToolLoopResult, text: str,
                  schema_name: str, step: int) -> ToolLoopResult:
        from ..llm.base import extract_json

        payload = extract_json(text, None)
        if not isinstance(payload, dict):
            result.steps.append(LoopStep(step, "final", ok=False,
                                         summary="final message was not a JSON object"))
            result.mode, result.error = "invalid_output", "final message was not a JSON object"
            return self._finish(result)

        citations = [c for c in (payload.pop("citations", None) or [])
                     if isinstance(c, str)]
        known = set(result.evidence_ids)
        result.citations = [c for c in citations if c in known]

        ok, problems = schemas.validate(schema_name, payload)
        if not ok:
            result.steps.append(LoopStep(step, "final", ok=False,
                                         summary="; ".join(problems)[:200]))
            result.mode, result.error = "schema_violation", "; ".join(problems)
            return self._finish(result)

        if DOSE_RE.search(json.dumps(payload, ensure_ascii=False)):
            result.steps.append(LoopStep(step, "final", ok=False,
                                         summary="dose numeric in model output"))
            result.mode, result.error = "dose_in_output", "model emitted a dose value"
            return self._finish(result)

        result.output = payload
        result.ok = True
        result.mode = "llm_tool_loop"
        result.steps.append(LoopStep(
            step, "final", ok=True,
            summary=f"answered after {len(result.evidence_ids)} evidence call(s)"))
        return result

    def _finish(self, result: ToolLoopResult) -> ToolLoopResult:
        if result.mode != "llm_tool_loop":
            self.state.warn(
                f"{self.agent_name}: autonomous execution unsuccessful "
                f"({result.mode}) — deterministic fallback used"
            )
        return result
