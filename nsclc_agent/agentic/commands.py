"""Slash commands — shared by the CLI REPL and the web composer.

A command either becomes a PROMPT for the agent (``/mdt``, ``/plan`` …) or
a LOCAL action the surface performs (``/compact``, ``/rewind`` …). The
surfaces render the same list (``/help``) and expand the same way.
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


COMMANDS: tuple[Command, ...] = (
    Command("mdt", "[重点]", "召集多学科会诊：并行请各专科子智能体评估，再由主诊综合", "prompt",
            "请召集多学科会诊（MDT）：先用 update_plan 列出会诊计划，再按本例需要用 delegate "
            "并行邀请相关专科（影像科、病理与分子病理科、胸外科、放疗科、肿瘤内科、临床药师、"
            "循证医学研究员），最后综合各专科意见、说明分歧如何取舍，提交结论。{arg}"),
    Command("plan", "[目标]", "只制定会诊计划与所需信息，暂不下结论", "prompt",
            "先只制定会诊计划：用 update_plan 列出步骤，并列出需要补充的信息与检查；"
            "暂不展开治疗分析，用 submit_consult 简要说明计划与待补信息。{arg}"),
    Command("review", "", "让智能体用规则引擎与受治理流水线自查当前方案", "prompt",
            "请对你当前的方案自查：调用 rule_review 与 governed_reference，逐条说明是否调整，"
            "然后重新提交结论。{arg}"),
    Command("evidence", "[主题]", "请循证医学子智能体核实关键证据", "prompt",
            "请用 delegate 邀请 evidence（循证医学研究员）核实本例方案的关键证据（入组人群、"
            "主要结果、指南推荐差异），据此修订并提交结论。{arg}"),
    Command("patient", "", "把当前结论改写成患者能听懂的说明", "prompt",
            "请把当前会诊结论改写成患者和家属能听懂的说明：为什么、怎么做、接下来做什么；"
            "不写具体剂量，提交结论。{arg}"),
    Command("whatif", "<假设>", "在不改变病例笔记的前提下推演一个假设", "prompt",
            "【假设推演 · 不改变病例笔记】如果 {arg}——请在工具调用中用 facts_override 推演，"
            "不要调用 record_case_facts，对比当前结论后提交。"),
    Command("compact", "", "压缩较早的会诊记录（由模型写摘要），释放上下文", "local"),
    Command("rewind", "[轮次]", "回退到第 N 轮之前（默认上一轮）", "local"),
    Command("usage", "", "查看本会诊的模型调用、token 与上下文占用", "local"),
    Command("agents", "", "查看可用的专科子智能体", "local"),
    Command("tools", "", "查看智能体可调用的工具", "local"),
    Command("hooks", "", "查看 hooks 及启用状态", "local"),
    Command("memory", "", "查看已载入的机构规范与用户偏好（记忆）", "local"),
    Command("clear", "", "开始新会诊（保留记忆与设置）", "local"),
    Command("help", "", "显示命令列表", "local"),
)

BY_NAME = {c.name: c for c in COMMANDS}


def parse(text: str) -> tuple[Command | None, str]:
    text = str(text or "").strip()
    if not text.startswith("/"):
        return None, text
    head, _, arg = text[1:].partition(" ")
    return BY_NAME.get(head.strip().lower()), arg.strip()


def expand(text: str) -> dict[str, Any]:
    """{"kind": "prompt", "prompt"} | {"kind": "local", "command", "arg"} |
    {"kind": "text", "text"} (not a command) | {"kind": "unknown"}."""
    if not str(text or "").strip().startswith("/"):
        return {"kind": "text", "text": text}
    command, arg = parse(text)
    if command is None:
        return {"kind": "unknown", "text": text,
                "message": "未知命令，输入 /help 查看可用命令"}
    if command.kind == "prompt":
        return {"kind": "prompt", "command": command.name,
                "prompt": command.template.format(arg=arg).strip()}
    return {"kind": "local", "command": command.name, "arg": arg}


def listing() -> list[dict[str, str]]:
    return [{"name": c.name, "args": c.args, "description": c.description, "kind": c.kind}
            for c in COMMANDS]
