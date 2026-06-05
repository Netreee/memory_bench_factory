# Benchmark Output Schema 设计原则

> **角色**:本 anchor 定义我们 benchmark 生成 pipeline 的**输出接口**——生成的 benchmark JSON 必须满足什么要求。
> **三重兼容**:① 能直接喂毕设的 evaluation harness(9 系统 × 3 范式) ② 兼容 OfficeMem 的 `onpolicy_benchmark.json` schema ③ 携带完整的算子 + 失败模式标签。

---

## 1. 设计原则

### 1.1 三重兼容
- **下游兼容**:直接喂毕设 `eval/` 跑 9 systems × 3 R 范式
- **格式兼容**:核心字段沿用 OfficeMem `onpolicy_benchmark.json`(chain / period / qas)
- **诊断扩展**:新增算子标签 + 失败模式标签 + 多口径判分

### 1.2 必须保留 OfficeMem 已有的 chain 结构
OfficeMem 的 `(chain, period, qas)` 三层结构正好对应:
- chain = 一个完整的记忆主体(如"AI 工程周报"持续 10 周)
- period = 一个时间步(每周一篇文档)
- qas = 在这个 chain 上签发的题目集(每题在每个 period 都有"当时正确答案")

**这个结构天然支持 OnPolicy 评测**(按时序逐 period 写入,每 period 测一次)——是 CR / TTL 任务的标准范式。

### 1.3 必须新增 4 类标签
为了让下游可以按毕设的算子 / 失败模式做归因诊断:
1. **算子标签**:`(formation_op, evolution_op, query_op)`
2. **失败模式标签**:`target_failmode` + `failmode_signature`
3. **MAB 任务类型**:`task_type ∈ {AR, CR, TTL, LR}`
4. **多口径判分**:`judge_modes`(避免 M5 格式错位)

### 1.4 必须支持等价表达答案集合
针对 M5 格式错位,gold answer 不应该是单一字符串:
- 单值 `"UK"` → 多值 `["UK", "United Kingdom", "U.K.", "英国"]`
- 数值 `"31.3"` → 可加容差 `{"value": 31.3, "tolerance": 0.1}`

---

## 2. 顶层结构(沿用 OfficeMem + 扩展)

```
Benchmark
├── benchmark_id
├── scenario           ← ScenarioSpec 反向溯源(新增)
├── statistics         ← 算子/失败模式/题型 分布统计(新增)
└── chains
    └── Chain
        ├── chain_id
        ├── chain_name
        ├── cluster
        ├── period_count
        ├── periods
        │   └── Period
        │       ├── period_idx
        │       ├── timestamp
        │       ├── ingest_docs        ← 注入到记忆系统的文档列表
        │       └── state              ← 该 period 后的 ground truth 字段值
        └── qas
            └── Question
                ├── qid
                ├── question
                ├── answer_by_period   ← {period_idx: answer or [equiv_set]}
                ├── field_name         ← OfficeMem 保留
                │
                ├── formation_op       ← 新增(算子标签)
                ├── evolution_op       ← 新增
                ├── query_op           ← 新增
                │
                ├── target_failmode    ← 新增(失败模式标签)
                ├── failmode_signature ← 新增(M3 伪引用证据编号等指纹)
                │
                ├── task_type          ← 新增(MAB 4 子任务)
                └── judge_modes        ← 新增(多口径判分)
```

---

## 3. 与 OfficeMem schema 的差异点(W1 改造重点)

OfficeMem 的 `onpolicy_benchmark.json` 现有结构:
```json
{
  "chain_id": "T_0010_______",
  "chain_name": "AI工程周报",
  "cluster": "T_0010",
  "period_count": 10,
  "periods": [...],
  "qas": [
    {
      "qid": "onpolicy_T_0010________001",
      "field_name": "P0P1P2缺陷逃逸率本周",
      "question": "P0P1P2缺陷逃逸率本周是多少?",
      "answer_by_period": {"0": "20%", "1": "0%", ...}
    }
  ]
}
```

我们的 v4 schema 在 `qas[i]` 上额外加 6 个字段:
```json
{
  "qid": "...",
  "field_name": "...",
  "question": "...",
  "answer_by_period": {
    "0": ["20%", "20.0%", "20 percent"],   ← 等价表达集合(避免 M5)
    "1": ["0%", "0.0%", "无缺陷逃逸"],
    ...
  },

  "formation_op": "F2",          ← 该题预期需要 F2 或以上才能答对
  "evolution_op": "E4",          ← 该题预期需要 E4(或 E3+E4)才能答对
  "query_op": "Q1",              ← 该题预期 Q1 即可
  "task_type": "CR",             ← 这是冲突修正任务

  "target_failmode": "M3",       ← 预期会在扁平系统 R3 上触发 M3
  "failmode_signature": {
    "type": "fake_citation",     ← 检查是否伪造证据编号
    "common_default": "20%"      ← 常识默认值(M3 倾向回退到这个)
  },

  "judge_modes": ["EM", "fuzzy", "llm_judge"]
}
```

---

## 4. 与毕设 evaluation harness 的对应

毕设第 4.5 节定义的实验流程:`ingest → infer → eval → aggregation`

| 阶段 | 对接我们 schema 的方式 |
|---|---|
| **ingest** | 按 `chains[i].periods[j]` 顺序,把 `ingest_docs` 喂给 `MemoryInterface.ingest()` |
| **infer** | 对每个 `(qid, R 范式)` 调用 `MemoryInterface.retrieve()` + LLM 生成,记录 token + latency |
| **eval** | 用 `judge_modes` 中的每种口径都打一遍分,分别记录 |
| **aggregation** | 9 系统 × 3 范式 × 我们的 benchmark = 27 cells;按 `target_failmode` × `task_type` 切片做归因表 |

毕设 `MemoryInterface`(第 3.5 节)接口我们必须完全兼容:
```python
class MemoryInterface:
    def ingest(self, document: str, metadata: dict) -> None
    def retrieve(self, query: str, top_k: int = 10) -> List[Evidence]
    def update(self, memory_id: str, new_content: str) -> None  # optional
    def reset(self) -> None
```

我们的 `Period.ingest_docs[i]` 直接对应一次 `ingest()` 调用;`metadata` 至少包含 `timestamp` 和 `source`(毕设要求)。

---

## 5. statistics 字段(供论文实验表用)

```python
@dataclass
class BenchmarkStats:
    total_questions: int
    by_formation_op: dict[str, int]   # {"F1": 5, "F2": 8, "F3": 10, "F4": 7}
    by_evolution_op: dict[str, int]
    by_query_op: dict[str, int]
    by_target_failmode: dict[str, int]   # {"M1": 4, "M2": 5, "M3": 12, "M4": 6, "None": 3}
    by_task_type: dict[str, int]      # {"AR": 8, "CR": 14, "TTL": 5, "LR": 3}
    by_chain: dict[str, int]
```

论文实验表可以直接基于这些 stats 做 cross-tab(如 `target_failmode × system` 矩阵)。

---

## 6. 关键设计决策(已锁定)

| 决策点 | 选择 | 理由 |
|---|---|---|
| 是否兼容 OfficeMem schema | ✅ 兼容 | 复用其 chain/period/qas 三层,降低 W1 实现成本 |
| answer 单值 vs 等价集 | **等价集** | 避免 M5 格式错位的系统性低估 |
| 是否带算子标签 | ✅ 必带 | 这是与现有 benchmark 的核心差异点,SIGMOD 卖点 |
| 是否带失败模式标签 | ✅ 必带 | 同上 |
| 多选题 | ❌ **禁用** | 避免 M6 多选偏置(除非选项随机化 + 边际分布校正) |
| LLM-judge prompt | 与毕设保持一致 | 用毕设 4.3 节定义的 DeepSeek-Chat |
| 主答案匹配口径 | EM + fuzzy + LLM-judge 三套口径 | 跨口径分歧本身就是评测可解释性信号 |

参见 [[operator_taxonomy]] + [[failmode_taxonomy]] + [[scenario_spec]]。
