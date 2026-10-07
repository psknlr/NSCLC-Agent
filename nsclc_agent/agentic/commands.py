"""Slash commands — shared by the CLI REPL and the web composer.

A command either becomes a PROMPT for the agent (``/mdt``, ``/plan`` …) or
a LOCAL action the surface performs (``/compact``, ``/rewind`` …). The
surfaces render the same list (``/help``) and expand the same way, in the
clinician's language (``zh`` or ``en``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Command:
    name: str
    args: str
    description: str
    kind: str  # prompt | local
    template: str = ""
    args_en: str = ""
    description_en: str = ""
    template_en: str = ""

    def text(self, lang: str = "zh") -> tuple[str, str, str]:
        """(args, description, template) in ``lang``."""
        if lang == "en":
            return (self.args_en or self.args, self.description_en or self.description,
                    self.template_en or self.template)
        return self.args, self.description, self.template


COMMANDS: tuple[Command, ...] = (
    Command("mdt", "[重点]", "召集多学科会诊：并行请各专科子智能体评估，再由主诊综合", "prompt",
            "请召集多学科会诊（MDT）：先用 update_plan 列出会诊计划，再按本例需要用 delegate "
            "并行邀请相关专科（影像科、病理与分子病理科、胸外科、放疗科、肿瘤内科、临床药师、"
            "循证医学研究员），最后综合各专科意见、说明分歧如何取舍，提交结论。{arg}",
            "[focus]", "Convene the MDT: specialist sub-agents assess in parallel, the lead integrates",
            "Convene a multidisciplinary tumour board (MDT): first lay out the consult plan with "
            "update_plan, then use delegate to consult the relevant specialists in parallel "
            "(radiology, pathology/molecular pathology, thoracic surgery, radiation oncology, "
            "medical oncology, clinical pharmacy, evidence), and finally integrate their opinions, "
            "explain how disagreements were resolved, and submit the conclusion. {arg}"),
    Command("plan", "[目标]", "只制定会诊计划与所需信息，暂不下结论", "prompt",
            "先只制定会诊计划：用 update_plan 列出步骤，并列出需要补充的信息与检查；"
            "暂不展开治疗分析，用 submit_consult 简要说明计划与待补信息。{arg}",
            "[goal]", "Plan the consult and list missing information — no conclusion yet",
            "Plan only: lay out the steps with update_plan and list the information and tests "
            "still needed; do not analyse treatment yet — use submit_consult to summarise the "
            "plan and the missing information. {arg}"),
    Command("review", "", "让智能体自查当前方案（分期、生物标志物分类、适应证与证据）", "prompt",
            "请对你当前的方案自查：按 AJCC/UICC 第 9 版重新核对分期（含转移部位与 M 分期）、"
            "生物标志物分类、每个方案的适用人群与证据（用 search_regimens / search_trials / "
            "guideline_search / citation_verify 等可用工具；内核辅助模式下也可调用 rule_review 与 "
            "governed_reference），逐条说明是否调整，然后重新提交结论。{arg}",
            "", "Self-review the current plan: stage, biomarker category, indications, evidence",
            "Review your current plan: re-check the stage against AJCC/UICC 9th edition (including "
            "the metastatic sites and the M category), the biomarker category, and each regimen's "
            "population and evidence with the tools you have (search_regimens, search_trials, "
            "guideline_search, citation_verify; in kernel-assisted mode also rule_review and "
            "governed_reference). Say for each point whether you change the plan, then resubmit "
            "the conclusion. {arg}"),
    Command("audit", "", "独立核对：确定性内核在会诊后核对分期、生物标志物分类与方案（只给医生看，不回传模型）",
            "local", description_en="Independent audit: the deterministic kernel re-reads the "
                                    "stage, biomarker category and plan after the consult "
                                    "(for the clinician; never returned to the model)"),
    Command("evidence", "[主题]", "请循证医学子智能体核实关键证据", "prompt",
            "请用 delegate 邀请 evidence（循证医学研究员）核实本例方案的关键证据（入组人群、"
            "主要结果、指南推荐差异），据此修订并提交结论。{arg}",
            "[topic]", "Ask the evidence sub-agent to verify the key evidence",
            "Use delegate to ask the evidence researcher to verify the key evidence behind this "
            "plan (trial populations, main results, guideline differences), then revise and "
            "submit the conclusion. {arg}"),
    Command("patient", "", "把当前结论改写成患者能听懂的说明", "prompt",
            "请把当前会诊结论改写成患者和家属能听懂的说明：为什么、怎么做、接下来做什么；"
            "不写具体剂量，提交结论。{arg}",
            "", "Rewrite the conclusion for the patient and family",
            "Rewrite the current conclusion for the patient and family in plain language: why, "
            "what will be done, what happens next; no specific doses. Submit it. {arg}"),
    Command("whatif", "<假设>", "在不改变病例笔记的前提下推演一个假设", "prompt",
            "【假设推演 · 不改变病例笔记】如果 {arg}——请在工具调用中用 facts_override 推演，"
            "不要调用 record_case_facts，对比当前结论后提交。",
            "<hypothesis>", "Reason through a hypothesis without changing the case notes",
            "[What-if · case notes unchanged] Suppose {arg} — reason with facts_override in your "
            "tool calls, do not call record_case_facts, compare with the current conclusion and "
            "submit."),
    Command("compact", "", "压缩较早的会诊记录（由模型写摘要），释放上下文", "local",
            description_en="Compact earlier turns into a model-written summary to free context"),
    Command("rewind", "[轮次]", "回退到第 N 轮之前（默认上一轮）", "local",
            args_en="[turn]", description_en="Rewind to before turn N (default: the last turn)"),
    Command("usage", "", "查看本会诊的模型调用、token 与上下文占用", "local",
            description_en="Model calls, tokens and context use of this consult"),
    Command("agents", "", "查看可用的专科子智能体", "local",
            description_en="List the specialist sub-agents"),
    Command("tools", "", "查看智能体可调用的工具", "local",
            description_en="List the tools the agent can call"),
    Command("hooks", "", "查看 hooks 及启用状态", "local",
            description_en="List the hooks and whether they are on"),
    Command("memory", "", "查看已载入的机构规范与用户偏好（记忆）", "local",
            description_en="Show the loaded institution protocols and preferences (memory)"),
    Command("clear", "", "开始新会诊（保留记忆与设置）", "local",
            description_en="Start a new consult (memory and settings are kept)"),
    Command("help", "", "显示命令列表", "local", description_en="List the commands"),
)

BY_NAME = {c.name: c for c in COMMANDS}


def parse(text: str) -> tuple[Command | None, str]:
    text = str(text or "").strip()
    if not text.startswith("/"):
        return None, text
    head, _, arg = text[1:].partition(" ")
    return BY_NAME.get(head.strip().lower()), arg.strip()


def expand(text: str, lang: str = "zh") -> dict[str, Any]:
    """{"kind": "prompt", "prompt"} | {"kind": "local", "command", "arg"} |
    {"kind": "text", "text"} (not a command) | {"kind": "unknown"}."""
    if not str(text or "").strip().startswith("/"):
        return {"kind": "text", "text": text}
    command, arg = parse(text)
    if command is None:
        return {"kind": "unknown", "text": text,
                "message": "Unknown command — type /help for the list" if lang == "en"
                else "未知命令，输入 /help 查看可用命令"}
    if command.kind == "prompt":
        return {"kind": "prompt", "command": command.name,
                "prompt": command.text(lang)[2].format(arg=arg).strip()}
    return {"kind": "local", "command": command.name, "arg": arg}


def listing(lang: str = "zh") -> list[dict[str, str]]:
    out = []
    for c in COMMANDS:
        args, description, _ = c.text(lang)
        out.append({"name": c.name, "args": args, "description": description, "kind": c.kind})
    return out
