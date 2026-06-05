# V10 Pipeline（as-built 确切实现）

> 这份文档记录 V10 **实际跑通的那条 pipeline**（不是设计意图——设计意图见 `redesign_v10.md`）。
> 每个 step 写清:**输入 / 谁干活 / 执行逻辑 / 输出 / 真实示例 / 代码出处**。
> 真实示例取自 `output/v10_benchmark.json`(office 多部门周报场景, 跑于 2026-06-01)。
> **§9 老实标注了 as-built 与设计意图的出入**(Stage E 没用 exploration、F 只有 3 闸、KU/CONFLICT 取舍等)。

---

## 0. 顶层数据流（DAG）

```
[模糊场景描述 + few-shot 文档, 无 QA]
  │
  ▼ Stage 0 Refine ──▶ spec
  ▼ Stage A Dimensions ──▶ dims (I/S/V/T)
  ▼ Stage B CapabilityPlan ──▶ plan (能力×题数)
  ▼ Stage C World ──▶ WorldState(多实体状态机, gt 烘焙在此)
        ├──▶ 点菜 generate_orders ──▶ orders[ ]  (代码: 从世界枚举 gt 已定的订单)
        └──▶ Stage D Render ──▶ corpus(多 session 多文档)
                                   │
        orders × corpus ──▶ Stage E Synthesize ──▶ raw_questions[ ]
                              ▼ Stage F Validate(3 闸) ──▶ passed[ ] + reject_log
                              ▼ Stage G Validity ──▶ 难度/区分度/排名/FAMA
                              ▼ 打包 ──▶ output/v10_benchmark.json
```

**核心原则**:gt(标准答案)由 **Stage C 的状态机用代码机械算**,不经 LLM。LLM 只干两件事——填世界素材(C)、把订单写成自然题(E)。

---

## 地基A — 状态机数据结构（`pipeline/world_state.py`, T1, 纯代码可单测 30/30）

记忆世界 = 每个 **(实体, 字段)** 一条**时间线**, 时间线 = 一串 **op**:

```python
Op  = {session:int, date:"YYYY-MM-DD", op:"SET|UPDATE|DELETE|EXPIRE", value:str|None, prev:str|None}
Timeline = [Op, ...]                      # 按 (date,session) 排序
WorldState = {entities:{实体:{字段:Timeline}}, cascades:[...], absent_fields:[...], n_sessions:int}
```

- **op 四类**(= Memora state-diff 三 op = MEME Cas/Abs/Del): `SET/UPDATE`(改值=Cas) / `DELETE`(Del) / `EXPIRE`(停统计、无替代值=Abs→遗忘)
- **切片** `Timeline.value_at_session(s)` / `value_at(date)`:从头按时间 fold 到 s, 返回当时有效值(值持续不变也能 fold 到)、或 `INVALID`(被 DELETE/EXPIRE)、或 `INSUFFICIENT`(还没出现)

**真实示例**(AI工程部.P0缺陷率):
```json
[{"session":0,"date":"2025-01-06","op":"SET","value":"2.5%","prev":null},
 {"session":2,"date":"2025-01-20","op":"UPDATE","value":"1.8%","prev":"2.5%"},
 {"session":4,"date":"2025-02-03","op":"EXPIRE","value":null,"prev":"1.8%"}]
```

## 地基B — 能力↔op 的 gt（`world_state.py` gt_* 函数, 单一口径机械算）

| 能力 | 函数 | 算法 | gt 形状(真实示例) |
|---|---|---|---|
| IE 信息抽取 | `gt_ie`/locate | 扫全程, 某值出现在哪几 session | `{"value":"5","sessions":[0,1]}` |
| MR 多跳聚合 | `gt_mr` | `reduce(values,max/min)` + **argmax/argmin** | `{"value":"3.1%","session":0,"agg":"max"}` |
| TR 时序 | `gt_tr` | 找 value 变化的 op | `{"session":3,"date":"2025-01-27","from":"陈强","to":"刘洋"}` |
| KU 最新值 | `gt_ku` | `timeline.latest_valid()` | `"李娜"` |
| CONFLICT 冲突 | `gt_conflict` | 同字段跨 session 出现 ≥2 不同值(★弱定义) | `{"conflict":true,"values":[...]}` |
| FORGET 遗忘 | `gt_forget` | 切片若 INVALID(被 EXPIRE) | `{"forgotten":true}` |
| ABS 拒答 | `gt_absent` | 字段 ∉ 任何 timeline | `INSUFFICIENT_EVIDENCE` |

---

## Stage 0 — Refine（模糊场景 → 结构化 spec）

- **输入**:`description`(自然语言场景描述) + `corpus_samples`(few-shot 文档, 无 QA)
- **谁**:LLM(`REFINE_SYSTEM`)
- **逻辑**:抽取本场景值得长期追踪的 `key_fields`(标 stable/evolving + value_type)、记忆主体、评测视角
- **输出**:`spec = {description_refined, key_fields:[{name,type,value_type}], subject, perspective}`
- **示例**:`key_fields=['P0缺陷率','Oncall数量','负责人','汇报对象']`
- **出处**:`pipeline/stages_v8.py:stage_0_refine`(V10 复用 V8)

## Stage A — Dimensions（场景维度推断）

- **输入**:spec + corpus_samples(包装成 `RefinedScenarioSpec`)
- **谁**:LLM(`use_llm=True`)
- **逻辑**:推断 4 个场景维度 I(摄入通道)/S(记忆主体)/V(视角)/T(时间模式)
- **输出**:`dims`(dataclass), 关键字段 `S_memory_subject`/`T_temporal_pattern`
- **示例**:`S=team, T=weekly`
- **出处**:`pipeline/stage_a_dimensions.py:infer_dimensions`(复用)

## Stage B — CapabilityPlan（能力配额规划）

- **输入**:spec + dims + `target_size`
- **谁**:LLM(`PLAN_SYSTEM`)
- **逻辑**:按场景给【红海五件套 IE/MR/TR/KU/ABS + 蓝海 CONFLICT/FORGET】分配题数, sum=target_size
- **输出**:`plan = {items:[{capability,n,rationale}], total}`
- **示例**:`{KU:4, TR:3, MR:2, IE:2, ABS:2, CONFLICT:1}`(target=14)
- **出处**:`pipeline/stages_v8.py:stage_b_capability_plan`(复用)

## Stage C — World（多实体状态机世界, ★gt 烘焙在此）

- **输入**:spec + dims + plan + `n_entities`(≥3) + `n_sessions`(6)
- **谁**:**LLM 填世界表(素材)** + **代码 assemble(编排 op、套级联、配日历、校验)**
- **逻辑**:
  1. LLM(`WORLD_SYSTEM`)输出"每实体每字段的取值表"(stable 给单值 / evolving 给 trajectory; 末尾 value=null 表停统计; cascades 显式预声明效果值; absent_fields)。★严禁写题/答案/"最新当前"措辞
  2. 代码 `assemble_world`:把 trajectory **diff 成 ops**(首现=SET、变值=UPDATE、null=EXPIRE)、套 cascades(注入前查重防重复 op)、配统一日历(session×7 天)、校验(演化字段须≥2 值、absent 不得与真字段重名)
- **输出**:`(WorldState, table原始, issues)`
- **示例**:3 实体 / 15 字段 / 6 周 / 2 级联 / absent=['部门预算','员工满意度']; `AI工程部.P0缺陷率: SET 2.5%@s0 → UPDATE 1.8%@s2 → EXPIRE@s4`
- **出处**:`pipeline/stages_v10.py:stage_c_world_v10` + `world_state.py:assemble_world`

## 点菜 — generate_orders（从世界枚举 gt 已定的订单, ★纯代码）

- **输入**:WorldState + `plan_req`(能力→题数)
- **谁**:代码(`order_gen.py`)
- **逻辑**:对每个能力, 枚举所有合格 (实体,字段), 用地基B 的 gt_* 算出答案, 打包成订单。**★每张非 ABS 订单 `evidence_sessions` 必 ≥2(天生跨文档 → 单篇答不出)**
- **输出**:`orders=[Order{capability,entity,field,gt,evidence_sessions,question_date,aux}]`
- **示例**:`Order(TR, 数据平台部, 负责人, gt={session:3,from:陈强,to:刘洋}, evidence_sessions=[0,3])`
- **出处**:`pipeline/order_gen.py:generate_orders`(T2, 单测 28/28)

## Stage D — Render（WorldState → 多 session 多文档；★mention-on-change 防剧透 + 草堆 filler）

- **输入**:WorldState
- **谁**:LLM(`CORPUS_SYSTEM_V10` 证据 + `FILLER_SYSTEM` 干扰, 每 session 一次) + 代码(剧透扫描/重写、字段黑名单)
- **逻辑**:
  1. **★证据渲染(mention-on-change)**:每周【只渲染本周发生变更(有 op)的字段】、之后不复述 → "现在 X 是多少"必须跨周回忆(根治弱 KU/IE)。EXPIRE 当周写"暂停统计"、之后不提。
  2. **写实**:每篇 200–400 字真实叙述, 只围绕给定字段、不编造其他指标。禁"当前/最新/现任/维持"全局口径。
  3. **剧透闸(代码,真闸)**:扫 `LEAK_BANNED`, 命中 → 点名重写(≤1 次重试)而非只记日志。
  4. **★草堆 filler**(`stage_d_add_filler_v10`):每周(含无变更的空周)加 N 篇杂项干扰文档(团建/培训/公告/纪要…), 扫【字段名黑名单】, 碰到被追踪字段一律丢(防泄漏)。把"说一次的事实"淹进草堆 → 才真考记忆(导师:要草堆+干扰,不要极端大海捞针)。
- **输出**:`(corpus, leak_log)`;doc 带 `is_filler` 标记
- **示例(3实体/6周)**:证据 17 篇(★剧透命中 0)+ 草堆 18 篇 = **35 篇 / 9148 字(干扰占比 53%)**;session 1 只有"算法研究部 Oncall=4"一个变更 → 该周只渲染这一项, 其余靠 filler 填
- **出处**:`pipeline/stages_v10.py:stage_d_corpus_v10` + `stage_d_add_filler_v10`

## Stage E — Synthesize（fact-first 直接合成, gt 来自订单）

- **输入**:orders + corpus
- **谁**:LLM(`QUESTION_SYSTEM_V10`)
- **逻辑**:对每张订单, 给 LLM【已确定答案 + 涉及周 + 能力 + 文档片段】, 让它写一道【必须读齐这些周、题面不泄漏答案】的自然问题。**答案直接取订单 gt, LLM 不碰**。
  - 能力句式:**IE=时点切片"在第N周X是多少"**(答案=那周的中间值, 考 recency 抗性)/ KU="截至最新X是多少" / TR="X首次变化在哪周"(只问周) / MR="最高/最低" / CONFLICT="各期是否一致" / FORGET="截至最新X是多少"(答案=已暂停) / ABS=问不存在字段
  - ★ 题/答错位不靠 prompt 死磕, 交给 Stage F 的「题答自洽」通用闸兜底(见下)
- **输出**:`raw=[{question, answer, capability, entity, field, evidence_sessions, gt, aux}]`
- **示例**:`{"question":"在第1周,AI工程部Oncall数量是多少?","answer":"5","capability":"IE"}`
- **出处**:`pipeline/stages_v10.py:stage_e_synthesize_v10`

## Stage F — Validate（★4 道通用闸,逐题过, 任一不过即 reject）

- **输入**:raw + corpus + WorldState
- **谁**:代码(闸a) + LLM(闸b/c/d)
- **四闸**:
  - **(a) grounding(代码)**:gt 支撑值是否真在【证据周】文档里(防渲染丢值)
  - **★(b) 题答自洽(LLM,通用)**:给【全部证据周】+题,强模型作答须 = gt(同 Stage G 的 LLM-judge 口径)。对不上 = 题问的根本不是 gt(畸形题)→ reject。**一根逻辑覆盖所有能力, 替掉 per-capability 码层特判**(IE"哪几周=5→答第0、1周≠gt5"自动毙)
  - **(c) 硬判别器(LLM)**:无语料 / 单证据周 baseline 答中 → reject 伪记忆(`fail_commonsense`/`fail_single_doc`)
  - **(d) judge_wf(LLM)**:只判题面良构 + 不泄漏(★不判 answer 对错——那是代码的)
  - ★(b)+(c) 正好夹出"有证据答得对 ∧ 没记忆答不出" = 真·有效记忆题
- **输出**:`(passed, reject_log)`, passed 题加 `memory_necessity` + `gt_source="状态机 state-diff(代码)"`
- **示例(草堆版)**:通过 12/16;reject = `oracle_consistency×3`(题答自洽抓畸形题)+ `judge_wf×1`;**KU 4/4 全过**(mention-on-change 后强 KU 成立, 伪记忆清零)
- **出处**:`pipeline/stages_v10.py:stage_f_validate_v10`

## Stage G — Validity（有效性闭环, baseline 作答矩阵）

- **输入**:passed questions + corpus
- **谁**:LLM(5 个 baseline 作答 + LLM-judge 判分) + 代码(统计)
- **逻辑**:
  1. 5 个**上下文消融 baseline** 当"能力不同的模型":`oracle_full / recency_last2 / first_half / random_1 / no_context`
  2. 每个 baseline 用其上下文答每题 → **LLM-judge 语义等价判分**(`GRADE_SYSTEM`, ★治 M5 格式错位:日期↔周次、中文↔阿拉伯数字、FORGET 的"已暂停/不知道"算对、给旧数值算错)→ 矩阵 `R[model][item]∈{0,1}`(原始答案一并存盘)
  3. 算:**难度**(平均答对率)、**区分度**(point-biserial: 本题 vs 总分去本题; 负=坏题)、**模型排名**(validity sanity: oracle 应居首/no_context 垫底)、**FAMA**(`max(0,MPA−λ(1−FAA))`, present=非FORGET项/absence=FORGET项)
- **输出**:`validity = {matrix, raw_answers, difficulty[], discrimination[], model_accuracy, model_ranking, fama, bad_items, validity_sane}`
- **示例(判分地板修复后, 真实)**:oracle **0.90** 居首、no_context **0.25** 垫底 → **`validity_sane=True`**;
  MR/IE/CONFLICT/FORGET 区分度 **+0.37~+0.93**(好题)、ABS 区分=**N/A**(校准项不计)、**负区分坏题 0**、**FAMA=0.875**
- **出处**:`pipeline/stages_v10.py:stage_g_validity_v10`

---

## 最终产物 schema（`output/v10_benchmark.json`）

```jsonc
{
  "world_state": {entities, cascades, absent_fields, n_sessions},   // 真值账本(可回溯)
  "corpus":      {sessions:[{session_id, date, docs:[...]}]},        // 多文档语料
  "questions":   [{question, answer, capability, entity, field,      // benchmark 本体
                   evidence_sessions, gt, aux, memory_necessity, gt_source}],
  "reject_log":  [{question, answer, capability, gate, reason}],     // 被哪道闸拒
  "validity":    {matrix, raw_answers, difficulty, discrimination,   // 有效性闭环
                  model_accuracy, model_ranking, fama, bad_items, validity_sane}
}
```
**一道完整题(真实)**:
```json
{"question":"数据平台部的负责人在哪一周发生了变化？","answer":"第3周(2025-01-27)",
 "capability":"TR","entity":"数据平台部","field":"负责人","evidence_sessions":[0,3],
 "gt":{"session":3,"date":"2025-01-27","from":"陈强","to":"刘洋"},
 "memory_necessity":"pass","gt_source":"状态机 state-diff(代码)"}
```

## 跑法（runners）

```bash
# 纯代码地基自检(无 LLM)
./venv/bin/python pipeline/world_state.py     # 30/30
./venv/bin/python pipeline/order_gen.py       # 28/28
# 真 LLM 分段冒烟(产物可逐段回溯)
./venv/bin/python tools/v10_stage_c_smoke.py  # 0→A→B→C + 点菜
./venv/bin/python tools/v10_filler_smoke.py   # 复用世界 → 写实证据(mention-on-change)+ 草堆 filler
./venv/bin/python tools/v10_efg_smoke.py      # 复用世界+草堆语料 → E+F(4闸)+G(完整 benchmark)
./venv/bin/python tools/v10_regrade.py        # 只重跑 Stage G(空答案鲁棒 + LLM-judge 判分)
./venv/bin/python tools/v10_restat.py         # 纯代码:从存盘矩阵即时重算统计(无 LLM)
```

---

## §9 as-built 与设计意图的出入（老实标注）

| 项 | redesign_v10 设计意图 | as-built 实际 | 为什么 |
|---|---|---|---|
| Stage E 出题 | 复用 V9 exploration(ReAct search/read) | **直接合成**(订单已锁定 gt+证据, 跨文档由结构保证) | 探索是"无状态机时硬找证据"的补丁, 有了点菜就冗余且不稳 |
| Stage F 闸数 | 4 闸(+trivial-pass MEME before-question) | **4 闸**(grounding/**题答自洽**/硬判别器/judge_wf) | 用通用「题答自洽」闸替掉 per-cap 码层特判(夯实B);trivial-pass 仍暂缓 |
| Stage D 渲染 | 每周写当期快照 | **mention-on-change**(只渲染变更字段)+ **草堆 filler** | 夯实B/C:逼 KU/IE 真跨周 + 制造草堆干扰 |
| IE 句式 | locate"哪几周=值" | **时点切片**"第N周X多少"(中间值) | mention-on-change 下 locate 失效; 时点切片考 recency 抗性 |
| CONFLICT | (未定) | **弱定义**(值随时间不同即冲突) | 用户拍板; 跟 KU/TR 有重叠, 接受 |
| KU 产出 | 红海主力 | **4/4 通过**(夯实B mention-on-change 后强 KU 成立) | 值只在变更周陈述、之后不复述 → "现在多少"必须跨周回忆, 硬判别器满意 |
| Stage G 判分 | (未细化) | 子串 → **LLM-judge**(治 M5)+ **空答案升温重试**(防污染矩阵) | 子串冤杀格式不同的对答案; oracle 长上下文偶发吐空 → 假负区分度 |

## §10 已知缺口（→ 后续）

- **诊断层未接**:𝓕-𝓔-𝓠 算子(除 𝓔 结构隐式)、M1–M6 失败模式**都未透传到生成最初**——V10 是"能力驱动"而非"失败模式诊断驱动"的生成器(详见与用户的讨论)。
- **真 baseline**:Stage G 现用 5 个上下文消融当"模型", 12 题上 point-biserial 噪声大(random_1>recency 的抖动即是)。应换成算子定位不同的真记忆系统(simpleMem/Mem0/RAPTOR…)跑 BAT, 区分度才可信。
- **规模**:当前 3 实体/6 周/~12 题, 论文需扩到 ~100 题 + 多实体(夯实后即上)。
- **多场景**:office 周报外另跑 3 个领域验泛化。
- **~~ABS/FORGET 判分地板~~ ✅已修(夯实A)**:ABS 不进区分度;no_context 的 FORGET 不计分。`validity_sane=True`。
- **~~弱 KU / 文档单薄~~ ✅已修(夯实B/C)**:mention-on-change 强 KU + 草堆 filler(语料 2.5k→9k 字)。
- **TR 多变化歧义**(重判暴露的小坑):某字段变了多次时,"在哪一周变化"有歧义(gt 取首次变化,但模型可能答末次)→ 该题全模型失分。修法:TR 题指明"首次/最近一次", 或只对单次变化字段出 TR。
- **规模**:当前 3 实体/6 周/10 题, 论文需扩。
