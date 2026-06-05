# Redesign V10 — 把 gt 从 LLM 手里拿走，交给「会算账的状态机」

> **一句话**：V8/V9 让 LLM 既当出题人又当裁判（gt 始终拴在 LLM 身上），结果是 toy fixture + judge 失效 + 伪记忆。
> V10 先用代码搭一个**多实体、带完整账本的虚拟世界**（状态机），gt 在生成时就**烘焙**进去；LLM 退为「出题人」，
> 代码当「账本 / 裁判」。再补**多实体级联规模**与**有效性闭环（区分度 + BAT + FAMA）**。
>
> **真代码出处**（已 clone 到 `refs/`，已逐行核对）：
> - MEME `refs/MEME/code/data/{generate_episode,generate_gold_facts,assemble_episodes}.py`、`code/eval/golden_memory.py`
> - Memora `refs/Memora/evals/model_eval/model_based_evaluator.py`（`fama_score` L92–112）、`data/README.md`
> - BenchBench `refs/BenchBench-BAT/{logic.py:get_agreement, reporting.py:get_z_score}`
> - PSN-IRT `refs/PSN-IRT/models/PSN_IRT.py`（4PL）→ `item_parameters.csv`

## 0. 为什么又要改：四版的「裁判归属」演化

| 版本 | 出题谁干 | gt（真答案）谁定 | 死因 |
|---|---|---|---|
| V6 | 代码（信号竞争失败模式） | 代码 | 太窄，只考一种 failmode |
| V7 | 代码模具 | 代码 | LLM 不是主角，题死板 |
| V8 | **LLM** answer-first | **LLM**（生成）+ LLM judge（校验） | LLM 既当运动员又当裁判 → toy fixture、judge 12/12 全过失效、伪记忆 5/12 |
| V9 | LLM + 多步 exploration | **LLM**（生成）+ judge 多票（校验）+ 硬判别器 | 题变深了，但 gt 还拴在 LLM 身上；硬判别器 reject 0 存疑；仍 1 实体小规模 |
| **V10** | **LLM 出题（语言/创意）** | **★ 代码状态机（账本/机械算）** | — |

**类比**：V8/V9 像让小说家既写故事、又当历史考据官（口径常跟自己打架 → 假冲突）。
V10 先用代码搭一个「记着每个实体、每个时刻状态的账本世界」，小说家在世界里逛着出题，**但答案直接查账本**。
账本永远不跟自己口径打架（治 V8 假冲突），还能精确算「这一刻该记得什么、该忘了什么」（→ Memora state-diff → FAMA）。

**关键认知**（来自 clone 精读）：我们 V8 放弃的「结构当裁判」**不是想法错，是实现有 bug**（口径不齐 / 漏 argmin）。
Memora 用 **state-diff 当 gt** 证明这条路是对的；MEME 证明 gt 应当**在生成时烘焙**（写进 before/after 状态），
而不是 runtime 反向遍历图。两篇可以**合并成一个引擎**：每个 entity 维护一条时间线 `[(value, session, op)]`，
任意 question_date 切片 → present_items（gt=记得）+ invalidated_items（gt=已忘/no）。
**MEME 的 Cas/Abs/Del 三类，就是 Memora state-diff 三种 op 的特例 → 一份 diff 逻辑覆盖两篇全部任务。**

---

## V8/V9 → V10 改动总览

| | V8/V9 | V10 |
|---|---|---|
| 真值世界 (Stage C) | FactGraph（1 实体、judge 参考用） | **多实体状态机**：entity timeline + ops + DAG 级联，**gt 烘焙在生成时** |
| 规模 | 1 实体 / 4 字段 / 5 session | **多实体**（MEME `entity_pools`）+ 实体间依赖边，撑起 MR/CONFLICT/级联 |
| 出题 (Stage E) | LLM answer-first 自己定 answer | **fact-first**：代码「点菜」给 (能力,实体,字段,date,**gt**,证据 session)，LLM 只把它写成**跨 session 才能答的自然题** |
| gt（裁判） | LLM judge | **★ 代码 state-diff 机械算**（单一口径，argmin/argmax 都对）；judge 退为辅（只判 well_formed） |
| 防伪记忆 | 硬判别器（V9） | 硬判别器**保留** + **MEME trivial-pass**（答 after 题前必先答对 before 题）+ FILLER 黑名单 |
| 防剧透 | （V9 deferred） | **必做**：session-local 措辞、禁「当前/现任/最新/截至…仍为」全局口径 |
| 有效性 | 无 | **★ baseline 作答矩阵 → 区分度（坏题探测）+ BAT（Kendall-τ validity）+ FAMA（遗忘惩罚）** |

---

## Stage C V10 —★状态机真值世界（取代纯 FactGraph，本版核心地基）

- **输入**：Spec + Dimensions + CapabilityPlan（Stage 0/A/B 复用 v8，不动）
- **输出**：`WorldState`（多实体时间线 + 级联边 + 一批已烘焙好的 gt-tuple）
- **谁**：代码（状态机机械记账）+ LLM（只生成实体/字段/取值的**素材**，不碰 gt）

### 数据结构（核心）

```jsonc
WorldState {
  "entities": {
    "AI工程部": {
      "P0缺陷率": {                      // 一个字段 = 一条时间线
        "timeline": [
          {"session":0, "date":"2025-01-06", "op":"SET",    "value":"2.5%"},
          {"session":1, "date":"2025-01-13", "op":"UPDATE", "value":"1.8%", "prev":"2.5%"},  // Cas: 有替代值
          {"session":3, "date":"2025-01-27", "op":"UPDATE", "value":"2.8%", "prev":"1.8%"},
          {"session":5, "date":"2025-02-10", "op":"EXPIRE", "value":null,   "prev":"2.8%"}   // Abs: 停止统计→遗忘
        ]
      },
      "负责人": {"timeline":[{"session":0,...,"value":"张三"}, ...]}
    },
    "数据平台部": { ... }                 // ★ 多实体
  },
  "cascades": [                           // ★ DAG 级联（MEME）：A 变触发 B 变，显式预声明替代值
    {"if":{"entity":"AI工程部","field":"负责人","becomes":"李四"},
     "then":{"entity":"AI工程部","field":"汇报对象","set":"CTO"}, "session":2}
  ],
  "absent_fields": ["季度营收","客户满意度"]   // ABS 用：任何 timeline 里都没有
}
```

### op 类型（Memora state-diff 三 op = MEME 三类）

| op | 含义 | MEME 对应 | state-diff 后果 |
|---|---|---|---|
| `SET` / `UPDATE` | 设值 / 改值（有替代值） | **Cas**（Change） | 旧值失效、新值生效 |
| `DELETE` | 删除（有「已删除」语义） | **Del**（Delete） | 该字段不再存在 |
| `EXPIRE` | 停止/过期（无替代值，不确定） | **Abs**（Absent） | 该字段→「已遗忘 / 不再有效」（gt=no，答旧值=用过期记忆→FAMA 罚） |

### gt 烘焙 = 切片（`slice(question_date)`，这就是 Memora 的 state-diff）

代码对任意 `question_date` 切时间线 → 得 `present_items`（当时有效值）+ `invalidated_items`（已失效/已忘）。
**gt 不靠 runtime 遍历推，生成时就算好钉死**（MEME `assemble_episodes.py:364` 的 before/after 结构）：

```
切到 2025-01-20 → P0缺陷率 present=1.8%（session1 仍有效）
切到 2025-02-15 → P0缺陷率 INVALIDATED（已 EXPIRE）→ 问「当前 P0?」gt=「已不再统计」；答 2.8% = 过期记忆 → FAMA 罚
```

### ★ 能力 ↔ op 映射（gt 全由代码机械算，单一口径 = 做对的「结构裁判」）

| 能力 | 状态机操作 | gt 来源（代码） |
|---|---|---|
| **IE** 信息抽取 | `slice(session=s)[field]` | 某 session 当时的值 |
| **MR** 多跳聚合 | `reduce(values, max/min)` + **argmax/argmin** | 极值 + 对应 session（★ argmin 也算，治 V8 漏 argmin bug） |
| **TR** 时序 | 找 value 变化的 op → `op.session/date` | 变化发生的时刻（[TIMESTAMP_BEFORE_CHANGE] 锚定） |
| **KU** 知识更新 | `timeline.last_valid_value` | 最新有效值（旧值在前面多 session 高频 → 必须比时间） |
| **CONFLICT** 冲突 | 检测同字段跨 session 不同值 | 「不一致」+ 两值两 session |
| **FORGET** 遗忘 | timeline 有 EXPIRE/DELETE → 之后 slice=INVALID | 「已遗忘/不再有效」（= Memora `forgetting_absence`，喂 FAMA λ） |
| **ABS** 不存在 | `field ∉ 任何 timeline` | `INSUFFICIENT_EVIDENCE` |

- **逻辑**：Stage C 生成 WorldState 后，按 CapabilityPlan 配额「点菜」枚举 gt-tuple：
  `{capability, entity, field, question_date, gt(代码算), evidence_sessions[≥2]}`。这批 gt-tuple 是 Stage E 出题的「订单」。
- **prompt（仅生成素材）**：LLM 只产出「实体名/字段名/各 session 取值」的草料（虚构命名，去污），**op 编排与 gt 由代码做**。

---

## Stage D V10 — corpus 渲染（timeline → 多 session 对话/文档）

- **输入**：WorldState
- **输出**：Corpus（多 session、多文档），结构照搬 MEME `assemble_episodes.py:364`：
  ```
  [filler x N] --- (before_questions 锚点) --- [filler x N]
  [Change/Delete Event] [filler x N] --- (after_questions 锚点) ---
  ```
- **谁**：代码定结构/事件位置 + LLM 把每个 timeline 节点渲成自然的周报/对话句
- **三条硬规矩**：
  1. **session-local 措辞**（★ 修 V9 剧透根因）：每篇只陈述「本期数值」，**禁**「当前/现任/最新/维持/截至…仍为/累计」等全局口径 —— 逼模型靠**比较多 session 日期**才能得最新值，而不是读一篇就抄到答案。
  2. **filler 黑名单**（MEME `FILLER_BLOCK_KEYWORDS`）：插入的干扰 session 扫描，凡含会泄漏答案的第一人称/字段短语一律 block。
  3. **change/delete event 显式成句**：值变化必须作为一条独立 event 出现（after 题的 gt 锚在它之后），TR 的「何时变」靠它定位。

---

## Stage E V10 — exploration 把 gt-tuple 写成自然题（fact-first，复用 V9 引擎）

- **输入**：gt-tuple 订单 + Corpus
- **输出**：`RawQuestion{question, answer(=订单 gt), field, capability, hops, evidence_spans}`（同 v9 字段）
- **谁**：LLM（V9 的 search/read + ReAct 多步 exploration，`stages_v9.stage_e_*` 直接复用）
- **关键改动**：**answer 不再由 LLM 拍**，而是订单里代码算好的 gt。LLM 的活变成：
  「这是答案 `gt` 和它散落的证据 session，**写一道必须读齐这些 session 才能答、且题面不泄漏答案**的自然问题。」
  → 保留了 exploration 的价值（跨文档深度 + 不泄漏措辞），但把 gt 彻底交给代码。
  → 这正是 v8「生成放手 LLM、gt 靠校验/账本」原则的彻底版：**LLM 管语言与创意，代码管记账与 gt**。

---

## Stage F V10 — 四道闸（grounding + 硬判别器 + trivial-pass + judge 辅）

- **输入**：list[RawQuestion] + Corpus + WorldState
- **输出**：list[Question]（通过）+ reject_log（带 `memory_necessity`/`trivial_pass`/`judge_votes`）

### 闸 (a) grounding —— 代码（同 v8/v9）
evidence_spans 模糊匹配在不在语料（θ=0.85）；不在 → reject。ABS 题跳过。

### 闸 (b) 硬判别器 —— LLM（保留 V9）
喂「无语料 / 单文档」baseline，能答中 → reject 伪记忆（`fail_commonsense` / `fail_single_doc`）。
**现在它纯做「记忆必要性」过滤**（gt 由状态机给，它不再兼任 gt）。

### ★ 闸 (c) trivial-pass 交叉验证 —— 代码（新增，抄 MEME `golden_memory.py:217`）
对 KU/TR/CONFLICT/FORGET 这类「值变过」的题：模型要被 after 题考分，**必先答对对应的 before 题**（证明它真存过旧值）。
答不出 before → 该 after 不计分/丢弃。治「蒙对最新值但其实没记住演化」。

### 闸 (d) judge —— LLM 多票（**退为辅**）
judge K=3 多票，**只判 `well_formed`（题面是否流畅、是否自洽）**；`answer_correct` 不再问 judge（gt 是代码的）。

### 通过条件
grounding ✓ **且** 硬判别器 pass **且** trivial-pass ✓ **且** judge well_formed 多票 ✓。

---

## Stage G V10 —★有效性闭环（baseline 作答矩阵，本版第二大件）

- **输入**：通过的 Question 集
- **输出**：每题带 `difficulty / discrimination`，benchmark 带 `BAT_validity`，FORGET 子集带 `FAMA`
- **谁**：代码（跑模型 + 统计）

1. **作答矩阵**：跑 N=8–12 个 baseline 模型 → `R[model,item] ∈ {0,1}`。
2. **难度档**：`p_i = mean_model R[:,i]`，分箱 easy/med/hard。
3. **区分度**（★ 第二个无效题探测器）：`r_pb(i) = point-biserial(R[:,i], 总分−本题)`。
   **负区分度 = 强模型反而错 = 坏题/答案错 → 回收人审**（BenchBench：无效题 ↔ 低区分 r≈−0.62；对应 PSN-IRT discrimination 参数 a）。
4. **BAT validity**（IBM BenchBench）：我们的模型排名 vs 聚合排名 **Kendall-τ**（`logic.py:get_agreement`）+ **Z-score** 归一化 vs 同侪 benchmark（`reporting.py:get_z_score`）；**z>0 = valid**。
5. **FAMA**（Memora `model_based_evaluator.py:fama_score` L92–112，逐行核对）：对 FORGET 题分 `memory_presence` / `forgetting_absence` 两类——
   `MPA = presence对/总`，`FAA = absence对/总`，`λ = N_forget/(N_present+N_forget)`，
   **`FAMA = max(0, MPA − λ·(1−FAA))`**。惩罚「该忘的还在用过期记忆答」。
6. **双探测器交叉**：硬判别器（生成时，抓「不靠记忆也能答」）⊕ 区分度（评测后，抓「题坏/答案错」）——**两个独立的无效信号**，交叉命中的题最该回收。

---

## §保留（V8/V9 已对的，不动）
answer-first 思路（V10 升为 fact-first）、grounding、exploration 多步出题（V9 引擎）、硬判别器、judge 多票（退为 well_formed 辅判）、能力规划取代硬编码 profile、虚构命名去污。

## §坑（clone 精读暴露，实现时盯紧）
- **Memora state simulator 不开源**（PDF §3.2，仅放 DATA+EVAL）→ Stage C 状态机得照 PDF §3.2 + `data/README.md` schema 自己写（我们本就在写状态机，正好）。
- **级联可解性**：DAG `cascades` 必须**显式预声明替代值**（MEME 做法），否则级联题无解。
- **泄漏三防**：虚构命名（ITD 去污）+ FILLER_BLOCK_KEYWORDS 黑名单 + session-local 措辞，缺一不可。
- **规模**：V8/V9 是 1 实体；V10 必须多实体（`entity_pools`）才撑得起 MR/CONFLICT/级联。

## §任务分解（实现顺序，地基先行）
- **T1 状态机引擎**（纯代码、可单测）：entity timeline + ops（SET/UPDATE/DELETE/EXPIRE）+ `slice(question_date)` state-diff。断言：给定 timeline+date → gt 正确。
- **T2 能力↔op 映射 → gt-tuple「点菜」生成器**（代码枚举各能力的 gt-tuple，单一口径）。
- **T3 Stage C 多实体世界 + DAG 级联**（`entity_pools` + `cascades` 显式预声明）——抄 MEME `generate_episode/gold_facts`。
- **T4 Stage D 渲染**（timeline→多 session 对话；change/delete event + filler 黑名单 + session-local 防剧透）。
- **T5 Stage E/F**（exploration 把 gt-tuple 写成自然题 + 四闸：grounding/硬判别器/trivial-pass/judge 辅）。
- **T6 Stage G 有效性**（R 矩阵 + 区分度 + BAT Kendall + FAMA）——抄 BenchBench + Memora。
