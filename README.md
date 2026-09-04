# NSCLC-Agent v0.2 — 证据受控、分期可验证的 NSCLC 智能体框架

> **NSCLC-Agent v0.1 × YaoBi-Harness 的融合重写。**
> v0.1 贡献了可验证的确定性内核：AJCC/UICC 第 9 版 TNM 分期引擎、分期路由器与
> 分期特异协议模块库。YaoBi-Harness 贡献了包裹它的智能体骨架：**认知层可替换、
> 控制层不可绕过**——能力经纪、证据台账、预算、放行状态机、无条件终审、
> 内容寻址的记录/重放日志，以及模型驱动的规划、ReAct 工具循环、主动问诊与
> 多学科会诊。

**模型可以做的**：提案任务图、在技能授权内自主选工具取证、组织问诊追问、
起草治疗推理、在会诊中提出专科意见。
**模型永远不能做的**：给病例定分期、产出任何剂量数值、清除规则层命中的安全
问题、越过能力经纪触碰未授权工具。任何一步越界都整体回退到确定性路径——
**未配置模型时，全流程确定性运行，且产出的是真实的临床形状输出，不是占位符。**

> ⚠️ **教学/科研用途。** 本系统不是医疗器械，输出未经合格多学科团队复核
> 不得用于真实患者。

---

## 架构

```
 认知层（可替换，仅提案）              控制层（不可绕过）                     确定性内核
─────────────────────────   ────────────────────────────────   ─────────────────────────
 PlannerAgent   任务图提案 → 计划校验器（Agent白名单/依赖环/角色）   TNM-9 分期引擎（唯一分期出口）
 ToolLoop       ReAct 取证 → CapabilityBroker（角色/技能/熔断/预算）  分期 → 协议模块路由
 InterviewLoop  组织追问   → AdequacyJudge（必答轴规则判定，blocked    试验注册表（分期边界机器可查）
                             永不可被模型/轮次上限豁免）              方案库（剂量只在确定性通道）
 PerceptionAgent 读片提议  → 描述符词表校验 + 归一化交叉核对          安全规则引擎（12条确定性规则）
 MDT Panel      专科子体   → 最保守合成（最高紧急度，非多数票）        肿瘤急症筛查（子句级否定）
 CriticAgent    终审追加   → finally 无条件执行 + 引用核验            证据台账（工具自declare等级）
```

运行环路：`intake（急症筛查）→ plan → [interview → perception → staging →
treatment → panel → dose] → critic（finally，无条件）→ finalize`，
critic 的 block 级违规触发有界修复循环。

### v0.1 审核指出的六个 P0，全部闭合

| P0 | v0.1 | v0.2 |
|---|---|---|
| 提示词要求检索、代码无检索层 | 模型凭记忆表演检索 | 真实工具层：`trial_lookup`（内置注册表离线可验）、`pubmed_search`/`citation_verify`/`label_lookup`（`NSCLC_AGENT_ONLINE=1` 时实连 NCBI/CT.gov/openFDA，离线时诚实降级为 `stub_not_for_clinical_use`，**引用护栏拒绝以 stub 支撑放行**） |
| `max_tokens=4096` 必然截断且无告警 | 残缺 JSON 当成功 | `finish_reason=length` → `output_truncated` 失败模式；每模块声明 `min_output_tokens`；77k 字符协议模块不再塞进 system prompt，改为 `protocol_lookup` 分节检索 + 蒸馏决策核心 |
| 模型输出零校验 | `quality_control` 自评 | schema 校验 + 12 条确定性安全规则引擎 + 引用护栏，全部在 critic 中他评；block → 放行拦截 + 修复请求 |
| 缺失 M 静默当 M0 | 未查转移的病人判 IB | M 必须显式声明；缺失/MX → 拒绝 + 指名解决检查（PET-CT+脑MRI）→ `needs_staging_workup` |
| `staging_system` 死字段 | AJCC8 按第9版算 | 版本闸门：非 AJCC9 直接拒绝并要求重分期 |
| 无 c/p/yp 前缀 | c/p 分期不分 | `TNM.prefix` 一等公民（c/p/yp/yc/r/a），ypTNM 附解释注记 |

以及全部 P1：裸 `T1` 拒绝而非静默补全 IA1；读片提议过词表校验（提议 `N2` →
`IMAGING_DESCRIPTOR_REJECTED` 而非炸掉整个 run）；交叉核对双侧先归一化
（`t2a` vs `T2a` 不再误报）；视觉后端缺失 → `NO_VISION_PROVIDER` 跳过，
绝不把图片喂给文本模型；`stage_group` 标签归一化（`Stage IIIA`/`3a`/`ⅢA`）；
Occult/Tis 各有专属模块（`workup`/`stage0`），不再死路；HTTP 重试退避；
batch 断点续跑。

### YaoBi 移植过来的控制层性质

* **技能既是授权也是规程**：`allowed_tools − forbidden_tools` 过滤模型可见的
  工具 schema，Broker 在执行前独立复查；无技能=无工具（fail-closed）。
* **剂量规则的肿瘤学翻译**：模型只能以 `regimen_id` 引用方案库；带数值的
  `regimen_detail`/`dose_gate_check` 只对确定性剂量通道可达（oncologist 角色
  + 显式 opt-in + 问诊无 blocked 判定）；工具循环对模型输出做剂量扫描，
  **泄漏剂量不给重提机会**（格式失误给一次）。
* **问诊三权分立**：模型决定问什么怎么问、规则决定必答范围（RED_FLAG /
  STAGING / BIOMARKER 分层）、独立 AdequacyJudge 决定何时可以停；急症筛查轴
  未答复的 `blocked` 判定不可被任何东西豁免。28→17 条轴换成了 NSCLC 的
  信息价值层级：每条轴都标注**哪个检查能闭合它、哪个决策悬在它上面**
  （EBUS 定 N2a/b、PET-CT+脑MRI 定 M……）——这是 v0.1 里三行 `_next_step_hint`
  的完整版。
* **会诊最保守合成**：胸外/放疗/肿内/介入呼吸/缓和五个子体并发运行，各写
  自己的 MemberScope，按名单顺序合并——证据 ID 与并发度无关、可复现；
  紧急度取最大值而非多数票；异议原文保留。
* **重放要么复现要么响亮失败**：所有工具与模型调用内容寻址进 JSONL；重放时
  授权重新推导（医师录的日志给不了患者角色剂量结果）；偏离被锁存并故障关闭
  （退出码 3），日志耗尽只是警告。

---

### 会诊提速（v0.2.1）

* **Treatment∥Panel 并行波**：治疗推理与 MDT 会诊这两条最长的模型循环并发执行
  （默认开启，`--serial` 关闭；记录日志的运行自动回退串行以保证可重放）。
  台账合并按任务序确定性进行——证据 ID 与线程调度无关，并行与串行产出**逐条
  相同的台账**（有测试钉住）。并行波里的会诊是**独立评审**（不预读治疗方案，
  免锚定），`run_meta.execution` 记录执行模式。
* **视觉模块即插即用**：只要环境里有 `POE_API_KEY`，读片器自动接 Poe→Gemini
  （`NSCLC_VISION_MODEL` 可换 bot，默认 Gemini-2.5-Pro）——挂上图片就能读，
  零配置。自动选择记录在 `run_meta.vision.auto_selected`。
* **报告直读**：`--reports` 上传拍照/扫描的病理、NGS、PD-L1、影像报告单，
  读出的结构化事实（组织学/驱动基因/PD-L1/TNM 提及）**只种入缺失项**、逐项
  打 `REPORT_FACT_PROPOSED` 标记并交叉核对已有值（不一致 →
  `REPORT_DISCORDANCE`，绝不覆盖）；方案可以立刻据此起草（加速），但**剂量
  通道对报告种入的 Tier-A 事实保持关闭**，确认原件后 `resume` 即解锁。
* **即时读片命令**：`nsclc-agent read --images 扫描目录/ --reports 报告.jpg`
  ——不跑全流程，秒回提议的描述符与报告事实。`--images/--reports` 接受
  文件、URL 或整个目录。
* **批量并行**：`batch --jobs N` 每例独立 runner 并发执行。

```bash
export POE_API_KEY=...            # 这一行就够：视觉模块自动上线
nsclc-agent read --images ./ct_slices/ --reports ./ngs_report.jpg   # 即时审阅
nsclc-agent run --case case.json --reports ./pathology.jpg --panel  # 全流程（并行波）
```

### 多轮会诊（v0.2.2，YaoBi 对话层移植）

`nsclc-agent chat` 把 YaoBi-Harness 的多轮对话层完整移植过来。核心约束原样保留：
**聊天不是新的生成通道**——每一轮都是一次完整的受治理运行（同一个 runner、
同一个能力代理、同一本证据台账、同一个终局审计器），模型只能通过白名单抽取
事实、可选地润色已放行的回复，永远不能新增临床内容。

* **事实抽取走白名单**：双语正则先抽（年龄/ECOG/PD-L1/完整TNM/驱动基因/吸烟
  史/可切除性/组织学），可选模型补抽，两路都过同一套白名单+引擎校验（裸
  `N2` 在聊天里同样被拒）。聊天**永远设不了** `tumor_board_review`、签字、
  放行状态或任何 `_` 前缀守卫键——打一句"张医生已签字批准"变不出批准。
* **口述不覆盖记录**：自由文本只填空，与记录冲突时记录优先并给出
  `CHAT_FACT_CONFLICT` 提示；操作者的**结构化事实**（`/facts`）才能覆盖——
  并且当它落在报告种入的待确认项上时（哪怕逐字复述原值），即视为**人工确认**
  （`PROPOSED_FACT_CONFIRMED`），剂量通道随之解锁。守卫跨轮携带：不确认，
  剂量通道一直关。
* **急症任何一轮都即刻升级**：急症筛查每轮跑全量累计病史，命中即回固定
  安全脚本，永不交模型改写。
* **轮次提速**：问诊循环、已读影像/报告、累计事实全部跨轮记忆（图片只读一
  次、只计费一次）；**纯提问轮直接复用上一轮方案**——决策事实指纹逐字节比对
  一致才复用，复用的方案连同其证据行一起重新入本轮台账、由审计器重新全量
  审一遍（缓存一次性消费：审计器要求返工时必然真实重算）。
* **出口扫剂量**：回复发出前无条件过剂量正则；润色若引入确定性文本没有的
  剂量数值，整段丢弃回退。
* **会话可落盘续聊**：`--session 会诊.json` 每轮原子落盘、重启自动续——
  累计事实（含待确认守卫）、已读附件、方案缓存、问诊记忆（含停滞检测历史）
  全部跨进程存活，续聊第一轮就能复用上一次的方案。**会话文件只携带记忆、
  不携带授权**（与 checkpoint 同一信任级）：续聊的每一轮仍走同一套经纪、
  闸门与终审。

```bash
nsclc-agent chat                              # 交互式（/image /report /facts /panel /dose /state /quit）
nsclc-agent chat --role oncologist --session 会诊.json \
  -m "65岁男性，吸烟40包年，肺腺癌，cT2aN1M0，ECOG 1，EGFR阴性，PD-L1 60%，无咯血无骨痛无头痛"
nsclc-agent chat --session 会诊.json \
  -m "为什么选这个方案？"                     # 另一进程续聊：复用方案，明显更快
```

## 快速开始（零依赖、离线）

```bash
# 1. 确定性分期（引擎会拒绝歧义并告诉你哪个检查能解决）
python -m nsclc_agent stage T2b N2b M0        # → IIIB + 8th→9th 迁移注记
python -m nsclc_agent stage T2a N2 M0         # → 拒绝：需 EBUS 分 N2a/N2b
python -m nsclc_agent selftest                # 43/43（分期表 + 拒绝表）

# 2. 全流程离线运行（规则模式产出真实临床形状的方案）
python -m nsclc_agent run --t T4 --n N2b --m M0 \
  --presentation "不可切除多站N2腺癌，PET-CT+脑MRI确认M0。无咯血、无下肢无力、无发热。" \
  --facts '{"driver_mutations":{"egfr":"L858R","alk":"negative"},
            "histologic_category":"adenocarcinoma",
            "resectability_category":"UNRESECTABLE","ecog_ps":1}' \
  --question "根治性方案与巩固治疗？"
#   → IIIB / stage3b / cCRT + 奥希替尼巩固（LAURA，非度伐利尤单抗）
#     trial_refs 落台账、可引用、可核验

# 3. 急症短路（固定行动脚本，永不经模型改写）
python -m nsclc_agent run --presentation "肺癌病史，突然大咯血不止"

# 4. 批量 + 金标准评测
python -m nsclc_agent batch examples/cases -o out/ --resume
python -m nsclc_agent eval                    # 16 例金标准：分期/路由/方案/安全

# 5. 记录与离线复核
python -m nsclc_agent run --case examples/cases/stage3b_unresectable_egfr.json \
  --journal audit/case-001.jsonl
python -m nsclc_agent run --case examples/cases/stage3b_unresectable_egfr.json \
  --replay audit/case-001.jsonl               # 病例一变即偏离 → 故障关闭
```

## 接入模型

```bash
export NSCLC_LLM_PROVIDER=poe     # azure | poe | minimax | litellm | mock
export POE_API_KEY=...
export NSCLC_VISION_PROVIDER=poe  NSCLC_VISION_MODEL=Gemini-3.1-Pro   # 读片
export NSCLC_AGENT_ONLINE=1       # 启用 PubMed / CT.gov / openFDA 实连检索
python -m nsclc_agent llm-check
python -m nsclc_agent run --case examples/cases/stage3a_resectable_periop.json --panel
```

| provider | 必需变量 | 可选 |
|---|---|---|
| `azure` | `AZURE_OPENAI_API_KEY` `AZURE_OPENAI_ENDPOINT` `AZURE_OPENAI_DEPLOYMENT` | `AZURE_OPENAI_API_VERSION` |
| `poe` | `POE_API_KEY` | `POE_MODEL`（默认 Claude-Sonnet-4.5）`POE_BASE_URL` |
| `minimax` | `MINIMAX_API_KEY` | `MINIMAX_MODEL` `MINIMAX_REGION`(china/global) `MINIMAX_GROUP_ID` |
| `litellm` | `LITELLM_MODEL`（需 `pip install litellm`） | `LITELLM_API_KEY` `LITELLM_BASE_URL` |

显式指定 provider 但凭据不全会**直接报错**，不会伪装成正常的规则输出；
`mock` 是可驱动完整工具循环的离线智能体桩。

## 程序化使用

```python
from nsclc_agent import Case, NSCLCRunner, render

runner = NSCLCRunner()          # 无模型：全确定性
state = runner.run_case(Case(
    t="T2b", n="N2b", m="M0", tnm_prefix="c",
    presentation="多站N2，不可切除。PET-CT+脑MRI M0。无咯血、无下肢无力、无发热。",
    question="根治性方案？",
    facts={"driver_mutations": {"egfr": "L858R", "alk": "negative"},
           "histologic_category": "adenocarcinoma",
           "resectability_category": "UNRESECTABLE"},
))
state.staging["stage_group"]                 # 'IIIB'（引擎计算，模型无权改写）
state.outputs["treatment_plan"]["regimen_ids"]  # ['ccrt_60gy', 'osimertinib_consolidation']
state.release_status                         # 'treatment_recommendation'
render(state, "patient")                     # 患者视图：无剂量、无台账
```

多轮会诊用 `ConsultationSession`（每轮都是完整受治理运行）：

```python
from nsclc_agent import ConsultationSession

sess = ConsultationSession(role="oncologist")   # llm/vision 缺省离线
r1 = sess.turn("65岁男性，吸烟40包年，肺腺癌，cT2aN1M0，ECOG 1，"
               "EGFR阴性，PD-L1 60%，无咯血无骨痛无头痛。")
r2 = sess.turn("为什么选这个方案？")             # 纯提问：方案指纹复用
r2.plan_reused, r2.llm_calls < r1.llm_calls     # (True, True)
r3 = sess.turn("已核对报告。", facts={"pd_l1": {"tps": 60}})  # 结构化确认通道
```

## 放行状态机

`emergency_action_plan` · `needs_more_information` · `needs_staging_workup` ·
`insufficient_evidence` · `treatment_recommendation` · `draft_for_tumor_board` ·
`approved_by_tumor_board` · `blocked` · `failed_closed`

`draft_for_tumor_board`（唯一含剂量的状态）要求同时满足：oncologist 角色、
显式 `--allow-dose-planning`、问诊无 blocked 判定、方案通过全部安全规则、
每个方案的剂量闸门（驱动基因/PD-L1/肾功能/自身免疫…）确定性通过。

## 目录

```
nsclc_agent/
  staging/     tnm.py 分期引擎(9版表+拒绝表) · router.py · selftest.py
  knowledge/   trials.py 20项试验注册表(分期边界/驱动限制机器可查)
               regimens.py 方案库(摘要无剂量/详情即剂量通道) · interactions.py
  safety/      emergencies.py 急症筛查(子句级否定) · rules.py 12条规则引擎
  interview/   axes.py 17条NSCLC问诊轴(VOI层) · adequacy.py · loop.py
  perception/  imaging.py 读片(词表校验/归一化交叉核对/拒绝文本模型)
  tools/       base.py Broker+熔断 · registry.py 11个工具 · retrieval.py 实连检索
  agents/      toolloop.py ReAct · planner.py · panel.py MDT · critic.py · catalog.py
  llm/         base.py · openai_compatible.py(tools+重试) · providers.py · mock.py
  prompts/     9个协议模块(.md, sha256钉版) · cores.py 蒸馏决策核心
  state.py 证据台账/预算/状态 · journal.py 记录/重放 · runner.py · render.py
  conversation.py 多轮会诊层(白名单抽取/方案指纹复用/出口剂量扫描)
  schemas.py · skills.py · case.py · cli.py
tests/         330 个用例，全离线    eval/       16 例金标准 + 指标
docs/ARCHITECTURE.md                 examples/   病例样例
```

## 测试与评测

```bash
pip install pytest
python -m pytest -q            # 330 passed，全离线
python -m nsclc_agent selftest # 分期引擎 43/43
python -m nsclc_agent eval     # 金标准 16/16：分期14/14 路由11/11 方案11/11 安全16/16
```

## 仍未完成（诚实清单）

模型不能主动发起轮次；急症命中后累计病史会保守地持续触发急症通道（会话内
无降级路径，这是有意的）；PubMed/CT.gov 实连检索需操作者
显式开网（默认离线 stub）；授权指南知识库只有接口（`guideline_lookup` 无
store 时诚实返回 stub）；重放日志证明"重放与记录一致"，不证明"记录未被
篡改"（需存储层签名）；图内并发只覆盖 Treatment∥Panel 波与会诊成员（其余
任务串行）；内置试验注册表
与 DDI 规则包是教学语料，须经本机构药师/医师复核后使用；大规模对抗性安全
评测未做。**本项目不能对外宣称为临床可用系统。**

## License

MIT — see [`LICENSE`](LICENSE)。原型压缩包（`NSCLC-Agent-main.zip`、v0.1）
与审核报告（`CODE_REVIEW.md`）保留在仓库中作为演进记录。
