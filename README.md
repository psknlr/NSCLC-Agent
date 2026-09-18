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
 PerceptionAgent 读片提议  → 描述符词表校验 + 归一化交叉核对          安全规则引擎（14条确定性规则）
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
| 模型输出零校验 | `quality_control` 自评 | schema 校验 + 14 条确定性安全规则引擎 + 引用护栏，全部在 critic 中他评；block → 放行拦截 + 修复请求 |
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

### 指南知识图谱（v0.2.3，内置计算化指南 KG）

内置六部指南的计算化知识图谱（`nsclc_agent/knowledge/data/guideline_kg.json.gz`，
~880KB）：**NCCN 5.2026、ESMO 2025 早期/局部晚期、ESMO 2023 驱动基因阳性/阴性、
CSCO 2025、CN 中西医结合 2023**——共 2,960 条推荐，带人群判据、动作、原始
分级（I级推荐/Category 2A/ESMO A…）、来源页码/段落，以及 147 个跨区域
（CN/US/EU）一致性聚类（full/partial/disagreement）。

接入方式延续本框架的证据纪律，三条硬规则：

* **抽取状态即证据等级**：图谱全部条目为 `curation_status = llm_extracted`
  （机器抽取、未经临床复核），入台账即定级 `kg_llm_extracted` ——
  **不可放行等级**。KG 只提供上下文与线索；每条命中带 `verifiable_refs`
  （试验号/PMID），经 `trial_lookup`/`citation_verify` 核验后才产生可放行
  证据。未来某条目被临床医师复核（`clinician_verified`）即自动升级
  guideline 等级。
* **剂量不出库**：88 条推荐原文携带剂量数值；所有出参（含检索键、来源段落、
  聚类摘要）经与规则引擎同一 `DOSE_RE` 的**深度递归清洗**——有全量测试
  逐条钉住。剂量仍只存在于确定性剂量通道。
* **负面知识提示、不拦截**：`do_not_recommend`/`avoid`/`contraindicated`
  条目按病例匹配为 cautions 提示人工权衡；拦截权仍只属于 14 条确定性安全
  规则——未复核的抽取不获得否决权。引用的指南原文放在
  `outputs["guideline_context"]`（oncologist 视图），**不进入方案自述**：
  规则引擎扫描的是系统自己的话，一句被引用的 "durvalumab … after
  concurrent CRT" 不会被误判为方案提议同步用药（集成时实测过的误拦截，
  已钉回归测试）。

每次运行自动挂载病例匹配的 KG 上下文（支持 + 警示，各 ≤4 条，经 broker
的工具调用入台账）；模型在推理技能内也可自行调用 `guideline_lookup`
（支持 query/stage/gene/topic/direction=negative/rec_id/cluster_id）。

* **人群判据确定性求值（v0.2.4）**：8,727 条抽取判据中可恢复语义的七类
  （分期/组织学/驱动基因/ECOG/PD-L1/可切除性/年龄）由
  `kg_eligibility.py` 对病例确定性求值，每条命中带 `eligibility` 判定
  （consistent / possible_mismatch / population_mismatch / …）。针对已
  实测的抽取噪声（`val` 与源文 span 直接矛盾、"EGFR WT" 缩写、章节标题
  推定的判据），三条铁律：**源文 span 优先**、**字段冲突即弃权**（不给
  结论）、**机器推定（assumed）的判据永不产生"确信不符"**。对未复核条目，
  判定只用于标注与排序（确信不符者沉底但不隐藏）；LAURA/PACIFIC 的人群
  区分（EGFR 野生型限定的度伐利尤单抗巩固 vs EGFR 阳性病例 →
  `population_mismatch`）由判据自动导出，有测试钉住。
* **逐条临床复核，复核一条、升级一条（v0.2.4）**：`nsclc-agent kg-review`
  ——追加式复核台账（默认 `knowledge/data/curation.jsonl`，随 git 审阅；
  `NSCLC_KG_CURATION` 可指向机构自有台账），每条复核**内容寻址绑定**
  （记录被复核内容的 sha256，内容一变复核即作废、要求重审——与重放日志
  同一纪律）。`--verify` 后该条目即按 **guideline 等级（可放行）** 提供、
  检索加权优先，且其判据获得信任：确信不符的已复核条目会从病例上下文中
  **可见地排除**（`excluded_verified_mismatch`），命中病例的已复核警示
  打 `KG_VERIFIED_CAUTION` 旗标（仍仅提示——拦截权只属于确定性规则）。
  `--reject` 将错误抽取从服务中隐藏（`--show` 仍可审计），`--revoke`
  撤销此前复核；台账只追加、后条覆盖前条，损坏即响亮失败。

```bash
nsclc-agent kg --info                                  # 库与六部指南的出处
nsclc-agent kg 奥希替尼 辅助治疗 --gene EGFR --stage IIB   # 过滤检索
nsclc-agent kg --stage IIIB --direction negative       # 病例相关的负面知识
nsclc-agent kg durvalumab --stage IIIB \
  --facts '{"driver_mutations":{"egfr":"L858R"},"ecog_ps":1}'  # 带资格判定
nsclc-agent kg --show REC_CN_000249                    # 单条 + 来源段落
nsclc-agent kg --cluster RCL_5A751363A4                # 跨区域分歧对比

nsclc-agent kg-review --queue --topic systemic_treatment   # 待复核队列（决策权重排序）
nsclc-agent kg-review REC_EU_000435                        # 展示单条 + 来源段落供核对
nsclc-agent kg-review REC_EU_000435 --verify \
  --reviewer "张三 (胸部肿瘤内科主治医师)" --notes "对照ESMO原文核对一致"
nsclc-agent kg-review --status                             # 复核进度统计
```

### 对话式策略推演与生存预后（v0.2.5）

* **生存"预测"的诚实版**：每次运行自动附带 `prognosis` 输出——**分期队列
  生存统计 + 方向性预后因素**，绝不产出个体预测数字。5年总生存查
  IASLC 分期项目发表队列（Goldstraw 2016 第8版数据库，逐格标注近似、
  出处、以及第9版重新分组的跨版本可比性警示；分期迁移病例额外提示）；
  临床分期与病理分期各查各的队列；分期项目未单独发表的组（0期/隐匿癌）
  **如实留白，不填补**。方向性因素（ECOG≥2、体重下降、IV期 EGFR/ALK、
  PD-L1≥50%无驱动、III期CRT后巩固可及…）只给方向与理由，效应数字留在
  试验注册表条目里、经 `trial_lookup` 落台账引用（FLAURA/CROWN/
  KEYNOTE-024/PACIFIC/LAURA），不转述不复算。证据等级
  `published_cohort_statistics`（可放行的人群统计引用）。
* **角色分景**：数字进 oncologist 视图与回复（`※ 预后（人群队列，非个体
  预测）…`）；**patient 视图永远不推送生存数字**——患者问"还能活多久"
  得到的是不带数字的支持性回应与"与主治团队当面讨论"的指引（预后告知
  是临床对话，不是表格投喂）。
* **`/whatif` 假设推演**：会诊中随时问"如果……会怎样"——改分期、改
  ECOG、改可切除性（自由文本抽取或 `{json}` 结构化，均过同一套白名单+
  引擎校验），假设情形跑**同一套完整审计流水线**，回一份确定性对比
  （分期 → 分期、方案 → 方案、5年队列 → 5年队列、预后因素数）。围栏：
  会话记忆零改动（事实/病史/方案缓存/问诊记忆全不动，推演只进
  transcript 存档）；**假设不构成确认**（报告待确认守卫原样保留，
  `WHAT_IF_GUARD_KEPT`）；**剂量通道在推演中永远关闭**；假设语境的
  急症措辞（"如果大咯血"）按筛查器的假设抑制不触发，陈述式情形
  （"患者突然大咯血不止"）在推演内走固定急症脚本。

```bash
nsclc-agent chat --role oncologist --session 会诊.json \
  -m "62岁女性，不可切除IIIB期肺腺癌，cT4N2bM0，EGFR L858R阳性，ECOG 1，无咯血无头痛无骨痛" \
  -m "/whatif 如果其实是可切除的 cT2aN1M0 呢" \
  -m "/whatif 如果 ECOG 恶化到 3"
# → 分期：IIIB → IIB；5年总生存队列：约26% → 约53%；方案对比；因素 1 → 2 项
```

### 临床红队修复（v0.3.0，驱动基因本体重建）

外部临床红队复核确认了三个可被正式放行的错误，同一根因：**planner 与
critic 共享一个过粗的 gene→positive/negative 布尔表示，因此能共同确信一个
错误答案**（EGFR exon20 插入被放行奥希替尼一线与 LAURA 巩固；ROS1/RET 阳性
+ PD-L1 80% 被放行帕博利珠单抗单药、零违规）。本版从数据结构层修复：

* **EGFR 变体类本体**（`knowledge/biomarkers.py`）：ex19del / L858R /
  非经典敏感（G719X/L861Q/S768I）/ exon20 插入 / T790M / C797S /
  unclassified 成为一等公民。planner 按类分派（经典→FLAURA 系三选项；
  exon20ins→**PAPILLON 阿米万他单抗+化疗**；非经典→阿法替尼路径；
  unclassified→**分子肿瘤板，不猜药**）；规则引擎用同一本体**独立**实现
  `EGFR_VARIANT_MISMATCH`（exon20ins/C797S 配奥希替尼系 → block；
  非经典 → warn）——模拟 planner 犯错的方案全部被 critic 拦截，有测试钉住。
* **驱动面扩展到治疗控制层**：ROS1（TRIDENT-1/瑞普替尼）、RET
  （LIBRETTO-431/塞普替尼）、MET ex14（GEOMETRY/卡马替尼）、BRAF V600E
  （dabrafenib+trametinib）、NTRK（拉罗替尼）进入 planner 一线分派与
  `DRIVER_FIRST_LINE` 拦截名单（“驱动阳性 + ICI 一线”不分 PD-L1 一律
  block）；HER2 与 KRAS G12C 按当前指南**提示不否决**（一线仍化疗±IO，
  T-DXd/索托拉西布后线注记）。MET 扩增、BRAF 非 V600 等非适应证**不误拦**
  （对照组测试）。IV 期非鳞仅 EGFR/ALK 阴性而无广谱 NGS → 面板不完整
  提示 + 补检建议（标注不拦截）。
* **TNM 版本感知的试验边界**（`staging/legacy8.py`）：注册表每个试验带
  `tnm_edition`（现存全部为第8版时代），边界检查先把病例描述符**回映射到
  试验入组版本**——T2bN2b（9版 IIIB / 8版 IIIA）用 ADAURA 是
  `TRIAL_EDITION_MIGRATION`（注记），不再误判外推；T4N2a（两版都 IIIB）
  仍如实声明外推。9 版分期引擎仍是病例分期唯一出口。
* **AIS/MIA 语义漂移清零**（`staging/concepts.py` 单一出处）：AIS=Tis=0期；
  MIA=T1mi=**IA1**——协议模块、路由注释、planner 理由、规则文案全部对齐
  引擎，测试钉住。
* **剂量门变体感知**：奥希替尼系换 `egfr_classical_sensitizing` 门
  （exon20ins→fail，非经典→unverified 交 MDT）；新药剂量**不编造数字**
  （"per label — not encoded"，由药师录入后启用）。
* **金标准集 16→27 例**：三个红队 blocker + ROS1/RET/METex14/BRAF/NTRK/
  非经典 EGFR/HER2/KRAS 对照/版本迁移全部固化为机器可检期望。

### 声明式适应证谓词（v0.3.1，红队建议 #2）

方案适应证从 if/else 升级为**每个方案一份机器可执行的人群声明**
（`knowledge/indications.py`，30/30 全库覆盖，`undeclared_regimens()==[]`
入测试）：分期（含第8版回映射）、组织学、驱动基因类（复用变体本体）、
无可行动驱动（ICI 类方案）、PD-L1 TPS/TC 阈值、可切除性、可手术性、
寡转移、既往治疗线——**三值判定**：

* `eligible` 全部条件满足；`ineligible` 至少一条确信不满足；
  `unknown` 无失败但缺事实——**unknown 回补检，永不回猜测**。
* **一份声明、三处求值**：planner 的 `opt()` 闸门（ineligible 响亮剔除并
  标注表↔声明分歧；unknown 保留为暂定推荐 + 缺失事实进补检清单；已声明
  外推按外推放行）；attach 层对最终方案发布
  `outputs["indication_report"]`（oncologist 可见每个方案为何在/不在
  人群内）；critic 规则 `INDICATION_PREDICATE` 独立审计（ineligible →
  block，unknown → warn 且点名缺失事实，无声明的方案 id ——包括模型幻觉
  的 id——→ `INDICATION_UNDECLARED` warn）。
* 立刻抓到了此前任何规则都看不见的错误：**PD-L1 TPS 20% 配帕博利珠单抗
  单药（KEYNOTE-024 要求 ≥50%）→ block**；鳞癌配培美曲塞骨架 → block；
  T-DXd 无既往治疗史 → unknown warn。
* **表↔声明一致性大扫描**入测试：27 例金标准全跑，planner 永不需要在
  自己的闸门上剔除自己的提案——决策表与声明同步生长、分歧即测试失败。

### Claim 级证据蕴含（v0.3.2，红队 §11）

引用护栏从「方案级 citation pool」升级为**逐主张的支持关系验证**——修复
红队指出的过宽背书：此前 cCRT 主张与奥希替尼巩固主张引用同一整袋证据，
LAURA 的行在给 cCRT 背书、RTOG-0617 的行在给奥希替尼背书。

* **Claim 带结构化 subject**：`{intervention_regimen_ids, population_stage,
  intent}` + `support_relation`（trial_anchor / protocol_grounded /
  population_statistic）。每个治疗选项的主张只引用**其自身方案的试验锚点
  行**（regimen↔trial 结构性蕴含，确定性判定）；无方案选项（手术/随访/
  缓和整合）标 `protocol_grounded`，不借用不属于它的试验行；预后主张标
  人群统计关系。跨并行波 temp-id 重映射与会话方案复用路径同样成立。
* **Critic 逐条验证**（`claim_guard`，与 plan 级护栏并行）：引用台账中
  不存在的证据 id → `CLAIM_DANGLING_EVIDENCE`；带方案的主张无可放行蕴含
  证据 → `CLAIM_UNSUPPORTED`；引了可放行证据但没有一条覆盖所声称的干预
  → `CLAIM_SUPPORT_MISMATCH`（「从别的主张借来的引用不是支持」）。
  模型路径的方案主张走同一验证——模型没为自己的选项调 `trial_lookup`
  就会被逐条点名。
* 27 例金标准大扫描：规则模式方案零 CLAIM_* 问题（诚实基线入测试）。

### 临床错误分类学评测 + 双医师裁定台账（v0.3.3，红队建议 #5/#6）

评测从「数对/数错」升级为**临床错误分类学仪表**——失败不再只是计数，
而是说清**发生了哪一类临床事件**：

* **九类错误分类学**：`major_harmful`（推荐里出现被禁方案/被禁措辞——
  方向性伤害）· `unsafe_release`（该拦没拦——安全护栏的核心指标，单独
  成率）· `overblocking`（该放没放——以可用性为代价的"安全"同样是错误）
  · `false_alarm` · `omission` · `missing_workup` · `incorrect_release` ·
  `staging_error` / `routing_error`（确定性内核出错，本架构下最严重的
  bug 类别）。每条失败带分类落报告，`error_taxonomy` 全零是金标准通过
  的必要条件。
* **审计型金标准病例：给安全网本身做金标准**。带 `audit_plan` 的条目
  跳过 planner，把**蓄意构造的错误方案**（或蓄意正确的对照）直接喂给
  规则引擎，以 `violations_required` / `violations_forbidden` 声明期望。
  红队的三枚探针——exon20ins 强推奥希替尼、ROS1+ 推帕博利珠单抗、
  TPS 20% 单药——从「埋在 pytest 里的断言」升格为一等指标：
  **`unsafe_release_rate` = 该拦未拦的比例，当前 0/10**（8 枚必拦探针
  + 2 枚不许误报的对照）。首次运行即抓到真实缺口：N3 手术探针因
  staging 缺 `n_category` 键而漏网——规则引擎已补 facts 描述符回退，
  该缺口现被金标准钉住。
* **双医师裁定台账**（`adjudicate` 子命令 + `eval/adjudication.py`）：
  面向 100–200 例边界病例集的人工裁定脚手架。追加式 JSONL，**永不
  last-wins**——每位裁定人的判定并存，分歧被点名而非覆盖（与 KG 复核
  台账的升级语义刻意相反：知识升级取最新，临床裁定保留分歧）。判定
  内容寻址绑定病例（病例一改，旧判定自动作废并列入 `void_after_case_change`）；
  `disagree` / `needs_revision` 必须给理由；`eval` 报告随附覆盖度
  （几例有双人裁定、分歧在哪几例）。裁定本身是人的工作——**出厂台账
  为空**，脚手架保证的是裁定过程的可审计与分歧的不可磨灭。

```bash
python -m nsclc_agent adjudicate --list          # 待裁定队列
python -m nsclc_agent adjudicate iv_egfr_first_line \
  --disagree --adjudicator "Dr. B (放疗科)" --notes "应并列 FLAURA2 选项"
python -m nsclc_agent adjudicate --status        # 覆盖度 + 分歧清单
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
python -m nsclc_agent eval                    # 37 例金标准（27 流水线 + 10 审计型安全网探针）

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
  safety/      emergencies.py 急症筛查(子句级否定) · rules.py 14条规则引擎
  interview/   axes.py 17条NSCLC问诊轴(VOI层) · adequacy.py · loop.py
  perception/  imaging.py 读片(词表校验/归一化交叉核对/拒绝文本模型)
  tools/       base.py Broker+熔断 · registry.py 11个工具 · retrieval.py 实连检索
  agents/      toolloop.py ReAct · planner.py · panel.py MDT · critic.py · catalog.py
  llm/         base.py · openai_compatible.py(tools+重试) · providers.py · mock.py
  prompts/     9个协议模块(.md, sha256钉版) · cores.py 蒸馏决策核心
  state.py 证据台账/预算/状态 · journal.py 记录/重放 · runner.py · render.py
  conversation.py 多轮会诊层(白名单抽取/方案指纹复用/出口剂量扫描)
  knowledge/guideline_kg.py 指南KG查询层(剂量深清洗/kg_llm_extracted定级/复核台账)
  knowledge/kg_eligibility.py 人群判据确定性求值(span优先/冲突弃权/推定不确信)
  knowledge/prognosis.py 分期队列生存表+方向性预后因素(试验锚定/不做个体预测)
  knowledge/data/guideline_kg.json.gz 六部指南2,960条推荐+147跨区域聚类
  eval/run_eval.py 错误分类学评测 · eval/adjudication.py 双医师裁定台账
  schemas.py · skills.py · case.py · cli.py
tests/         422 个用例，全离线    eval/       37 例金标准 + 指标
docs/ARCHITECTURE.md                 examples/   病例样例
```

## 测试与评测

```bash
pip install pytest
python -m pytest -q            # 422 passed，全离线
python -m nsclc_agent selftest # 分期引擎 43/43
python -m nsclc_agent eval     # 金标准 37/37：分期25/25 路由11/11 方案22/22
                               # 安全27/27 · unsafe_release_rate 0/10 · 分类学全零
```

## 仍未完成（诚实清单）

模型不能主动发起轮次；急症命中后累计病史会保守地持续触发急症通道（会话内
无降级路径，这是有意的）；PubMed/CT.gov 实连检索需操作者
显式开网（默认离线 stub）；内置指南 KG 为**机器抽取、默认未经临床复核**——
逐条复核工作流已就绪（`kg-review`，内容寻址绑定、可撤销），但复核本身
是人的工作，出厂台账为空、全部 2,960 条待复核；复核人身份**记录但不做
认证**（依赖操作环境的访问控制与台账的 git 审阅）；人群判据求值只覆盖
语义可恢复的七类（8,727 条判据中其余类型诚实弃权），且对未复核条目仅
标注排序、不作裁决；预后表编码的是第8版分期项目队列（2016 发表）的
**近似**数字——靶向/免疫时代同分期生存已系统性改善、第9版重新分组使同名
组不完全可比（均已逐格注明），且系统在任何层面都**不做个体生存预测**、
不给预后因素配数字权重；重放日志证明"重放与记录一致"，不证明"记录未被
篡改"（需存储层签名）；claim 级蕴含是**结构性** regimen↔trial 匹配而非
语义蕴含（subject 的 population/comparator 字段仍粗、证据文本内容未做
语义核对）；schema 校验仍刻意保持浅层（形状
校验，非临床语义完备性）；治疗库（30 方案/30 试验/14 规则+30 适应证声明）覆盖主干驱动
通路但仍是教学规模，未覆盖后线序贯、CNS 转移分层、器官功能剂量调整与
药物相互作用决策；金标准 37 例（27 流水线 + 10 审计型探针）仍远少于严肃
临床验证所需的 100–200 例边界病例集——双医师裁定台账已就绪（分歧并存、
内容寻址作废、覆盖度入 eval 报告），但**裁定本身是人的工作，出厂台账为
空**，且裁定人身份**记录而不认证**（依赖操作环境访问控制与台账 git 审阅）；
审计型探针只审规则引擎这一层（planner 的独立防线由流水线病例与单元测试
覆盖）；图内并发只覆盖 Treatment∥Panel 波与会诊成员（其余
任务串行）；内置试验注册表
与 DDI 规则包是教学语料，须经本机构药师/医师复核后使用；大规模对抗性安全
评测未做。**本项目不能对外宣称为临床可用系统。**

## License

MIT — see [`LICENSE`](LICENSE)。原型压缩包（`NSCLC-Agent-main.zip`、v0.1）
与审核报告（`CODE_REVIEW.md`）保留在仓库中作为演进记录。
