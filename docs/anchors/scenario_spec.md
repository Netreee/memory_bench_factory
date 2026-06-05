# ScenarioSpec:Pipeline 输入 Schema 设计原则

> **角色**:本 anchor 定义我们 benchmark 生成 pipeline 的**输入接口**——用户用什么形态把"一个场景"喂给 pipeline。
> **关键决策**(用户已确认):**只给 description + corpus_samples,不给 QA examples**,让 pipeline 从 corpus 自己推断"该场景需要测什么"。

---

## 1. 设计原则

### 1.1 最小输入,最大推断
- 用户只提供 **场景描述** 和 **少量代表性文档**;**不提供 QA examples**
- Pipeline 责任:**自动**从 corpus 推断
  1. 该场景的 ingest 形态(文档结构、字段、时序节奏)
  2. 该场景的记忆主体(项目/客户/团队/业务线/个人)
  3. 该场景的评测视角(first-person / PM / leader / audit / successor)
  4. 该场景应测的 **失败模式 × 算子配额**(15 cells)
  5. 该场景应测的 **MAB 任务类型配比**(AR / CR / TTL / LR)

### 1.2 为什么不给 QA examples
- 给 QA 会引入"循环自证"——pipeline 学到的是 example 的表面形态而非场景真实结构
- 给 QA 会导致"模式锁定"——pipeline 套用 example 模板,无法跨场景泛化
- "few-shot 输入"的真正卖点是:**用最少信号撬动整套带标签 benchmark**

### 1.3 为什么需要 corpus_samples 而不只是 description
- description 只能传达"是什么场景",无法传达"长什么样"
- corpus_samples 让 pipeline 学到 ingest 通道的真实分布(字段、长度、密度、时间标签)
- 后续 W1 实验:**OfficeMem 1899 篇中只采样 5 篇喂给 pipeline,看能否泛化出与原版 benchmark 排名相关的题集**

---

## 2. 字段需求清单

### 必填字段

| 字段 | 类型 | 用途 |
|---|---|---|
| `name` | str | 场景标识(如 `office_engineering_weekly`) |
| `description` | str(1-3 段) | 场景的自然语言描述 |
| `corpus_samples` | list[Document](3-5 篇) | 代表性原始文档(markdown 或纯文本) |

### Document 内部字段

| 字段 | 类型 | 用途 |
|---|---|---|
| `doc_id` | str | 唯一标识 |
| `title` | str | 文档标题 |
| `content` | str | 原始 markdown 内容 |
| `metadata.timestamp` | ISO date | **关键**:用于时序排序 |
| `metadata.author` | str | 可选,用于推断视角 |
| `metadata.doc_type` | str | 可选,用于推断 ingest 通道(周报/会议纪要/技术方案/邮件...) |

### 可选字段(影响 pipeline 推断)

| 字段 | 类型 | 用途 |
|---|---|---|
| `temporal_pattern` | enum | `weekly` / `monthly` / `daily` / `event_driven` / `none`,影响 chain 切分 |
| `target_size` | int | 期望生成多少道题(默认 ~30) |
| `subject_hint` | str | 记忆主体提示("项目/团队/客户/业务线"),pipeline 自动识别也可不给 |
| `perspective_hint` | str | 评测视角提示,pipeline 自动识别也可不给 |

---

## 3. 与 OfficeMem-as-few-shot 的对应

OfficeMem 现有 1899 篇真实办公文档,可以**自然地充当 W1/W2 的 ScenarioSpec 来源**:

```
ScenarioSpec_office = ScenarioSpec(
    name="office_engineering_weekly",
    description="""
    企业内部每周工程周报系列,持续 8-12 周。每篇周报由项目负责人撰写,
    汇报当周的核心字段进展(缺陷逃逸率、Oncall 数量、CPU 容量、性能指标等)。
    字段值会随时间变化,新值会覆盖旧值。评测应站在业务线 leader 视角,
    关注"当前的真实值是什么"以及"什么时候变过/被什么事件触发"。
    """,
    corpus_samples=[
        # 从 1899 篇中采样 3-5 篇代表性周报
        load_doc("human_readable_md/Ct8Nd03lDoHuw1xfX9Lcjw0Ennc/"),  # 周报 1
        load_doc("human_readable_md/S0FadM6JJo8I8fx1huxcvUUWnVd/"),  # 周报 2
        load_doc("human_readable_md/PhUAd0njrofnMHx8Vycc0SLGnDf/"),  # 技术方案
        # ...
    ],
    temporal_pattern="weekly",
    target_size=30,
)
```

**关键实验**(W2):
- Pipeline 用 ScenarioSpec_office(只 5 篇 corpus)生成 benchmark_v3
- 跑毕设 9 baselines × 3 R 范式 = 27 cells,得到排名 Rank_v3
- OfficeMem 原版 benchmark (用 1899 篇全量) 跑同样 27 cells,得到排名 Rank_orig
- 看 Spearman(Rank_v3, Rank_orig)
- **≥ 0.85** → 证明 "few-shot 泛化" 能力是真的 ← SIGMOD 卖点核心

---

## 4. Pipeline 内部对 ScenarioSpec 的处理(高层)

```
ScenarioSpec
    ↓
[Stage A] Corpus 理解
    ├─ A1: 文档结构识别(字段、时序、author)
    ├─ A2: ingest 通道分类(周报 / 会议 / 邮件 / 工单 / ...)
    └─ A3: 主体识别(项目 / 团队 / 客户 / 业务线 / ...)
    ↓
[Stage B] 失败模式 × 算子 配额推断
    ├─ B1: 根据 corpus 推断该场景常见的 MAB 任务类型分布(AR/CR/TTL/LR)
    ├─ B2: 推断 (𝓕, 𝓔, 𝓠) 算子覆盖需求
    └─ B3: 推断 5 失败模式应在哪些题上诱发
    ↓
[Stage C] Corpus 扩展(从 corpus_samples 学分布,生成大批 corpus)
    ↓
[Stage D] 题目签发(按 15 cells 配额生成,每题带完整标签)
    ↓
[Stage E] 输出 Benchmark(对齐 OfficeMem schema + 毕设接口)
```

---

## 5. 与可选输入的优雅退化

| 用户给的 | Pipeline 行为 |
|---|---|
| 只给 description | 警告"corpus_samples 缺失会影响真实感",仍可生成,但质量降级 |
| 给 description + 1 篇 corpus | 警告"corpus_samples 过少",pipeline 内部用 LLM 扩 corpus(但要 disclose) |
| 给 description + 3-5 篇 corpus | **标准输入**,pipeline 最佳表现 |
| 给 description + 全量 corpus | OK,但浪费;pipeline 内部仍会采样,等价于上一行 |
| 给 description + corpus + QA examples | **拒绝**(返回错误),提示用户:"我们不接受 QA 示例,以避免模式锁定" |

参见 [[operator_taxonomy]] + [[failmode_taxonomy]] + [[benchmark_output_schema]]。
