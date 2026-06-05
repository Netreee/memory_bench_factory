# Redesign V8 — 详尽 Pipeline 规格(可照此实现,无需再问)

> V8 与 V7 方向不同(不是迭代),审查时**独立评 V8 作为"memory benchmark 生成器"好不好**,
> 不拿 V7 当参照。核心原则:**生成放手让 LLM 当主角,gt 靠裁判在【校验层】保(不在生成层)。**
> 调研锚点见 docs/literature/(DocDancer 2601.05163 / WebShaper 2507.15061 / Mem2Act 2601.19935 /
> LifeBench 2603.03781 / AutoBencher / YourBench)。

## 核心数据流(8 步,贯穿示例 = AI 工程周报场景)

```
0 Refine → A Dimensions → B CapabilityPlan → C FactGraph → D Corpus
  → E Answer-first 出题 → F 三道校验闸 → G 打包
```

设计铁律:
- **不 mock**。Stage 0/A 用真 LLM 调 prompt。每步若无显式输入,默认 = 上一步输出。
- **LLM 是主角**(0/A/B/C/D/E 都是 LLM 合成);代码只在 F 的硬校验 + G 打包。
- **裁判在 F**,不在 E。E 放手出题,F 严格过滤。

---

## Stage 0 — Refine(细化场景)

- **输入**:`RawScenarioInput{ description: str(模糊场景描述), corpus_samples: list[Document](few-shot,无QA) }`
- **输出**:`RefinedScenarioSpec{ name, description_refined, corpus_samples, key_fields, subject, perspective }`
- **谁**:LLM
- **prompt 要点**:"你是场景分析师。给定模糊场景描述 + few-shot 文档,产出:① 一句清晰的 description_refined;② 推断本场景值得长期追踪的 key_fields,每个标 `stable/evolving` + 类型(人名/数值/状态);③ 记忆主体 subject、评测视角 perspective。只依据输入,不编造领域外设定。严格 JSON。"
- **输出示例**:
```json
{ "description_refined": "AI 工程团队周度迭代追踪,业务线 leader 视角",
  "key_fields": [
    {"name":"P0缺陷率","type":"evolving","value_type":"数值百分比"},
    {"name":"Oncall数量","type":"evolving","value_type":"整数"},
    {"name":"负责人","type":"evolving","value_type":"人名"},
    {"name":"项目名","type":"stable","value_type":"专名"}],
  "subject":"project", "perspective":"cross_line_leader" }
```

## Stage A — Dimensions(维度推断)

- **输入**:RefinedScenarioSpec
- **输出**:`ScenarioDimensions{ I_ingest_channels, S_memory_subject, V_perspective, T_temporal_pattern, cognitive_flavor }`
- **谁**:LLM(★ 真 LLM,不走启发式 fallback)
- **prompt 要点**:复用 v7 的 `LLM_INFER_SYSTEM`(给定 5 维取值范围,综合 corpus 的 doc_type/content + description 推断)。注意:不再把答案当 hint 喂——让 LLM 真推。
- **输出示例**:`{ I:["weekly_report"], S:"project", V:"cross_line_leader", T:"weekly", cognitive_flavor:"semantic-heavy" }`

## Stage B — CapabilityPlan(能力规划,取代硬编码 profile)

- **输入**:dims + spec
- **输出**:`CapabilityPlan{ items:[{capability, n, rationale}], total }`
- **谁**:LLM(★ 取代 V7 的 5 个硬编码 profile,改 LLM 按场景规划 → 可泛化到任意域)
- **prompt 要点**:"给定场景维度,规划本 memory benchmark 覆盖哪些记忆能力、各几题。能力清单(红海五件套必覆盖):① 信息抽取 IE ② 多session综合 MR ③ 时序推理 TR ④ 知识更新 KU ⑤ 拒答 ABS;蓝海可选:冲突消解 / 遗忘 / 失败归因 / procedural。按本场景特点(如周报 KU/TR 重)分配,给 rationale。总数 = target_size。严格 JSON。"
- **输出示例**:
```json
{ "items":[{"capability":"KU","n":3,"rationale":"周报字段跨周更新,知识更新是核心"},
           {"capability":"TR","n":2,"rationale":"缺陷率随周演化,考变化时机"},
           {"capability":"MR","n":3,"rationale":"跨周聚合(最高/最低)"},
           {"capability":"IE","n":3},{"capability":"ABS","n":1}], "total":12 }
```

## Stage C — FactGraph(时序事实演化图)★ gt 的裁判参照系(非出题模具)

- **输入**:spec + dims + plan
- **输出**:`FactEvolutionGraph`(结构见下)
- **谁**:LLM
- **逻辑/prompt 要点**:"为本场景设计一个【时序事实演化图】,作为 benchmark 的 ground-truth 世界。定义:实体、字段(stable/evolving)、N 个 session(带日期)、每个 session 每个字段的当前值、以及演化事件(某字段何时从 X 变 Y)。★ 值要自然合理、是该场景正常值(种子扰动:不照搬 few-shot 里的具体人名/数值,换一套新的);evolving 字段要有真实演化轨迹;为支持冲突/abstention,可显式标注某字段在某 session 被推翻、某字段全程不存在。严格 JSON。"
- **数据结构**:
```json
{ "entities":[{"id":"proj1","name":"智策平台","type":"project"}],
  "fields":[{"name":"负责人","type":"evolving","value_type":"人名"},
            {"name":"P0缺陷率","type":"evolving","value_type":"数值"},
            {"name":"项目名","type":"stable"}],
  "sessions":[
    {"session_id":0,"date":"2025-04-17","state":{"负责人":"王建国","P0缺陷率":"20%","项目名":"智策平台"}},
    {"session_id":1,"date":"2025-04-24","state":{"负责人":"王建国","P0缺陷率":"17%","项目名":"智策平台"}},
    {"session_id":4,"date":"2025-05-15","state":{"负责人":"李秀英","P0缺陷率":"8%","项目名":"智策平台"}}],
  "evolutions":[{"field":"负责人","from":"王建国","to":"李秀英","at_session":4,"type":"update"}],
  "absent_fields":["个人邮箱","项目预算"] }
```
- 角色提醒:**这是裁判的参照系,F 阶段用它机械算时序/计数题 gt;它不是 E 阶段的出题模具。**

## Stage D — Corpus(语料合成)

- **输入**:FactEvolutionGraph
- **输出**:`Corpus{ sessions:[{session_id, date, docs:[{doc_id, doc_type, content, fact_refs}]}] }`
- **谁**:LLM
- **逻辑/prompt 要点**:"基于事实图某 session 的 state,合成 2-3 篇【异质文档】(周报=数值指标 / 通报=人事状态 / 邮件),把该 session 的 facts 自然叙述进去、信息分散到多篇。★ 每篇标注 `fact_refs`(它叙述了哪些字段的值)供后续 grounding。★ 每篇正文必须含该 session 的日期锚点(如'2025-04-17 周报');演化字段只写当前 session 值、不回顾不展望。严格 JSON。"
- **输出示例**:
```json
{"session_id":0,"date":"2025-04-17","docs":[
  {"doc_id":"s0_报","doc_type":"weekly_report","content":"【2025-04-17 周报】本周 P0 缺陷率 20%,Oncall 10 起...","fact_refs":["P0缺陷率","Oncall数量"]},
  {"doc_id":"s0_通","doc_type":"personnel_update","content":"【2025-04-17 通报】现任负责人王建国...","fact_refs":["负责人"]}]}
```

## Stage E — Answer-first 出题(LLM 主角)★ V8 核心

- **输入**:Corpus + FactGraph + CapabilityPlan
- **输出**:`list[RawQuestion]{ question, answer, evidence_spans:[{doc_id, span}], capability, hops }`
- **谁**:LLM(★ 主角,自由出题面)
- **逻辑/prompt 要点(answer-first)**:"你是记忆评测出题专家。探索给定多 session 语料。针对指定能力(如 KU 知识更新),**先在语料里定位证据链(evidence_spans:引用哪些 doc 的哪些原文片段)→ 据证据确定答案 answer → 再据答案写一个自然口语的问题 question**。硬约束:① 问题必须依赖记忆(跨 session / 需追踪演化 / 多文档聚合),单看一篇文档答不出;② answer 必须被 evidence_spans 直接支持,不能靠常识猜;③ evidence_spans 必须是语料里的【原文片段】(供机器核对)。难度:从单跳到多跳(hops 标注)。严格 JSON 输出题数组。"
- **输出示例**(KU 题):
```json
{ "question":"截至最新一期,智策平台的负责人是谁?", "answer":"李秀英", "capability":"KU", "hops":1,
  "evidence_spans":[{"doc_id":"s4_通","span":"现任负责人李秀英"},
                    {"doc_id":"s0_通","span":"现任负责人王建国"}],
  "reasoning":"负责人从王建国(s0-3)更新为李秀英(s4),问最新值" }
```

## Stage F — 三道校验闸(裁判在这里)

- **输入**:list[RawQuestion] + Corpus + FactGraph
- **输出**:`list[Question](通过) + reject_log + judge_conflict_log`
- **谁**:代码(a,b)+ LLM(c)
- **闸 (a) grounding —— 代码**:逐个 evidence_span 在 `Corpus[doc_id].content` 里做模糊匹配(Levenshtein partial_ratio ≥ 0.85)。任一 span 不在原文 → reject(幻觉证据)。
- **闸 (b) 可执行验证 —— 代码(★ 同时是"结构当裁判"的可证伪实验)**:若 capability ∈ {KU, TR, MR-聚合, 计数},从 FactGraph 用代码确定性算 gt(KU=末 session 值;TR=evolutions 里变点;MR=对 states argmax/min)。
  - 代码算的 `gt_code` 与 LLM 的 `answer` **一致** → 通过且 gt 双重确认;
  - **不一致** → 记入 `judge_conflict_log`(★ 这正是检验"结构裁判 vs LLM 主角"谁错的数据;冲突率高 = 任子谦的怀疑成立)。冲突时**默认信代码 gt 并标记待人验**,不直接丢。
- **闸 (c) judge —— LLM(GPT-5 Teacher)**:输入 question+evidence+answer,判三点:① evidence 能否唯一推出 answer ② 是否 context-free(不靠题面外常识) ③ 是否真考记忆(非单文档可答)。任一不过 → reject。
- **输出示例**:`通过 9 / 12,reject 3(grounding×1 幻觉证据 / judge×2 单文档可答),judge_conflict_log: KU 题 0 冲突、MR 题 1 冲突(代码算周期1、LLM 答周期2 → 信代码、标人验)`

## Stage G — 打包

- **输入**:通过的 Question + dims/plan/graph 快照
- **输出**:`benchmark_extended.json`(全标签)+ `benchmark_oflat.json`(OfficeMem 兼容)
- **谁**:代码(同 V7 的 save_benchmark / save_benchmark_officemem_compat)
- 额外存:每题的 `evidence_spans`、`gt_source`(code/llm/human-pending)、`judge_conflict` 标记 → 供审查与人验抽样。

---

## §裁判怀疑(任子谦)→ 已设计成可证伪

"结构能否当好裁判"不作前提:F-(b) 把"代码(结构)算的 gt"与"LLM 出的 answer"逐题对照,**冲突率就是答案**——
冲突低 → 结构裁判可靠;冲突高 → 怀疑成立、弃用结构裁判改信 grounding+judge+人验。再加最终人验抽样(对标
Mem2Act/LifeBench 90%+)给每道闸测准确率。**先试,但用数据决定信不信它。**

## §定位

memory × 生成器交叉点(竞品 Mem2Act/LifeBench 是固定 benchmark、YourBench/AutoBencher 是通用 QA 生成器,
无人在交叉点)。novelty = 产品是"吃任意场景+few-shot 的 memory benchmark 生成器",非方法新。

## §任务分解(实现顺序)

- T1 schema:RefinedScenarioSpec(扩 key_fields)、CapabilityPlan、FactEvolutionGraph、RawQuestion(带 evidence_spans)
- T2 Stage 0/A/B(真 LLM,不 mock)
- T3 Stage C FactGraph 合成
- T4 Stage D Corpus(多文档 + fact_refs + 日期锚点)
- T5 Stage E Answer-first 出题(核心)
- T6 Stage F 三道闸(grounding 代码 + 可执行 + judge)+ 冲突日志
- T7 run_pipeline_v8 端到端 + 跑出产物
