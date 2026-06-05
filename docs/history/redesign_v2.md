# V2 重新设计 spec(W1.5)

> **背景**:W1 V1 经 3 路 subAgent 审查发现 4 个根本性问题(详见末尾 "V1 死症"),不只是 bug 修复就能进 W2。V2 必须**重新设计 Stage D 接口 + 补 MemoryInterface 层 + 修 schema + 加 LLM hook**。
> **目标**:7-10 天内交付 W1 V2,然后才能进 W2 simpleMem 评测。

---

## 1. V1 的 4 个根本死症

### 死症 1:OpsProfile 没被消费 — 配额是装饰品
- `forge_chains_with_labels(chains, benchmark_path)` 签名不接 `OpsProfile`
- Stage B 算的 F1/F2/F3/F4 = 4/10/11/5, Q1/Q2/Q3 = 10/14/6, M1-M4 = 3/10/14/3 → 全被丢弃
- 实际签发 F2=103, F4=1, Q1=104, M3=26, M4=78 → 算子轴完全塌陷
- SIGMOD 卖点"5 维空间 ablation"在 V1 数据上根本跑不出来

### 死症 2:W3 Spearman 是循环论证
- V1 直接复用 OfficeMem 旧 qa 主体(question + answer_by_period),只换标签皮
- 9 baseline 看到的题和答案 99% 与 OfficeMem 一样 → Spearman 必然 ≥ 0.85
- **这不是 few-shot 泛化的证据,是抄作业** — 审稿人一眼戳穿
- **必须切到"用 LLM 从 corpus 真正生成新题"**

### 死症 3:failmode 标签 80% 虚标
- 我们标的 26 道 M3 全部不满足毕设 M3 触发条件(反常识 gold + LLM 常识默认值)
- 我们标的 78 道 M4 全部不是 AR 多选题 + 同义改写干扰
- `_infer_target_failmode` 用 `task_type → failmode` 一对一硬映射,完全丢了独立信息

### 死症 4:接口/schema 7 处兼容性硬伤
- qid 跨 chain 不唯一(104 qid / 73 unique)→ W2 evaluator silent data corruption
- 等价集 `.0%` 拼接 bug → `"13.8%"` → `"13.8.0%"`(非法字符串,62 处污染)
- 空答案/`null` 进等价集 → 77 处行为不可预测
- `timestamp` vs OfficeMem `date` 字段错位 → 老 evaluator KeyError
- 顶层 dict vs OfficeMem list → 老 evaluator TypeError
- `Question.source_chain_id` 缺失 → benchmark 不能 standalone
- `ingest_docs.content` 不 inline → 必须在 OfficeMem cwd 才能 resolve path

---

## 2. V2 数据流(修正后)

```
ScenarioSpec(description + corpus_samples)
   ↓
[Stage A v2] 维度推断
   - 启发式 + LLM hook(LLM 不可用时启发式 fallback)
   - V 维度 tie 用优先级 tie-breaker(更专 > 更泛)
   - T 维度真正分析 timestamp 节律,不再 fallback project_phase
   ↓
ScenarioDimensions(I, S, V, T)
   ↓
[Stage B v2] 配额推断 → OpsProfile
   - 默认 quota 调整:Q1 重于 Q2(office_engineering 场景)
   - HEURISTIC_PROFILES 改 @dataclass(frozen=True) 类型安全
   ↓
OpsProfile + ScenarioSpec
   ↓
[Stage C v2] 加载/聚类 chain + corpus content inline
   - 从 OfficeMem 加载 chain 结构(W1 简化路径)
   - **关键**:ingest_docs[i].content 必须 inline(读 .md 文件内容到 JSON)
   - W4+ 才考虑"从 corpus_samples 自动聚类 chain"通用版
   ↓
list[Chain](含 corpus content,qas=[])
   ↓
[Stage D v2] 按 OpsProfile 配额,用 LLM 从 corpus 生成新题  ★ 核心重写
   - 输入:Chain (含 corpus content) + OpsProfile + ScenarioDimensions
   - 对每个 (failmode, task_type, F/E/Q) cell 配额,LLM 生成 N 道题
   - 每道题带:qid (全局唯一,chain_id 前缀) + source_chain_id + 完整标签 + failmode_signature
   - LLM 不可用时:启发式 fallback 改造旧 qa(降级路径,带 disclose)
   ↓
list[Chain] (qas 带完整标签)
   ↓
[Stage E v2] 双 wrapper 输出
   - 我们扩展版 JSON:dict wrapper + 完整标签
   - OfficeMem 兼容版 JSON:list wrapper + canonical answer + date 字段
   ↓
Benchmark JSON × 2 文件
```

---

## 3. 关键接口重写

### 3.1 Stage D 主入口签名(必改)

**旧 V1**:
```python
def forge_chains_with_labels(chains: list[Chain], benchmark_path: Path) -> list[Chain]:
    # 改造 OfficeMem 旧 qa
```

**新 V2**:
```python
def generate_questions_for_chains(
    chains: list[Chain],
    profile: OpsProfile,            # ★ 真正消费配额
    dimensions: ScenarioDimensions, # 提供场景上下文给 LLM
    llm_client: Optional[ChatClient] = None,  # ★ LLM hook
    legacy_fallback_path: Optional[Path] = None,  # 启发式降级用旧 qa 作种子
) -> list[Chain]:
    """对每个 chain,按 OpsProfile 配额用 LLM 生成新题。
    
    LLM 不可用时降级到"改造旧 qa"模式,但会在 statistics 里明示。
    """
```

### 3.2 MemoryInterface 适配器层(必新建)

新建 `pipeline/memory_interface.py`,严格按毕设第 3.5 节签名:

```python
from abc import ABC, abstractmethod

class MemoryInterface(ABC):
    """毕设 3.5 节定义的统一适配器接口。"""
    
    @abstractmethod
    def ingest(self, document: str, metadata: dict) -> None: ...
    
    @abstractmethod
    def retrieve(self, query: str, top_k: int = 10) -> list: ...
    
    def update(self, memory_id: str, new_content: str) -> None:
        """可选实现(只有 Mem0/Zep/A-MEM 具备此能力)。"""
        raise NotImplementedError
    
    @abstractmethod
    def reset(self) -> None: ...


class BenchmarkRunner:
    """按 benchmark JSON 的 chain × period 顺序喂 ingest_docs,
    然后对每个 qa 跑 retrieve + LLM 生成答案。
    """
    def run_chain(self, chain: Chain, memory: MemoryInterface, paradigm: str) -> dict:
        # 1. reset memory
        # 2. for each period: ingest docs in order
        # 3. for each qa: retrieve + answer (按 R1/R2/R3 范式)
        # 4. return predictions
```

W1.5 阶段只接 simpleMem(在 `/Users/ryanleory/proj/memory-systems-eval/simpleMem_src/`):
```python
class SimpleMemAdapter(MemoryInterface):
    """适配 simpleMem 到毕设 MemoryInterface 签名。"""
```

### 3.3 schema 修订(7 个字段/类型修正)

```python
@dataclass
class Question:
    qid: str  # 必须全局唯一 = f"{chain_id}__{local_qid}"
    source_chain_id: str  # ★ 新增反向溯源
    question: str
    answer_by_period: dict  # {period_idx: list[str] 等价集}
    answer_canonical_by_period: dict  # ★ 新增:每 period 的单值标准答案(供 OfficeMem 老 evaluator)
    field_name: Optional[str] = None
    formation_op: Literal["F1","F2","F3","F4"] = "F2"
    evolution_op: Optional[Literal["E1","E2","E3","E4"]] = None
    query_op: Literal["Q1","Q2","Q3"] = "Q1"
    target_failmode: Optional[Literal["M1","M2","M3","M4"]] = None
    failmode_signature: Optional[dict] = None  # ★ 真实填充
    task_type: Literal["AR","CR","TTL","LR"] = "CR"
    judge_modes: list = field(default_factory=lambda: ["EM","sub_match","fuzzy","llm_judge"])  # ★ 加 sub_match


@dataclass
class Period:
    period_idx: int
    date: str  # ★ 主字段对齐 OfficeMem(原 timestamp 改名 date)
    timestamp: str = ""  # 兼容字段(我们扩展用)
    ingest_docs: list = field(default_factory=list)
    # ingest_docs[i] 必须含 content 字段,inline 读 .md 内容(self-contained)


@dataclass
class Benchmark:
    benchmark_id: str
    scenario_name: str
    scenario_spec_snapshot: dict  # ★ 新增:存 description + corpus_doc_ids + hints
    dimensions: Optional[ScenarioDimensions] = None
    ops_profile: Optional[OpsProfile] = None
    chains: list = field(default_factory=list)
    statistics: Optional[BenchmarkStats] = None
```

### 3.4 双 wrapper 输出(Stage E)

```python
def save_benchmark_dual(bm: Benchmark, out_dir: str) -> None:
    """输出两份 JSON:扩展版 + OfficeMem 兼容版。"""
    # 扩展版:dict wrapper + 完整标签
    save_benchmark(bm, f"{out_dir}/benchmark_v2.json")
    # OfficeMem 兼容版:list[chain],qa 用 answer_canonical_by_period 单值
    save_benchmark_officemem_compat(bm, f"{out_dir}/benchmark_v2_oflat.json")
```

---

## 4. P0/P1 修复清单

### P0(blocking,必修)

1. **qid 全局唯一** → `forge_question` 加 `chain_id` 前缀
2. **`_build_equiv_set` 重写** → `.0%` 只在 `float.is_integer()` 时生成
3. **空答案/null 处理** → `_build_equiv_set("")` 返回 `["<NO_ANSWER>"]`,判分时标 SKIP
4. **`Period.date` 替代 `timestamp`** → 对齐 OfficeMem
5. **顶层 list wrapper 兼容版输出** → `save_benchmark_officemem_compat`
6. **`Question.source_chain_id`** → schema + Stage D 都加上
7. **`ingest_docs[i].content` inline** → Stage C 读 .md 文件 inline 进 JSON

### P1(高优先,本周修)

8. **V 维度 tie-breaker** → `cross_line_leader > line_manager`(更专的赢)
9. **T 维度真分析** → timestamp 节律识别(等间隔/事件驱动/周期)
10. **`failmode_signature` 真实填充** → M3 题填 `{"type":"fake_citation_risk","common_default":<高频值>}`
11. **`_infer_target_failmode` 重写** → 不再 `task_type → failmode` 硬映射,改用 LLM 判别
12. **`_looks_like_meeting` 阈值提高** → 从 2 提到 4,避免误判 PRD 为会议
13. **`SUBJECT_KEYWORDS["business_line"]` 扩充** → 让 office 场景能识别 business_line 双重主体
14. **scenario_loader timestamp 字段** → 改用 `start_time_str`(首次撰写),不要 last_modified

### P2(nice-to-have,有空再修)

15. 加 `load_benchmark(path) → Benchmark` 反序列化
16. `HEURISTIC_PROFILES` 改 `@dataclass(frozen=True)`
17. 加 `pytest` 单元测试覆盖 `_build_equiv_set` / `_to_int_quota` / `_has_reversal`
18. Default `query_quota` 调整 Q1=0.55, Q2=0.35, Q3=0.10(office 场景)
19. 大小写一致性(`text.lower()` 后比对 lowercase 关键词)

---

## 5. W1.5 任务拆分(7-10 天)

| Task | 内容 | 工作量 |
|---|---|---|
| **T1** | schema 修订(Question/Period/Benchmark 字段补全 + 双 wrapper 输出) | 0.5 天 |
| **T2** | MemoryInterface 适配器层(ABC + BenchmarkRunner + SimpleMemAdapter) | 1 天 |
| **T3** | Stage A v2 重写(V/T 启发式修复 + LLM hook + tie-breaker) | 1 天 |
| **T4** | **Stage D v2 重写**(消费 OpsProfile + LLM 生成新题)★ | **3 天** |
| **T5** | P0 bug 集合修复(qid/equiv/空答案/ingest_docs inline) | 1 天 |
| **T6** | Stage C v2(content inline + 修 timestamp 字段) | 0.5 天 |
| **T7** | run_pipeline_v2.py + 端到端 smoke test | 1 天 |
| **T8** | failmode_signature 实现 + verify M3/M4 标签符合毕设案例 | 1 天 |

**总计:9 天**(乐观估计;Stage D V2 是最大不确定性)

---

## 6. 关键决策原则

1. **LLM 是 first-class citizen,启发式只作 fallback**:Stage A/D 主路径走 LLM,LLM 失败再退到启发式 + 显式 warn
2. **OpsProfile 必须真消费**:Stage D 的产出必须严格符合 profile 配额(±20% 容忍),否则报错
3. **schema 必须自洽 + 自包含**:benchmark JSON 应能从任意 cwd 跑评测,不依赖外部 corpus 路径
4. **三重兼容是硬要求,不是 nice-to-have**:OfficeMem 老 evaluator 必须能直接读我们的 OfficeMem 兼容版 JSON
5. **failmode 标签必须可验证**:每条 M3/M4 题都必须有 `failmode_signature`,W2 评测时可以验证标签预测准确性

---

## 7. 引用

- 3 路 subAgent 审查报告(本对话历史,2026-05-28)
- 毕设第 3.5 节:MemoryInterface 接口定义
- 毕设第 4.5 节:实验流程
- 毕设第 5.4 节:M1-M6 失败模式真实案例
- `pipeline/` 当前 V1 代码(全部需要重写)
- simpleMem 实际位置:`/Users/ryanleory/proj/memory-systems-eval/simpleMem_src/`
- R1/R2/R3 adapter 已实现:`/Users/ryanleory/proj/memory-systems-eval/adaptors.py`

参见 [[operator_taxonomy]] [[failmode_taxonomy]] [[scenario_spec]] [[benchmark_output_schema]] [[scenario_dimensions]]。
