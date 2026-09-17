# NSCLC-Agent 代码审核报告

审核对象：`NSCLC-Agent-main.zip`（8,847 行；`nsclc_agent/` 核心 ~1,900 行 Python + 7 个协议提示词模块 ~4,900 行 Markdown）
审核问题：**是否完美实现了"自主智能的 NSCLC 诊断与决策"**
审核方式：全量通读源码 + 运行测试（`106 passed`）+ 运行 `selftest`（`34/34`）+ 逐格核对 AJCC/UICC 第 9 版分期表 + 针对性缺陷复现脚本

---

## 0. 总体结论

**没有。** 目前的实现可以准确描述为：

> **一个可验证的确定性分期路由器 + 一次性 LLM 提示词补全（one-shot completion）。**

它不是一个自主智能体（autonomous agent）。判断依据是硬性的：`agent.py::run()` 是一条从头到尾没有分支回路的直线——读片（可选，一次）→ 分期 → 路由 → 拼提示词 → **调一次模型** → 返回原始字符串。全代码库 `grep` 不到任何 `tool_call` / `tools=` / `function_call` / 检索 / 循环 / 反思 / 状态机。

分层评价：

| 层次 | 评价 | 说明 |
|---|---|---|
| **分期引擎（诊断的可验证内核）** | ★★★★☆ 扎实 | 第 9 版分期表我逐格核对**完全正确**，含 N2a/N2b、M1c1/M1c2 拆分与 4 条迁移；纯符号、零 I/O、可单测。这是全项目最有价值的部分 |
| **感知层（读片）** | ★★☆☆☆ 契约对，实现弱 | "只提议描述符、绝不定分期"的契约设计是正确的；但提议值不校验、不归一化、视觉后端可静默回落到纯文本模型 |
| **决策层（治疗决策）** | ★☆☆☆☆ 开环 | 无检索、无工具、无输出校验、无安全规则引擎、无循环、无多轮状态。README/DESIGN 宣称的 "evidence-based" 在**代码层面没有任何支撑机制** |
| **工程质量** | ★★★☆☆ | 分层干净、零依赖可跑、106 测试全过；但无 CI、无日志/审计落盘、无重试、无评测集 |

一句话：**诊断（分期）做到了"可验证"，决策没有做到"自主"，也没有做到"evidence-based"——后者目前完全依赖模型凭记忆编造。**

---

## 1. 阻断性问题（P0，直接影响正确性/安全性）

### P0-1 提示词强制要求"检索证据"，但代码里根本没有检索层

7 个模块的 JSON schema 全部要求每一步输出：

```jsonc
"tool_call": { "name": "web_search"|"pubmed_search"|"guideline_search"|"regulatory_label_search", ... },
"tool_result_summary": string,
"sources": [ { "source_type": "PMID"|"DOI"|"NCT"|"FDA"|"LABEL", "source_id": string } ]
```

并硬性规定 `☑ ≥3 retrievals + regulatory anchor; numerics traceable`、
`2.4.X NUMERIC TRACEABILITY: every numeric claim traceable to a retrieved source in the same step`。

而 `providers/` 只实现了 `complete(messages) -> LLMResponse`，**没有任何工具调用能力**。

后果：模型被强制"**表演**"一次检索——凭记忆写出 PMID / NCT 号 / HR / 95%CI。这与同一份提示词第 14 行的 `Do NOT fabricate patient data, trial results, approvals, or guidelines` **直接冲突**，而且是系统性的、每一次调用都会发生的冲突。用这样的输出做 RLHF/过程监督训练数据，等于在训练模型"编造带 PMID 的可信谎言"。

> 这是全项目最严重的设计缺陷：**它把"必须检索"写进了提示词，却没有给模型检索的能力。**

### P0-2 `max_tokens=4096` 必然截断输出，且截断不产生任何告警

- `stage3a.md` 系统提示词 **77,064 字符**（≈20k tokens），其余模块 ~42k 字符。
- 输出 schema 要求：完整 `case_context`（50+ 字段）+ `chosen_process`（**7–14 步**，每步含 thought/tool_call/tool_result_summary/sources）+ `rejected_process` + `preference_reason` + `quality_control`。
- 配置默认 `max_tokens: 4096`（`config.py:21`、`config.example.yaml:20`）。

4096 token 装不下这个 schema，**结果必然是被截断的、无法解析的 JSON**。更糟的是 `agent.run()` 拿到 `response.finish_reason == "length"` **不做任何检查、不打任何 flag**，`AgentResult.error` 仍然是 `None`——上游会把一个残缺输出当成成功结果。

### P0-3 模型输出零校验

`agent.run()` 把 `response.content` 原样塞进 `AgentResult`。没有：JSON 解析、schema 校验、`quality_control` 复核。

而 schema 里的 `quality_control` 全是**模型自评的布尔值**：

```jsonc
"resectability_gate_check": boolean,      // N3 = unresectable
"consolidation_agent_check": boolean,     // durvalumab vs osimertinib
"trial_stage_boundary_check": boolean,    // IIIA-capped trials not applied to IIIB
```

模型只要写 `true` 就"通过"了。**自评不是校验。** 而这些恰恰是可以用确定性代码机器校验的规则（见 §4.5）。

### P0-4 M 分期缺失被静默当作 M0 → 未完成分期检查的病人被判为可根治期

`agent.py:205` `stage_from_strings(case.t, case.n, case.m or "M0")`，`tnm.py:137` 空串 `→ "M0"`。

引擎本身是**正确**的：`MX` 会抛 `StagingError("complete metastatic workup (PET/CT + brain MRI) before assigning a curative stage group")`。但 `or "M0"` 把这条保护绕过去了。实测：

```
Case(t="T2a", n="N0", presentation="no metastatic workup done")
  → Stage IB | module: stage1 | flags: []          ← 零告警，直接进根治模块
```

一个连 PET/CT 都没做的病人，被系统判为 IB 期并路由到"早期根治"协议。这是**临床安全问题**，不是代码风格问题。

### P0-5 `Case.staging_system` 是死字段——AJCC8 的 TNM 会被按第 9 版算并标注为第 9 版

`grep -rn staging_system nsclc_agent/` 结果：只在 `case.py` 里被定义和 `setdefault`，**引擎、agent、路由全程不读**。

所以一个标注 `"staging_system": "AJCC8"` 的病例（旧病历里非常常见），会被第 9 版表计算，然后 `StageResult.edition` 理直气壮地写 `"AJCC/UICC 9th edition"`，并把这个错误分期作为"权威分期"注入系统提示词。第 8 版 T1N1=IIB，第 9 版 T1N1=IIA——这正是模块提示词里反复强调要小心的"staging-edition trap"，而代码本身踩了进去。

### P0-6 缺少 c / p / yp 前缀——临床分期与病理分期不区分

`TNM` 只有 `t/n/m` 三个字段。但下游协议模块的决策**完全依赖**这个区分：

- ADAURA / ALINA / IMpower010 / KEYNOTE-091 用的是 **pStage**（术后）
- CheckMate 816 / KEYNOTE-671 / AEGEAN 用的是 **cStage**（术前）
- 新辅助后的辅助决策依赖 **ypTNM** 与病理缓解（pCR/MPR）

提示词 schema 里有 `c_stage` / `p_stage` / `yp_tnm` 字段，但**代码无法产生它们**——只能靠模型从自由文本里猜。这让"确定性分期是权威"的核心卖点在最关键的分支点上失效。

---

## 2. 正确性缺陷（P1，已实测复现）

复现脚本输出（`dry_run`，mock 后端）：

| # | 缺陷 | 实测结果 |
|---|---|---|
| P1-1 | **裸 `T1` 被静默降级为 `T1a`，伪造出 IA1** | `T1 N0 M0 → T1aN0M0 / IA1` |
| P1-2 | 读片提议的描述符**不做词表校验**就种入病例 | 提议 `T2`/`N2` → 整个 run 失败 `STAGING_ERROR` |
| P1-3 | 影像/病例一致性比较用**裸字符串** | `"t2a"` vs `"T2a"` → 误报 `IMAGING_DISCORDANCE[T]` |
| P1-4 | `stage_group` 标签**不归一化** | `"Stage IIIA"` → `module: None`，run 失败 |
| P1-5 | 视觉后端**静默回落到纯文本模型** | `resolve_vision_provider_name() → "mock"` |
| P1-6 | **Tis 路由到 stage1 模块，而该模块自身拒绝 Tis** | `Tis → 分期 "0" → module: stage1` |

逐条说明：

**P1-1** `tnm.py:103` 的 `fix = {"T1": "T1a", ...}` 与本项目的核心设计哲学**自相矛盾**：`T2`、`N2`、`M1c` 因为歧义被明确拒绝（DESIGN.md §4 专门论述了为什么），但 `T1` 却被静默补全成 `T1a`，凭空制造出 IA1 的精度（真实可能是 IA3）。要么一致地拒绝，要么返回未细分的 `"I"` 并加 note。

**P1-2** `imaging.py::_norm()` 只做去空白和 null 判断，**不校验** `T_CATEGORIES`/`N_CATEGORIES`/`M_CATEGORIES`。视觉模型返回 `"N2"`（提示词明确禁止，但模型不一定听）→ `_ingest_imaging` 原样种入 → `resolve_stage` 抛 `StagingError` → **整个 run 报错退出**。这是"优雅降级"设计的漏洞：读片失败有 `IMAGING_READ_FAILED` 兜底，读片**成功但格式非法**却会把整个 run 打死。

**P1-3** `agent.py:148` `str(human).strip() != str(prop).strip()`。大小写、`Ｔ2a` 全角、`T2A` 全都会误报不一致。应先各自过 `_normalize_t/_normalize_n/_normalize_m` 再比。误报的代价是真实不一致被淹没在噪声里。

**P1-5** `resolve_vision_provider_name()` 的最后一档是 `return self.config.default_provider`——**不检查 `supports_vision`**，`read_imaging()` 也不检查。结果是把 base64 图片发给一个纯文本模型，得到一段幻觉描述或一个 API 错误。应在无可用视觉后端时直接 flag `NO_VISION_PROVIDER` 并跳过读片。

**P1-6** `router.py:16` `"0": "stage1"`（注释：`Tis / AIS-MIA — handled by the Stage I module`），但 `stage1.md:40` 的强制 gate 写的是 `Set case_context.stage_group ∈ {IA1, IA2, IA3, IB}`。路由器把 Tis 送进去，模块自己会判它 out-of-scope。**路由表与模块内容互相矛盾**，且没有测试能发现（`test_router.py` 只测路由不测模块 gate 一致性）。

### 其他 P1

- **P1-7 Occult 期直接报错退出**：`TX N0 M0` → `error="No protocol module available"`，`response=None`。但这恰恰是最需要"下一步查什么"输出的场景（定位检查：支气管镜/胸部增强CT/PET-CT）。`_UNROUTED_GUIDANCE` 里有文字，却没进入任何模型输出路径。
- **P1-8 Provider 无重试/退避/限流处理**：`openai_compatible.py` 一次 `urlopen`，429/500/超时直接抛错。`batch` 跑到一半被限流就全部失败，且没有断点续跑。
- **P1-9 `routing_dict = route_result.__dict__`**（`agent.py:329`）：返回的是**活引用**，调用方修改会污染 dataclass 实例。应 `dataclasses.asdict()`。
- **P1-10 相对路径图片**：`imaging_petct_crosscheck.json` 里 `"images": ["examples/images/sample_placeholder.png"]` 相对 CWD 解析，不是相对 case 文件。换个目录跑就找不到。

---

## 3. "自主智能"缺失的部分（架构级）

DESIGN.md §2 自己列了一张 POMDP 对照表，诚实地把大部分格子标成了"extension seam"（待扩展）。这份诚实值得肯定，但它也正好说明**"自主智能"目前基本没有实现**。逐项对照：

| 自主智能体应有的能力 | 本项目现状 |
|---|---|
| **Agent loop**（感知→推理→行动→观察→再推理） | ❌ 无。`run()` 是直线，模型只被调用一次 |
| **工具调用**（检索、计算器、查表） | ❌ 无。提示词要求工具，代码不提供 |
| **信念状态 b(s)**（分期的概率分布） | ❌ 无。单点分期。DESIGN.md 承认 "replace point stage with a distribution" 是待办 |
| **VOI / 主动信息获取规划** | ⚠️ 极简。`_next_step_hint()` 是 3 个 `if` + 静态字符串字典（T→CT/支气管镜，N→EBUS，M→PET-CT+脑MRI），不随病例、成本、既有检查变化，也不参与任何决策 |
| **纵向状态 / 多轮** | ❌ 无。无法处理复诊、治疗反应评估、进展后再分期、ypTNM |
| **MDT 多角色协作**（外科/放疗/内科/病理/影像辩论与仲裁） | ❌ 无。单一模型单次回答 |
| **确定性安全规则引擎** | ❌ 无。N3 不手术、EGFR/ALK 禁用围术期 IO、durvalumab 不与 cCRT 同期、不加量到 74 Gy——全是**可机器校验**的规则，现在 100% 靠提示词祈祷 |
| **输出校验 / 自我纠错循环** | ❌ 无 |
| **临床决策质量评测集** | ❌ 无。106 个测试全在测管线和分期，**零个**测决策质量 |
| **审计日志落盘**（run_id / prompt hash / 版本锁） | ❌ 无。`to_dict()` 有 provenance，但从不持久化，也无 prompt 版本指纹 |

---

## 4. 具体修改建议（按优先级，可直接落地）

### 4.1 【P0】给 Provider 层加工具调用，建 `nsclc_agent/tools/`

这是把"evidence-based"从口号变成机制的唯一路径。

```python
# providers/base.py
@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict          # JSON Schema

@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict

@dataclass
class LLMResponse:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)   # 新增
    ...

class LLMProvider(ABC):
    @abstractmethod
    def complete(self, messages, *, params=None,
                 tools: list[ToolSpec] | None = None) -> LLMResponse: ...
```

`OpenAICompatibleProvider._payload()` 加 `body["tools"]`，`_parse()` 解析 `choices[0].message.tool_calls`。

新增工具（先做 3 个最关键的）：

```
nsclc_agent/tools/
  pubmed.py           # E-utilities，返回 PMID + 标题 + 摘要 + 年份（有官方免费 API）
  clinicaltrials.py   # ClinicalTrials.gov API v2，NCT 号核验
  labels.py           # openFDA drug label API，适应症/人群核验
  calculators.py      # ppoFEV1/ppoDLCO、BSA 与卡铂 AUC(Calvert)、Charlson、RECIST 1.1
  registry.py         # ToolSpec 注册 + 分发
```

**最小可行版本**：哪怕只做 `verify_citation(pmid|nct|doi)` 一个工具——在返回结果前把模型写出的每个 PMID/NCT 去真实 API 核验一遍，不存在的标记 `FABRICATED_CITATION`——就能把幻觉引用问题从"不可见"变成"可见且可拦截"。这是投入产出比最高的一步。

配套改 `agent.run()` 为工具循环：

```python
def run(self, case, *, max_tool_rounds: int = 8, ...):
    messages = self.build_messages(...)
    for _ in range(max_tool_rounds):
        resp = prov.complete(messages, tools=self.tool_registry.specs())
        if not resp.tool_calls:
            break
        messages.append(Message("assistant", resp.content, tool_calls=resp.tool_calls))
        for tc in resp.tool_calls:
            messages.append(Message("tool", self.tool_registry.dispatch(tc), tool_call_id=tc.id))
```

如果**不打算**做工具层，那么必须反向修改：把 7 个模块提示词里所有 `tool_call` / `sources` / `MUST retrieve` / `≥3 retrievals` 全部删除，改成"仅陈述你确信的定性结论，**禁止**给出任何未经检索的 HR/CI/p 值/PMID"，并在 README 里明确说明本系统不检索证据。**两条路必须选一条，不能维持现状。**

### 4.2 【P0】输出校验 + 截断检测

```python
# nsclc_agent/validation.py
def validate_response(result: AgentResult, module_key: str) -> list[str]:
    flags = []
    if result.response and result.response.finish_reason == "length":
        flags.append("OUTPUT_TRUNCATED: raise max_tokens; the module schema "
                     "needs ≥16k output tokens")
    try:
        payload = json.loads(strip_fences(result.response.content))
    except json.JSONDecodeError as e:
        flags.append(f"OUTPUT_NOT_JSON: {e}")
        return flags
    flags += jsonschema_errors(payload, SCHEMAS[module_key])
    flags += safety_rule_violations(payload, result.staging)   # §4.5
    return flags
```

同时把 `config.example.yaml` 的 `max_tokens` 从 `4096` 改到 **`16384`**（stage3a 建议 `24576`），并在 `prompts/__init__.py::MODULES` 里为每个模块声明 `min_output_tokens`，`agent.run()` 启动前检查 `params.max_tokens >= module.min_output_tokens`，不满足直接 flag。

把 7 个模块里的 JSON schema 抽成真正的 JSON Schema 文件 `nsclc_agent/schemas/stage*.schema.json`，提示词里改为引用——一份定义，代码和模型共用，避免漂移。

### 4.3 【P0】分期引擎修补

```python
# tnm.py
# (1) M 必须显式给出
def _normalize_m(m: str) -> str:
    raw = _clean(m).upper()
    if not raw:
        raise StagingError(
            "M category not provided: complete PET/CT + brain MRI, or pass "
            "'MX' explicitly to acknowledge an incomplete metastatic workup")
    ...

# (2) T1 不再静默降级
if raw.upper() == "T1":
    raise StagingError(
        "Ambiguous 'T1': specify T1a (≤1 cm) / T1b (>1–2 cm) / T1c (>2–3 cm) "
        "— they determine IA1 vs IA2 vs IA3")

# (3) c/p/yp 前缀
@dataclass
class TNM:
    t: str; n: str; m: str = "M0"
    prefix: str = "c"          # "c" | "p" | "yp" | "r" | "a"
    def __str__(self): return f"{self.prefix}{self.t}{self.n}{self.m}"

# (4) 版本闸门
def stage(tnm: TNM, *, edition: str = "AJCC9") -> StageResult:
    if edition != "AJCC9":
        raise StagingError(
            f"This engine implements AJCC/UICC 9th edition only; the case is "
            f"labeled {edition}. Restage with 9th-edition descriptors "
            f"(N2 must be split into N2a/N2b, M1c into M1c1/M1c2).")

# (5) 分期标签归一化
def normalize_stage_group(label: str) -> str:
    """'Stage IIIA' / 'stage iiia' / '3A' / 'ⅢA' → 'IIIA'"""
```

`agent.resolve_stage()` 相应改为：删掉 `or "M0"`；捕获 M 缺失后打 `M_NOT_ESTABLISHED` flag 并把 `NEXT_STEP_SUGGESTED` 提到最前面；`case.stage_group` 先过 `normalize_stage_group()`；把 `case.staging_system` 传给 `stage(edition=...)`。

### 4.4 【P1】感知层修补

```python
# imaging.py
_VALID = {"t": set(T_CATEGORIES), "n": set(N_CATEGORIES), "m": set(M_CATEGORIES)}

def _norm_descriptor(value, kind):
    s = _norm(value)
    if s is None: return None, None
    try:
        canon = {"t": _normalize_t, "n": _normalize_n, "m": _normalize_m}[kind](s)
    except StagingError as e:
        return None, f"IMAGING_DESCRIPTOR_REJECTED[{kind.upper()}]: {s!r} — {e}"
    return canon, None
```

- `_ingest_imaging()` 比较前双方都过归一化，消除 `"t2a"` vs `"T2a"` 误报。
- `read_imaging()` 开头加 `if not prov.supports_vision: raise ImagingError(...)`；`resolve_vision_provider_name()` 找不到 vision 后端时返回 `None`，`run()` 打 `NO_VISION_PROVIDER_CONFIGURED` 并跳过读片，**不要**回落到文本模型。
- 图片路径相对 case 文件解析：`Case.from_dict(data, base_dir=path.parent)`。
- 加图片数量上限（如 20 张）与总字节上限，避免请求体爆掉。

### 4.5 【P1】新增确定性安全规则引擎 `nsclc_agent/safety/rules.py`

这是把"safety rules"从提示词搬进代码的关键，也是最能提升"智能可信度"的改动。每条规则输入 `(staging, case, parsed_output)`，输出违规 flag：

```python
RULES = [
  Rule("N3_NO_SURGERY",
       lambda s, c, o: s["n_category"] == "N3" and mentions_surgery(o),
       "N3 disease routed to surgery — N3 is unresectable"),
  Rule("EGFR_ALK_EXCLUDES_PERIOP_IO",
       lambda s, c, o: driver_positive(c, "egfr", "alk") and mentions_periop_io(o),
       "Perioperative/adjuvant ICI recommended in an EGFR/ALK-positive case"),
  Rule("EGFR_III_CONSOLIDATION",
       lambda s, c, o: s["stage_group"].startswith("III") and driver_positive(c, "egfr")
                       and mentions(o, "durvalumab") and not mentions(o, "osimertinib"),
       "Durvalumab consolidation in EGFR+ unresectable III (LAURA → osimertinib)"),
  Rule("NO_CONCURRENT_DURVALUMAB", ...),      # PACIFIC-2 阴性
  Rule("NO_RT_DOSE_ESCALATION", ...),         # RTOG 0617：不上 74 Gy
  Rule("TRIAL_STAGE_BOUNDARY", ...),          # CheckMate816/ADAURA/ALINA/IMpower010/KEYNOTE-091 ≤ IIIA
  Rule("STAGE0_NO_ADJUVANT", ...),            # AIS/MIA 不上辅助治疗
  Rule("CITATION_EXISTS", ...),               # 每个 PMID/NCT 必须核验通过
]
```

违规时 `AgentResult.flags` 加 `SAFETY_RULE_VIOLATION[...]`，并把 `error` 置位。**这套规则同时可以直接当作评测指标**——见 4.6。

### 4.6 【P1】新增评测集 `eval/`

目前 106 个测试**零覆盖临床决策质量**。建议：

```
eval/
  golden/           # 40–60 例，每例含 case + 期望决策要点 + 必须命中的证据 + 必须避免的错误
  metrics.py        # 分期准确率 / 路由准确率 / 安全规则违规率 / 引用真实率 /
                    # schema 合规率 / 截断率 / 关键决策点召回
  run_eval.py       # nsclc-agent eval --provider poe --out eval-report.json
```

金标准病例应刻意覆盖：8 版→9 版迁移 4 个格子、N2a/N2b 分界、M1c1/M1c2、EGFR+ III 期（osimertinib vs durvalumab）、N3 手术陷阱、试验分期边界、AIS/MIA 过度治疗、寡转移 LCT、PS 2–3 的降级治疗。

### 4.7 【P1】工程与合规

- **CI**：加 `.github/workflows/ci.yml`，跑 `pytest` + `python -m nsclc_agent selftest` + `ruff`。
- **重试**：`openai_compatible.complete()` 加指数退避（429/5xx，3 次），`batch` 加断点续跑（跳过已存在的 `*.result.json`）。
- **审计落盘**：`AgentResult` 增加 `run_id`（uuid4）、`prompt_sha256`、`module_version`、`engine_version`、`config_digest`、`timestamp`，`batch` 写 `run.jsonl`。这才是 DESIGN.md 说的"让生成数据可信"的那个属性。
- **PHI**：`batch -o` 会把完整病例文本写盘。README 已提"仅用去标识化影像"，但应扩展到文本，并加 `--redact` 开关 + 输出目录默认 `chmod 700`。
- **依赖**：`_resolve_secret` 允许 config 里内联 `api_key`，与 README"绝不写入配置文件"的说法不符——建议内联时打印警告。
- `routing_dict` 改用 `dataclasses.asdict()`。

### 4.8 【P2】提示词层面的问题

1. **版本号不一致**：`stage3a.md` 头部是 `Version 3.3 (2026-01 Update)`，其余 6 个是 `2026-06`；而 `stage3a.md:310` 内部又出现 `(v3.4 UPDATE)`。README 统一称 v3.3/2026-06。建议在 `MODULES` 里显式声明版本并加测试断言文件头与之一致。
2. **深度严重不均**：`stage3a` 77k 字符，其余 ~42k。IIIA 模块明显是原来的"总模块"改名而来（它的 scope gate 讲的是围术期/辅助通用框架，不是 IIIA 专属逻辑），其余 6 个是后来按统一模板压缩写的。建议把 stage3a 里通用的部分抽成 `_common.md` 由所有模块共享，各模块只保留分期特异逻辑——既省 token 又消除不一致。
3. **缺 Stage 0 模块**：如 P1-6 所述，Tis/AIS-MIA 应有独立 `stage0.md`（WHO 第 5 版把 AIS 归为前驱病变，处理逻辑与 IA1 完全不同：随访 vs 亚肺叶切除，绝不辅助治疗），而不是塞给 stage1 让它自己判 out-of-scope。
4. **Occult 期无模块**：应加一个 `workup.md`（定位检查协议），让 `TX N0 M0` 也能产出可执行输出，而不是 `error` 退出。
5. **中文能力被硬禁**：`All reasoning ... MUST be in ENGLISH`。如果目标用户是中文临床/教学场景，应改为"术语用英文、说明可中文"或加 `--language` 参数。

---

## 5. 值得肯定的部分

审核不是只挑毛病，以下几点确实做得好，改动时**不要破坏**：

1. **"把最高幻觉风险的一步从模型手里拿走"这个核心判断是完全正确的**，而且执行到位——分期引擎纯符号、零 I/O、可单测，分期结果作为"权威"注入系统提示词并明令禁止模型重推。这是整个项目最有价值的设计思想。
2. **第 9 版分期表逐格核对无误**，包括 4 条迁移（T1N1 IIB→IIA、T1N2a IIIA→IIB、T3N2a IIIB→IIIA、T2N2b IIIA→IIIB）、N2a/N2b 与 M1c1/M1c2 拆分。`selftest.py::EXPECTATIONS` 作为 CLI 与 pytest 共享的单一真相源，设计很干净。
3. **拒绝歧义而不猜测**（`T2`/`N2`/`M1c` 抛 `StagingError` 并给出可操作提示）——这个原则对临床系统是对的，只是执行不彻底（`T1`、缺失 `M` 是漏网之鱼）。
4. **"读片只提议、不定分期"的契约**方向正确，`MODEL_PROPOSED_UNVERIFIED` 标记、discordance 交叉核对、失败优雅降级都想到了。
5. **依赖方向严格向下**（`staging` 不依赖任何东西 → `providers` 不依赖临床层 → `agent` 组合），零第三方依赖即可跑，mock 后端让全链路离线可测——工程分层很干净。
6. **DESIGN.md 诚实标注了哪些是"extension seam"**，没有把未实现的能力吹成已实现（虽然 README 的措辞比 DESIGN.md 激进不少，建议向 DESIGN.md 看齐）。

---

## 6. 建议的实施顺序

| 阶段 | 内容 | 产出 |
|---|---|---|
| **第 1 周** | §4.3 分期引擎修补 + §4.4 感知层修补 + §4.2 截断检测 + P1-9/P1-10 | 消除所有已复现的正确性缺陷；测试从 106 扩到 ~140 |
| **第 2–3 周** | §4.5 安全规则引擎 + §4.2 JSON Schema 校验 + `max_tokens` 修正 | 输出首次变成**可校验**的；quality_control 从自评变成他评 |
| **第 4–6 周** | §4.1 工具层（先做 `verify_citation` + `pubmed_search`）+ agent 工具循环 | "evidence-based" 首次名副其实；引用幻觉可拦截 |
| **第 7–8 周** | §4.6 评测集 + §4.7 CI/审计/重试 | 有了衡量标尺，后续改动可回归 |
| **之后** | 分期信念分布、真正的 VOI 规划器、纵向多轮状态、MDT 多角色 | 才谈得上"自主智能" |

---

## 7. 一句话总结

**这是一个优秀的"可验证 NSCLC 分期引擎 + 高质量协议提示词库"，但它现在还不是一个自主智能体，也还不是一个 evidence-based 系统——因为它要求模型检索却不给检索能力，要求模型输出结构化 JSON 却从不校验，要求模型遵守安全规则却没有一条规则写进代码。**

把 §4.1（工具/检索）、§4.2（输出校验）、§4.5（安全规则引擎）这三件事做完，它才配得上 README 里的那句 "evidence-based decision-support agent"；再把 §4.6（评测集）做完，才有资格用它生成 RLHF 训练数据。
