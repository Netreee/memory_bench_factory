# Redesign V9 — 在 V8 主路上补三个控质量关键件

> V8 主路(LLM answer-first 出题 + 升级 judge)已走通:重跑 12/12 通过、覆盖全恢复、题质量在线。
> 但两轮"文献精华吸收度"审查一致指出 V8 漏了三个让题"有难度、真考记忆、可证伪"的关键件。
> V9 **只在 V8 上加这三件,不动主路**;审查时独立评 V9 产物。
>
> Stage 0/A/B/C/D 与 redesign_v8.md **完全一致**(refine→维度→能力规划→FactGraph→多文档 corpus),
> 本文档只详写改动的 **Stage E(多步 exploration)** 与 **Stage F(硬判别器 + judge 多票)**。

## V8 → V9 改动总览

| | V8 | V9 |
|---|---|---|
| Stage E 出题 | LLM 一次性看【全部语料】answer-first 出题 | **DocDancer 式多步 exploration**:agent 用 search/read 工具逐步收 evidence,再 answer-first 合成(出跨多文档的深题) |
| Stage F 闸(b) | (已弃用,空着) | **★ 硬判别器**:把题喂"无语料/单文档"baseline,能答中→reject(保有效性、可证伪记忆必要性) |
| Stage F 闸(c) judge | 单次 LLM judge | **self-consistency 多票**(3 票 temp≈0.3 多数表决,治单 judge 两头摆) |

---

## Stage E V9 — 多步 exploration + answer-first(改)

- **输入**:Corpus(多 session 多文档) + FactGraph + CapabilityPlan
- **输出**:`list[RawQuestion]{ question, answer, field, capability, hops, evidence_spans }`(同 v8 字段)
- **谁**:LLM agent(ReAct 式多步循环)+ 代码工具
- **代码工具(2 个,在 corpus 上)**:
  - `search(keyword) -> [{doc_id, session, snippet}]`:在所有文档 content 里关键词检索,返回命中片段(代码实现:遍历 corpus 文档做子串/模糊匹配)
  - `read(doc_id) -> content`:返回该文档全文
- **逻辑(每出一道题,跑一轮 ReAct,max_steps≈4)**:
  1. 给 LLM 当前能力(如 KU)+ 已收集的 observation 历史 + 可用工具
  2. LLM 输出一步 `action`:`{tool:"search"|"read"|"synthesize", arg:...}`
  3. 若 `search/read` → 代码执行、把结果追加进 history,继续下一步(★ 渐进式:下一步基于上一步看到的)
  4. 若 `synthesize` → LLM 给出 answer-first 的题(此时它已跨多文档收集了 evidence)→ 本题结束
  5. 达到 max_steps 仍未 synthesize → 强制 synthesize
- **为什么**:V8 一次性整坨喂语料,LLM 就近取 1-2 篇 → 题浅。多步探索强迫它跨 session/跨文档串联,天然出深题、自然提升 hops。
- **prompt 要点(系统)**:"你是记忆出题 agent。用 search/read 工具【多步探索】语料,围绕指定能力收集【跨多个 session/文档】的证据;证据够了再 synthesize 一道题(answer-first:先据证据定 answer,再写自然问题)。每步只输出一个 action 的 JSON。约束同 v8:题必须跨 session/多文档才能答、answer 被 evidence 支持、evidence_spans 逐字原文。"
- **action JSON**:`{"thought":"...","tool":"search","arg":"负责人"}` 或 `{"tool":"read","arg":"s4_通"}` 或 `{"tool":"synthesize","question":"...","answer":"...","field":"...","hops":2,"evidence_spans":[{"doc_id":"...","span":"..."}]}`
- **示例轨迹(KU 题)**:
  ```
  step1 {tool:search, arg:"负责人"} → [{s0_通,"负责人张三"},{s2_通,"变更为李四"},{s4_通,"现任王五"}]
  step2 {tool:read, arg:"s4_通"}    → "...截至2月3日 现任负责人王五..."
  step3 {tool:synthesize, question:"历经多次变动,目前负责人是谁?", answer:"王五",
         field:"负责人", hops:2, evidence_spans:[{doc_id:"s4_通",span:"现任负责人王五"}]}
  ```

## Stage F V9 — 三道闸(grounding + ★硬判别器 + judge 多票)

- **输入**:list[RawQuestion] + Corpus + FactGraph
- **输出**:`list[Question](通过) + reject_log`(每题带 `memory_necessity`、`judge_votes`)
- **谁**:代码(闸 a) + LLM(闸 b、c)

### 闸 (a) grounding —— 代码(同 v8)
evidence_spans 模糊匹配在不在语料(θ=0.85);不在→reject(幻觉证据)。ABS 题无 evidence,跳过。

### ★ 闸 (b) 硬判别器 —— LLM(放弃结构裁判腾空的位置)
**反事实:能不靠记忆答中的题,对 memory 评测无效,reject。**
- **模式 1(无语料)**:只给 `question`,不给任何语料,让 teacher 答 `a_blank`。若 `match(a_blank, answer)` → reject(常识/题面泄漏可答)
- **模式 2(单文档)**:只给 evidence_spans 命中的【那一篇】文档,让 teacher 答 `a_single`。若 `match(a_single, answer)` → reject(单文档可答,不需跨文档记忆)
- 两模式都答不中 → 通过,记 `memory_necessity="pass"`;否则记 `fail_commonsense / fail_single_doc`
- ABS 题特例:其"答案"是拒答,无语料时 teacher 若也拒答不算"答中"(继续看是否真问了不存在字段)
- **prompt 要点**:teacher 用一个强模型,prompt = "只依据(给定/无)上下文回答,不会就说不知道,答案极简"
- **示例**:Q"目前负责人是谁→王五":模式1(无语料)teacher 答"不知道"✓、模式2(只给 s4_通)teacher 答"王五"→ **fail_single_doc → reject**(它只需读最新一篇,不算跨 session 记忆)。← 这正是硬判别器要抓的"伪记忆题"

### 闸 (c) judge —— LLM self-consistency 多票
- judge(读字段真值轨迹,判 answer_correct/needs_memory/well_formed,prompt 同 v8 升级版)跑 **K=3 次,temp≈0.3**
- 多数表决:≥2 票 pass → pass;否则 reject。记 `judge_votes`
- 治 v8 单 judge 两头摆(上版误杀 TR/ABS、这版 reject 0 疑似太松)

### 通过条件
grounding 通过 **且** 硬判别器 pass **且** judge 多票 pass。

---

## §保留(V8 已对的,不动)
answer-first 思路、FactGraph(judge 真值参考)、judge 三准则与校准(别误杀 TR/ABS、专名不算外部知识、ABS 放行)、grounding、多文档 corpus、种子扰动、能力规划取代硬编码 profile。

## §仍未做(V9 后)
难度旋钮(Layer-wise 同答案扩展——多步 exploration 部分替代)、信息混淆、时空一致性代码校验、人验抽样校准(论文可信度门槛,V9 后补)。

## §任务分解(实现顺序)
- T1 Stage E V9:search/read 工具(代码)+ ReAct 多步出题循环
- T2 Stage F V9:闸(b) 硬判别器(无语料/单文档反事实)+ 闸(c) judge 多票
- T3 run_pipeline_v9 端到端 + 跑产物(对照 V8 看:hops 是否变深、伪记忆题是否被硬判别器抓出、judge 多票是否稳)
