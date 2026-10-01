"""Specialist sub-agents: the MDT, as an agent architecture.

The lead agent delegates focused questions to specialists through the
``delegate`` tool — the same pattern as Claude Code's Task tool / custom
sub-agents and the OpenAI Agents SDK's agents-as-tools: each specialist
runs its OWN loop with its own system prompt, its own (narrower) toolset and
a fresh context (the task + the case notes), and returns a structured
report. The lead's context only receives the report, so a case can be
examined from six angles without flooding the lead's window.

Several delegations in one model step run concurrently natively; the
browser build runs them one after another.

Specialists read; they do not write the case notes, cannot delegate
further and cannot submit the consult — the lead integrates. Custom
specialists are plain definitions (name, title, description, prompt,
tools), loadable from Markdown files with a front-matter header.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .loop import Cancelled, run_loop
from .tools import Tool, Toolset, obj

REPORT_TOOL = "submit_report"


@dataclass
class AgentDefinition:
    name: str
    title: str
    description: str
    prompt: str
    tools: tuple[str, ...]
    max_steps: int = 8
    builtin: bool = True
    title_en: str = ""
    description_en: str = ""

    def label(self, lang: str = "zh") -> str:
        return (self.title_en or self.title) if lang == "en" else self.title

    def to_dict(self, lang: str = "zh") -> dict[str, Any]:
        en = lang == "en"
        return {"name": self.name, "title": self.label(lang),
                "description": (self.description_en or self.description) if en
                else self.description,
                "prompt": self.prompt, "tools": list(self.tools),
                "max_steps": self.max_steps, "builtin": self.builtin}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentDefinition":
        name = re.sub(r"[^a-z0-9_]", "_", str(data.get("name") or "").strip().lower())[:40]
        if not name:
            raise ValueError("a specialist needs a name")
        return cls(name=name, title=str(data.get("title") or name)[:40],
                   description=str(data.get("description") or "")[:400],
                   prompt=str(data.get("prompt") or "")[:6000],
                   tools=tuple(str(t) for t in data.get("tools") or ()),
                   max_steps=max(2, min(int(data.get("max_steps") or 8), 16)),
                   builtin=False,
                   title_en=str(data.get("title_en") or "")[:40],
                   description_en=str(data.get("description_en") or "")[:400])


_COMMON = """你是 NSCLC-Agent 多学科会诊（MDT）中的{title}。主诊智能体把一个具体问题交给你。
只从你的专科角度作答：先用工具核实，再给出判断；证据不足时直接说明缺什么。
不要替其他专科下结论，不要写给患者的话。完成后调用 submit_report 提交专科意见
（report 用中文、要点式、简洁；key_points 列出关键发现；recommendations 列出你的建议；
concerns 列出风险或分歧；confidence 标明把握度）。"""

BUILTIN_AGENTS: tuple[AgentDefinition, ...] = (
    AgentDefinition(
        "radiology", "影像科医师",
        "解读影像与分期描述符：T/N/M 判定、淋巴结分站、远处转移与脑转移评估、分期引擎核对、读片。",
        "关注：T 大小与侵犯、N 分站（9 版 N2a/N2b）、M 分类（M1c1/M1c2）、需要补做的影像检查（PET-CT、脑 MRI、EBUS 等）。",
        ("stage_tnm", "cns_assessment", "read_attachment", "protocol_sections",
         "guideline_search"),
        title_en="Radiologist",
        description_en="Imaging and staging descriptors: T/N/M, nodal stations, distant and "
                       "brain metastases, staging-engine check, image reading."),
    AgentDefinition(
        "pathology", "病理与分子病理科医师",
        "组织学分型、驱动基因报告解读（变异类别、阴阳性、检测充分性）、PD-L1、是否需要扩展 NGS 或再活检。",
        "关注：组织学是否明确；驱动基因逐个判定（阳性/阴性/未检/失败）；EGFR 变异类别；检测平台与充分性；建议补检项目。",
        ("assess_biomarkers", "guideline_search", "search_trials"),
        title_en="Pathologist (molecular)",
        description_en="Histology, driver reports (variant class, positive/negative, test "
                       "adequacy), PD-L1, and whether broader NGS or re-biopsy is needed."),
    AgentDefinition(
        "thoracic_surgery", "胸外科医师",
        "可切除性与可手术性、围术期（新辅助/辅助/围术期）策略、手术禁忌。",
        "关注：可切除性（单站/多站 N2、N3）、可耐受手术（肺功能、PS）、围术期方案的人群边界与驱动基因影响。",
        ("stage_tnm", "check_indication", "protocol_sections", "search_trials",
         "guideline_search", "check_organ_function"),
        title_en="Thoracic surgeon",
        description_en="Resectability and operability, perioperative (neoadjuvant / "
                       "adjuvant / perioperative) strategy, contraindications to surgery."),
    AgentDefinition(
        "radiation_oncology", "放疗科医师",
        "根治性放化疗、巩固治疗、SBRT、寡转移局部治疗、脑转移 SRS/WBRT。",
        "关注：根治性 cCRT 的适应证与巩固方案选择、寡转移局部治疗、脑转移局部治疗时机；不给出放疗剂量分割的具体处方。",
        ("protocol_sections", "search_trials", "cns_assessment", "guideline_search",
         "check_indication"),
        title_en="Radiation oncologist",
        description_en="Definitive chemoradiotherapy, consolidation, SBRT, local therapy for "
                       "oligometastases, SRS / WBRT for brain metastases."),
    AgentDefinition(
        "medical_oncology", "肿瘤内科医师",
        "全身治疗选择：靶向、免疫、化疗、ADC；一线与后线序贯；耐药机制。",
        "关注：驱动基因导向的一线选择、免疫适应证（PD-L1、驱动阴性）、进展后的序贯与耐药机制、方案的适应证核对。",
        ("search_regimens", "check_indication", "later_line_options", "search_trials",
         "guideline_search", "regimen_dosing", "prognosis", "assess_biomarkers"),
        title_en="Medical oncologist",
        description_en="Systemic therapy: targeted, immunotherapy, chemotherapy, ADCs; first "
                       "and later lines; resistance mechanisms."),
    AgentDefinition(
        "pharmacy", "临床药师",
        "剂量参考、器官功能调整、药物相互作用、毒性监测。",
        "关注：方案库参考剂量（须核对说明书与本院方案）、肾/肝功能与 QTc、合并用药相互作用、监测要点。",
        ("regimen_dosing", "check_organ_function", "interaction_check",
         "search_regimens"),
        title_en="Clinical pharmacist",
        description_en="Reference dosing, organ-function adjustments, drug interactions, "
                       "toxicity monitoring."),
    AgentDefinition(
        "evidence", "循证医学研究员",
        "检索并核实试验与指南证据：入组人群、主要结果、证据等级、地区指南差异。",
        "关注：关键试验的入组人群是否覆盖本例、结果与局限、NCCN/ESMO/CSCO 推荐差异；引用必须来自工具结果。",
        ("search_trials", "guideline_search", "citation_verify", "pubmed_search"),
        title_en="Evidence researcher",
        description_en="Finds and verifies trial and guideline evidence: populations, main "
                       "results, evidence level, regional guideline differences."),
)


def report_tool() -> Tool:
    return Tool(
        REPORT_TOOL,
        "Submit your specialist opinion to the lead agent (ends your turn).",
        obj({"report": {"type": "string"},
             "key_points": {"type": "array", "items": {"type": "string"}},
             "recommendations": {"type": "array", "items": {"type": "string"}},
             "concerns": {"type": "array", "items": {"type": "string"}},
             "confidence": {"type": "string", "enum": ["high", "moderate", "low"]}},
            ["report"]),
        category="control", terminal=True, label="提交专科意见")


class SubAgentRunner:
    """Runs specialist loops for the lead's ``delegate`` tool."""

    def __init__(self, llm: Any, clinical: Toolset, *,
                 definitions: list[AgentDefinition],
                 case_notes: Callable[[], dict[str, Any]],
                 emit: Callable[[dict[str, Any]], None],
                 cancelled: Callable[[], bool] = lambda: False,
                 parallel: bool = False, temperature: float = 0.2,
                 max_tokens: int = 4000, instructions: str = "",
                 post_tool: Callable[[str, dict[str, Any], dict[str, Any]], str | None]
                 | None = None, language: str = "zh") -> None:
        self.language = language
        self.llm = llm
        self.clinical = clinical
        self.definitions = {d.name: d for d in definitions}
        self.case_notes = case_notes
        self.emit = emit
        self.cancelled = cancelled
        self.parallel = parallel
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.instructions = instructions
        self.post_tool = post_tool
        self.usage = {"calls": 0, "prompt": 0, "completion": 0}
        self.consulted: list[str] = []
        self._lock = threading.Lock()

    def tool(self) -> Tool:
        roster = "; ".join(f"{d.name} = {d.title}: {d.description}"
                           for d in self.definitions.values())
        return Tool(
            "delegate",
            "Delegate a focused question to a specialist sub-agent of the MDT. "
            "The specialist works in its own context with its own tools and "
            "returns a structured opinion (report, key points, "
            "recommendations, concerns, confidence). Give a precise task; "
            "several delegations in one step run in parallel. Specialists: "
            + roster,
            obj({"agent": {"type": "string", "enum": list(self.definitions)},
                 "task": {"type": "string",
                          "description": "the precise question for the specialist"},
                 "context": {"type": "string",
                             "description": "anything the specialist needs beyond the case notes"}},
                ["agent", "task"]),
            handler=self.delegate, category="delegation", parallel_safe=True,
            label="Specialist consult" if self.language == "en" else "请专科会诊")

    def delegate(self, agent: str, task: str, context: str = "") -> dict[str, Any]:
        definition = self.definitions.get(str(agent))
        if definition is None:
            return {"ok": False, "summary": f"unknown specialist {agent!r}",
                    "data": {"error": f"unknown specialist {agent!r}",
                             "available": list(self.definitions)}}
        system = _COMMON.format(title=definition.title) + "\n\n" + definition.prompt
        if self.instructions:
            system += "\n\n【机构规范与用户偏好】\n" + self.instructions
        if self.language == "en":
            from .prompts import LANGUAGE_DIRECTIVE_EN

            system += LANGUAGE_DIRECTIVE_EN
        user = (f"【主诊智能体的问题】{task}\n\n"
                + (f"【补充背景】{context}\n\n" if context else "")
                + "【当前病例笔记】" + json.dumps(self.case_notes(), ensure_ascii=False,
                                               default=str))
        messages: list[dict[str, Any]] = [{"role": "system", "content": system},
                                          {"role": "user", "content": user}]
        allowed = [t for t in definition.tools if self.clinical.get(t)]
        toolset = Toolset.of(self.clinical.only(allowed).tools.values(), [report_tool()])
        submitted: dict[str, Any] = {}

        def terminal(arguments: dict[str, Any]) -> tuple[dict[str, Any], bool]:
            submitted.update(arguments if isinstance(arguments, dict) else {})
            return {"status": "accepted"}, True

        self.emit({"type": "subagent_start", "agent": definition.name,
                   "title": definition.label(self.language), "task": task})
        try:
            loop = run_loop(
                self.llm, messages, toolset, emit=self.emit, terminal=terminal,
                terminal_name=REPORT_TOOL, agent=definition.name,
                max_steps=definition.max_steps, temperature=self.temperature,
                max_tokens=self.max_tokens, parallel=self.parallel,
                cancelled=self.cancelled,
                nudge="请调用 submit_report 提交你的专科意见。",
                post_tool=self.post_tool, base_event={"depth": 1})
        except Cancelled:
            raise
        except Exception as exc:  # noqa: BLE001 - a failed specialist is an observation
            self.emit({"type": "subagent_end", "agent": definition.name, "ok": False})
            return {"ok": False, "summary": f"{definition.title} failed: {exc}",
                    "data": {"error": str(exc)}}
        with self._lock:
            self.usage["calls"] += loop.calls
            self.usage["prompt"] += loop.prompt_tokens
            self.usage["completion"] += loop.completion_tokens
            self.consulted.append(definition.name)
        report = dict(submitted) if submitted else {"report": loop.text or "（未提交意见）"}
        self.emit({"type": "subagent_end", "agent": definition.name, "ok": True,
                   "confidence": report.get("confidence")})
        tools_used = [s["name"] for s in loop.steps if s.get("kind") == "tool"]
        title = definition.label(self.language)
        sep = ": " if self.language == "en" else "："
        return {"ok": True,
                "summary": title + sep + str(report.get("report") or "")[:120],
                "data": {"specialist": title, **report,
                         "tools_used": tools_used},
                "_ui": {"specialist": definition.name, "title": title,
                        "report": report, "children": loop.steps}}


_FRONT_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)


def load_definitions(directory: Path) -> list[AgentDefinition]:
    """Custom specialists from ``*.md`` files with a simple front matter:

        ---
        name: geriatric_oncology
        title: 老年肿瘤科医师
        description: 老年综合评估与治疗强度
        tools: assess_biomarkers, check_organ_function, prognosis
        ---
        (prompt body)
    """
    out: list[AgentDefinition] = []
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.md")):
        match = _FRONT_RE.match(path.read_text(encoding="utf-8"))
        if not match:
            continue
        meta: dict[str, Any] = {}
        for line in match.group(1).splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                meta[key.strip()] = value.strip()
        meta["tools"] = [t.strip() for t in str(meta.get("tools") or "").split(",") if t.strip()]
        meta["prompt"] = match.group(2).strip()
        try:
            out.append(AgentDefinition.from_dict(meta))
        except (ValueError, TypeError):
            continue
    return out
