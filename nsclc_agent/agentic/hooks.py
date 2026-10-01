"""Hooks: deterministic code that runs at fixed points of the agent loop.

Same idea as Claude Code's hooks (UserPromptSubmit / PostToolUse / Stop):
the harness observes and comments at well-defined moments, and the user
decides which hooks are on. In NSCLC-Agent every clinical safety net lives
here, and every hook is ADVISORY — it adds context for the model or raises
findings the model must weigh; none of them blocks, rewrites or withholds.

* ``user_prompt_submit`` — before the model sees a message
  (emergency screen, case-note seeding);
* ``post_tool_use`` — after each tool call
  (evidence ledger: what the agent actually looked up);
* ``stop`` — when the agent submits its consult
  (rule engine, stage consistency, citation provenance, dose provenance,
  emergency addressed). Unanswered findings go back to the model once.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

EVENTS = ("user_prompt_submit", "post_tool_use", "stop")


@dataclass
class HookResult:
    context: str | None = None
    alert: dict[str, Any] | None = None
    findings: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Hook:
    name: str
    event: str
    title: str
    description: str
    fn: Callable[[dict[str, Any]], HookResult | None]
    default: bool = True

    def to_dict(self, enabled: bool) -> dict[str, Any]:
        return {"name": self.name, "event": self.event, "title": self.title,
                "description": self.description, "enabled": enabled}


class HookRunner:
    def __init__(self, hooks: list[Hook], enabled: dict[str, bool] | None = None) -> None:
        self.hooks = hooks
        self.enabled = {h.name: bool((enabled or {}).get(h.name, h.default)) for h in hooks}

    def run(self, event: str, ctx: dict[str, Any]) -> list[tuple[Hook, HookResult]]:
        out = []
        for hook in self.hooks:
            if hook.event != event or not self.enabled.get(hook.name):
                continue
            try:
                result = hook.fn(ctx)
            except Exception as exc:  # noqa: BLE001 - a broken hook is reported, not fatal
                result = HookResult(findings=[{"rule_id": f"HOOK_ERROR:{hook.name}",
                                               "severity": "warn",
                                               "message": f"{type(exc).__name__}: {exc}"}]
                                    if event == "stop" else [])
            if result is not None:
                out.append((hook, result))
        return out

    def describe(self) -> list[dict[str, Any]]:
        return [h.to_dict(self.enabled[h.name]) for h in self.hooks]


# --------------------------------------------------------------- built-ins

def _emergency_screen(ctx: dict[str, Any]) -> HookResult | None:
    from ..safety import emergencies

    screen = emergencies.screen(ctx["narrative"])
    if not screen.hard_hits:
        return None
    emergency = {"signals": [h["label"] for h in screen.hard_hits],
                 "pathway": emergencies.action_plan(screen)}
    ctx["state"]["emergency"] = emergency
    return HookResult(
        context="【急症筛查 hook】命中：" + "、".join(emergency["signals"])
                + "。标准处置路径：" + "；".join(emergency["pathway"].get("immediate_actions") or [])
                + "（供你判断；急症优先）",
        alert=emergency)


def _fact_seed(ctx: dict[str, Any]) -> HookResult | None:
    from ..conversation import (
        extract_facts_deterministic, merge_facts, sanitize_fact_payload,
    )

    try:
        extracted = extract_facts_deterministic(ctx["message"])
    except Exception:  # noqa: BLE001 - seeding is best effort
        return None
    cleaned, _ = sanitize_fact_payload(extracted)
    changed, _ = merge_facts(ctx["facts"], cleaned, overwrite=False)
    if not changed:
        return None
    return HookResult(context="【病例笔记 hook】已从本条消息预填："
                              + "、".join(changed) + "（如有误请用 record_case_facts 修正）")


_REF_KEYS = {"trial_id", "rec_id", "nct", "pmid", "cluster_id"}
_REF_LIST_KEYS = {"trial_ids", "trial_refs", "verifiable_refs", "evidence"}


def _collect_refs(value: Any, out: set[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in _REF_KEYS and isinstance(item, (str, int)):
                out.add(_norm_ref(item))
            elif key in _REF_LIST_KEYS and isinstance(item, list):
                out.update(_norm_ref(x) for x in item if isinstance(x, (str, int)))
            else:
                _collect_refs(item, out)
    elif isinstance(value, list):
        for item in value:
            _collect_refs(item, out)


def _norm_ref(value: Any) -> str:
    return re.sub(r"[\s\-_]", "", str(value)).upper()


def _evidence_ledger(ctx: dict[str, Any]) -> HookResult | None:
    """Observer: remember every reference a tool actually returned."""
    data = (ctx.get("result") or {}).get("data")
    _collect_refs(data, ctx["state"]["evidence_seen"])
    if ctx.get("tool") == "regimen_dosing" and (ctx.get("result") or {}).get("ok"):
        ctx["state"]["dosing_seen"].add(str((ctx.get("args") or {}).get("regimen_id")))
    return None


def _rule_review(ctx: dict[str, Any]) -> HookResult | None:
    return HookResult(findings=ctx["toolbox"].rule_findings(ctx["consult"]))


def _stage_consistency(ctx: dict[str, Any]) -> HookResult | None:
    finding = ctx["toolbox"].stage_finding(ctx["consult"])
    return HookResult(findings=[finding]) if finding else None


def _citation_provenance(ctx: dict[str, Any]) -> HookResult | None:
    """A cited reference that no tool returned AND that no local source can
    resolve is a likely hallucination — say so."""
    from ..knowledge import trials as trial_lib

    seen = ctx["state"]["evidence_seen"]
    kg = None
    unresolved = []
    for option in ctx["consult"].get("options") or []:
        for ref in (option or {}).get("evidence") or []:
            norm = _norm_ref(ref)
            if not norm or norm in seen or trial_lib.resolve_trial_id(str(ref)):
                continue
            if norm.startswith("REC"):
                if kg is None:
                    from ..knowledge.guideline_kg import load_default

                    kg = load_default()
                if kg is not None and kg.available and kg.get(str(ref).strip()):
                    continue
            unresolved.append(str(ref))
    if not unresolved:
        return None
    return HookResult(findings=[{
        "rule_id": "UNVERIFIED_CITATION", "severity": "warn",
        "message": "以下引用既未出现在本次工具结果中，也无法在试验注册表/指南知识库中找到："
                   + "、".join(sorted(set(unresolved)))
                   + "——请用 search_trials / citation_verify 核实，或删除。"}])


def _dose_provenance(ctx: dict[str, Any]) -> HookResult | None:
    from ..safety.rules import dose_in_payload

    consult = ctx["consult"]
    text = " ".join([str(consult.get("reply") or "")]
                    + [str(o.get("rationale") or "") + str(o.get("name") or "")
                       for o in consult.get("options") or [] if isinstance(o, dict)])
    if not dose_in_payload(text):
        return None
    findings = []
    if not ctx["state"]["dosing_seen"]:
        findings.append({
            "rule_id": "DOSE_NOT_FROM_LIBRARY", "severity": "warn",
            "message": "回答含剂量数值，但本次会诊未调用 regimen_dosing 核对方案库参考剂量；"
                       "请核对或删除具体剂量。"})
    if ctx.get("role") == "patient":
        findings.append({
            "rule_id": "DOSE_TO_PATIENT", "severity": "warn",
            "message": "面向患者的回答含具体剂量；建议改为“具体剂量由主治团队确定”。"})
    return HookResult(findings=findings) if findings else None


_EMERGENCY_WORDS = ("急诊", "立即", "急症", "紧急", "emergency", "urgent", "立刻")


def _emergency_addressed(ctx: dict[str, Any]) -> HookResult | None:
    emergency = ctx["state"].get("emergency")
    if not emergency:
        return None
    consult = ctx["consult"]
    reply = str(consult.get("reply") or "").lower()
    if consult.get("intent") == "emergency" or any(w in reply for w in _EMERGENCY_WORDS):
        return None
    return HookResult(findings=[{
        "rule_id": "EMERGENCY_NOT_ADDRESSED", "severity": "block",
        "message": "急症筛查命中（" + "、".join(emergency["signals"])
                   + "），但结论没有优先处理急症。"}])


def builtin_hooks() -> list[Hook]:
    return [
        Hook("emergency_screen", "user_prompt_submit", "急症筛查",
             "每条消息做子句级、否定感知的肿瘤急症筛查；命中时把标准处置路径放到模型面前，并提示医生。",
             _emergency_screen),
        Hook("fact_seed", "user_prompt_submit", "病例笔记预填",
             "从消息中确定性地提取明确事实（TNM、年龄、ECOG、驱动基因…）预填病例笔记，模型可修正。",
             _fact_seed),
        Hook("evidence_ledger", "post_tool_use", "证据台账",
             "记录工具实际返回过的试验/指南/文献编号与方案库剂量查询，供引用与剂量溯源检查。",
             _evidence_ledger),
        Hook("rule_review", "stop", "规则引擎复核",
             "20 条确定性安全规则复核方案（驱动基因一线、N3 手术、适应证、器官功能、剂量…）。",
             _rule_review),
        Hook("stage_consistency", "stop", "分期一致性",
             "模型给出的分期与 AJCC/UICC 第 9 版分期引擎不一致时提示。", _stage_consistency),
        Hook("citation_provenance", "stop", "引用溯源",
             "方案引用的试验/指南既未经工具返回、也无法在本地注册表或知识库找到时提示（防幻觉引用）。",
             _citation_provenance),
        Hook("dose_provenance", "stop", "剂量溯源",
             "回答含剂量数值但未查方案库，或面向患者给出剂量时提示。", _dose_provenance),
        Hook("emergency_addressed", "stop", "急症优先",
             "急症筛查命中而结论未优先处理急症时提示（高优先级）。", _emergency_addressed),
    ]
