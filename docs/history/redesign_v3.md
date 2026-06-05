# V3 设计 spec(W1.5 重启)

> **背景**:V1 跑通骨架但本质是"标签贴皮";V2 设计了 LLM 合成但仍用 OfficeMem 全量 corpus。
> 用户基于头脑风暴提了 4 个核心问题,经 2 路 subAgent 调研(Spec Refinement / Memory Taxonomy)后,**V3 是把"输入场景描述+few-shot 示例 → 输出完整 benchmark"这个核心 idea 彻底落到代码的版本**。
> **目标投稿**:SIGMOD 2026 年 7 月窗口。

---

## 1. V1 → V2 → V3 走过的路

| 版本 | 输入 | corpus 来源 | qa 来源 | 状态 |
|---|---|---|---|---|
| V1 | description + 5 篇 + hint | **OfficeMem 全 1899 篇** | **复用 OfficeMem 旧 104 题改造** | 作废:违反 idea |
| V2 | description + 5 篇(无 hint) | OfficeMem chain(content inline) | LLM 生成 + 启发式 fallback | 作废:仍未真正"合成 corpus" |
| **V3** | **description + 3-5 篇** | **LLM 合成** | **LLM 按 cell_quota 生成 + failmode_signature** | **当前版本** |

V3 的核心动词 = **合成**(synthesis)。LLM 调用次数从 V1 的 0 次 → V3 的多次(Clarifier、Stage C、Stage D 都调 LLM)。

---

## 2. V3 数据流(7 步)

```
RawInput (description + corpus_samples[3-5])
    ↓
[Stage 0] Spec Refinement Layer  ★ 用户决策:W1.5 真上
    - Clarifier(LangGraph 风格):判断"够不够清晰"→ 不够则一次性发 markdown 表单(max 6 问)
    - Brief Writer:把对话编译成 RefinedScenarioSpec
    - 硬上限 2 轮,缺字段标 open-ended 不脑补
    ↓
[Stage A] 维度推断
    - 从 refined spec 推 (I, S, V, T, cognitive_flavor) 5 维
    - LLM 主路径 + 启发式 fallback + tie-breaker
    ↓
[Stage A'] Seed Bias 检测 + 自动多样化  ★ 用户决策:W1.5 真上
    - 用 LLM 给每篇 corpus_sample 在 5 维空间打位置
    - 检测未覆盖的维度组合
    - 用 LLM 合成新 seed 补全(类似 Self-Instruct 自举)
    - 输出 ExpandedSeedPool(5-10 篇,覆盖均衡)
    ↓
[Stage B] 配额计划 — 三轴架构  ★ 用户决策:LME 5 维 + 失败模式 + 算子
    - 能力轴 = IE/MR/TR/KU/ABS + 可选 LRU
    - 失败模式轴 = M1/M2/M3/M4/M5(M6 不签发)
    - 算子轴 = F1-4 / E1-4 / Q1-3
    - cell_quota = (capability, failmode) → 题数(SIGMOD novel angle)
    - 输出 OpsProfileV3
    ↓
[Stage C] Corpus 合成 ★ 这才是真合成
    输入: ExpandedSeedPool + OpsProfile + ScenarioDimensions
    用 LLM 生成:
    - N 个 chains(同 cluster 不同维度组合)
    - 每 chain × M periods,每 period 1-3 篇新文档
    - chain 内字段演化是【设计出来的】,ground truth 我们知道
    输出: list[Chain](合成的 corpus,qas=[])
    ↓
[Stage D] 题目生成 ★ 真按 cell_quota 出题
    输入: Chains + cell_quota
    对每个 (capability, failmode) cell:
    - 按配额 N,用 LLM 从 corpus 字段演化生成 N 道题
    - 每题强制带 failmode_signature(M3 题填 common_default,M4 题填 distractor 等)
    - answer_by_period 升级为等价集 + answer_canonical 单值
    输出: list[Chain](qas 完整)
    ↓
[Stage E] 打包输出
    - 扩展版 JSON(完整标签 + cell_quota statistics)
    - OfficeMem 兼容版 JSON(顶层 list + canonical answer + date 字段)
    - self-contained(corpus content inline)
```

---

## 3. V3 核心 schema

```python
# ─────────── 能力 / 失败模式 / 认知风格 枚举 ───────────
CapabilityName = Literal["IE", "MR", "TR", "KU", "ABS", "LRU"]
# IE 信息抽取 / MR 多session推理 / TR 时序推理
# KU 知识更新(吸收 MAB·SF) / ABS 拒答 / LRU 长程理解(可选)

FailmodeName = Literal["M1", "M2", "M3", "M4", "M5"]
# 毕设 5.4 节定义,M6 多选偏置是设计问题不签发

CognitiveFlavor = Literal["episodic-heavy", "semantic-heavy",
                          "procedural-heavy", "mixed"]
# 不上配额,只作 Stage D prompt prior


# ─────────── Stage 0 输出:RefinedScenarioSpec ───────────
@dataclass
class RefinedScenarioSpec:
    name: str
    description_refined: str
    corpus_samples: list[Document]
    perspective: str
    subject_type: str
    temporal_pattern: str
    target_size: int
    open_ended_fields: list[str]  # ★ 用户没说的字段,Clarifier 留 open-ended
    cognitive_flavor_hint: Optional[CognitiveFlavor] = None
    clarification_history: list = field(default_factory=list)  # 完整对话记录,可 audit


# ─────────── Stage B 输出:OpsProfileV3 ───────────
@dataclass(frozen=True)
class OpsProfileV3:
    # 主轴 1: 能力配额
    capability_quota: dict[CapabilityName, int]
    # 主轴 2: 失败模式配额
    failmode_quota: dict[FailmodeName, int]
    # 主轴 3: 算子配额
    formation_quota: dict[str, int]  # F1-F4
    evolution_quota: dict[str, int]  # E1-E4
    query_quota: dict[str, int]       # Q1-Q3
    # ★ 二维 cell 配额(Stage D 签发原子)
    cell_quota: dict[tuple[CapabilityName, FailmodeName], int]
    # Prior(只供 prompt 注入)
    cognitive_flavor: CognitiveFlavor = "mixed"
    structure_hint: Literal["tree", "state-machine", "ledger", "free-form"] = "free-form"
    target_size: int = 50
    profile_name: str = ""
    rationale: str = ""


# ─────────── Stage D 输出:Question ───────────
@dataclass
class Question:
    qid: str                                # 全局唯一 = f"{chain_id}__{local_id}"
    source_chain_id: str                    # ★ 反向溯源
    question: str
    answer_by_period: dict                  # {period_idx: list[str] 等价集}
    answer_canonical_by_period: dict        # ★ 单值 canonical,供 OfficeMem 老 evaluator
    field_name: Optional[str] = None

    # 三轴标签
    capability: CapabilityName              # ★ 替代 V1 的 task_type
    failmode: Optional[FailmodeName]
    failmode_signature: dict                # ★ 强制实填(M3 必有 common_default 等)
    formation_op: Literal["F1","F2","F3","F4"]
    evolution_op: Optional[Literal["E1","E2","E3","E4"]]
    query_op: Literal["Q1","Q2","Q3"]

    judge_modes: list = field(
        default_factory=lambda: ["EM","sub_match","fuzzy","llm_judge"]
    )


# ─────────── Stage E 输出:Benchmark ───────────
@dataclass
class Benchmark:
    benchmark_id: str
    scenario_name: str
    scenario_spec_snapshot: dict            # ★ 保存 description + corpus_doc_ids + Clarifier 对话
    ops_profile: OpsProfileV3
    chains: list[Chain]                     # 合成的 chain,corpus inline
    statistics: BenchmarkStats
```

---

## 4. 4 个用户决策点的具体实现 spec

### 4.1 Spec Refinement Layer(用户决策:W1.5 真上)

实现位置:`pipeline/stage_0_refinement.py`

```python
from anthropic import Anthropic  # 或 OpenAI 兼容客户端

class Clarifier:
    """两步走:Step 1 判定是否需要追问,Step 2 编译 refined spec。"""

    MAX_ROUNDS = 2

    def clarify(self, raw_input, history=None) -> ClarifyOutput:
        """单轮 clarify;LLM structured output:need_clarification + question + verification。"""

    def compile(self, raw_input, history) -> RefinedScenarioSpec:
        """编译 final refined spec(LangGraph 风格 5 原则:
        max specificity / fill open-ended NOT guess / no unwarranted assumptions /
        first person / cite corpus concretely)"""
```

**核心 prompt 骨架**(参考 LangGraph open_deep_research,逐字 verbatim 在 survey/spec_refinement/blog_articles/):

```
ROLE: 你是一个 memory benchmark 设计专家,任务是把用户的模糊请求
refine 成结构化 ScenarioSpec。

ABSOLUTE RULES:
1. 缺字段标 open-ended,不准猜
2. 如果已经问过一轮,几乎不要再问(only ASK IF ABSOLUTELY NECESSARY)
3. 一次问最多 6 个问题,markdown 表单格式
4. 缩写/术语需要澄清
```

**用户交互**:Stage 0 暴露一个 CLI 或 Python 函数 `refine_scenario(raw_input) → RefinedScenarioSpec`,内部循环 Clarifier 直到 `need_clarification=False` 或 `MAX_ROUNDS` 触发。

**测试方式**(W1.5 阶段):用毕设 SIGMOD 实验范本 scenario 构造 3-5 个 raw_input(不同模糊程度),手工运行 Clarifier,看是否能编译出合理的 refined spec。

**Token 预算**:Claude Opus 4.7 ~$0.45/场景,SIGMOD 5 场景 × 10 ablation = ~$30,可行。

---

### 4.2 LME 5 维能力轴(用户决策:全盘采纳)

替换 V1 的 `task_type = AR/CR/TTL/LR`。

```python
CapabilityName = Literal["IE", "MR", "TR", "KU", "ABS", "LRU"]
```

- **IE** Information Extraction:单 period 字段回填(e.g. "P0 缺陷率本周是多少")
- **MR** Multi-Session Reasoning:跨 period 综合(e.g. "10 周里 P0 缺陷率最高的是哪周")
- **TR** Temporal Reasoning:时序推理(e.g. "X 字段在哪两个 period 之间反转")
- **KU** Knowledge Update:识别字段更新(e.g. "最新的负责人是谁")— 吸收 MAB·SF
- **ABS** Abstention:识别"不知道"的题(false premise)
- **LRU** Long-Range Understanding(可选,场景默认 0)

**为什么不用 MAB**:
1. MAB ICLR'26 v2 把 AR/CR/TTL/LR 改成了 AR/TTL/LRU/SF — taxonomy 不稳定
2. TTL 需要 streaming task design,我们 corpus-driven 做不到(Evo-Memory 证明)
3. LME 5 维 mutually-disjoint,每维题型可枚举,Stage D LLM 生成时 prompt 清晰

---

### 4.3 Seed Bias 检测 + 自动多样化(用户决策:W1.5 真上)

实现位置:`pipeline/stage_a_seed_expansion.py`

```python
def check_seed_coverage(
    samples: list[Document],
    refined_spec: RefinedScenarioSpec,
    llm_client,
) -> SeedCoverageReport:
    """用 LLM 给每篇 sample 在 5 维空间打位置,返回:
    - covered_cells: 哪些 (I, S, V, T) 组合已覆盖
    - missing_cells: 哪些应覆盖但没覆盖
    - bias_score: 覆盖均衡度(基于 entropy)
    """


def expand_seeds_via_llm(
    samples: list[Document],
    missing_cells: list,
    refined_spec: RefinedScenarioSpec,
    llm_client,
) -> list[Document]:
    """针对 missing_cells,用 LLM 合成新 seed。
    
    每篇新 seed 必须:
    ① 与 description 一致
    ② 与原 samples 风格相近(不能跳脱)
    ③ 落在 missing_cells 之一
    ④ 含合理的 metadata(timestamp / author / doc_type)
    """
```

**核心 prompt 骨架**(类似 Self-Instruct 自举):

```
ROLE: 你是文档分布扩展专家。

INPUT:
- 5 篇真实 samples(展示分布)
- 场景描述(目标空间)
- 缺失的维度组合:{...}

TASK:
为每个缺失的 (I, S, V, T) 组合合成 1 篇新文档,
必须风格与 samples 一致,只是落在缺失的位置。

OUTPUT: list[Document] with metadata
```

**测试方式**:从 OfficeMem 故意采 5 篇全是 "周报" 的 sample,看 expand_seeds_via_llm 能否补出 "会议纪要 / 技术方案" 等其他通道的样本。

---

### 4.4 target_size 规模 — 五脏俱全(用户决策:最终 500/前期少但全)

**前期目标**(W1.5 V3.0):**单场景 50 题**
- 每能力维度 ≥ 8 题
- **每个非零 cell 至少 1 题**(五脏俱全的硬约束)
- 跑通整个 pipeline,做 schema/接口验证

**中期目标**(W1.5 V3.1 - W2):**单场景 200 题**
- 每能力维度 ≥ 30 题
- 每个非零 cell ≥ 3 题
- 开始有统计学意义的 ablation 结果

**最终目标**(W3 阶段):**单场景 500 题**
- 每能力维度 ≥ 80 题
- 每非零 cell ≥ 10 题
- 与 LongMemEval(500 题)同量级,可发表

**W4-W5**:多场景 × 500 题 → 总 2000+ 题(达到 MAB 量级)

---

## 5. office_engineering 场景 V3 配额示例(target_size=50)

| 能力 \ 失败 | M1 | M2 | M3 | M4 | M5 | none | 行计 |
|---|---|---|---|---|---|---|---|
| **IE** | 1 | – | – | 2 | 4 | 3 | **10** |
| **MR** | 4 | 3 | – | 2 | – | 2 | **11** |
| **TR** | – | – | 2 | – | 2 | 4 | **8** |
| **KU** | – | 2 | **8 ★** | – | – | 2 | **12** |
| **ABS** | – | – | – | – | – | 6 | **6** |
| LRU | 0 | 0 | 0 | 0 | 0 | 3 | **3** |
| **列计** | **5** | **5** | **10** | **4** | **6** | **20** | **50** |

★ `(KU, M3) = 8` 是核心 cell — 对应毕设 simpleMem × CR R3 = 10.1% 的最 sharp 观察。**这 8 题 LLM 生成时 prompt 必须明确:`failmode_signature = {type: 'conflict_defaulting', common_default: <训练分布高频值>, injected_value: <反常识值>}`**。

**每个非零 cell 都有 ≥ 1 题**(五脏俱全)。

---

## 6. W1.5 V3 任务拆分(8 个 task,~10-14 天)

| Task | 内容 | 工作量 |
|---|---|---|
| **T1** | schema 重设计(LME 能力轴 + 失败模式轴 + cell_quota + RefinedScenarioSpec + cognitive_flavor) | 1 天 |
| **T2** | **Stage 0** Spec Refinement Layer(Clarifier 两步走 + Brief Writer + 5 原则 prompt) | 2 天 |
| **T3** | **Stage A v3** 维度推断 + cognitive_flavor 推断(LLM 主路径 + 启发式 fallback + tie-breaker) | 1 天 |
| **T4** | **Stage A'** Seed Bias 检测 + 自动多样化(LLM 评估 + 自举合成) | 2 天 |
| **T5** | **Stage B v3** 三轴配额(LME 5 维 + cell_quota lookup table + 多 profile) | 1 天 |
| **T6** | **Stage C v3** Corpus 合成(LLM 从 ExpandedSeedPool 生成 chain + period + content)★ | 3 天 |
| **T7** | **Stage D v3** cell_quota 驱动的 LLM 题目生成 + failmode_signature 强填 ★ | 2 天 |
| **T8** | **Stage E v3** + MemoryInterface 适配器 + 端到端 smoke test(50 题 office) | 2 天 |

**总 ~14 天**。Stage C v3(T6)是最大不确定性,因为这是真正的"corpus 合成",涉及多文档一致性、字段演化设计、ground truth 同步。

---

## 7. 关键设计原则(全部锁定)

1. **LLM 是 first-class citizen,启发式只作 fallback** — V1 的"全启发式 0 LLM 调用"是死路
2. **OpsProfile 必须真消费,Stage D 按 cell_quota 签发** — V1 把 OpsProfile 当装饰品的覆辙不重蹈
3. **每个非零 cell 至少 1 题(五脏俱全)** — V3.0 即使 50 题也要覆盖完整三轴空间
4. **`failmode_signature` 强制实填** — M3 题必须有 common_default,M4 题必须有 distractor,M5 题必须有 equivalent_set
5. **schema 自包含** — corpus content inline,benchmark JSON 任意 cwd 都能跑
6. **双 wrapper 输出** — 扩展版 + OfficeMem 兼容版
7. **Clarifier 硬上限 2 轮 + open-ended fields 可 audit** — 支持 W4 Clarifier ablation
8. **Seed bias 检测必须先于 Stage C** — 不能让 corpus 合成放大 user 的输入偏差

---

## 8. 引用

- `survey/spec_refinement/REPORT.md`(8 篇 PDF + 5 篇 blog,LangGraph + FATA + CLAM)
- `survey/memory_taxonomy/REPORT.md`(20 篇 PDF,LME vs MAB vs MIRIX 对比)
- `docs/anchors/operator_taxonomy.md`(𝓕-𝓔-𝓠 算子)
- `docs/anchors/failmode_taxonomy.md`(M1-M5)
- `docs/anchors/scenario_dimensions.md`(I, S, V, T)
- 毕设第 3.5 节(MemoryInterface)、第 5.4 节(失败模式案例)

V1 / V2 anchor(`redesign_v2.md`)保留但**作废**,以 V3 为准。
