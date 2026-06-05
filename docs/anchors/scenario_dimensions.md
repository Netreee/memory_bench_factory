# Scenario Dimensions:场景的 5 维正交空间

> **角色**:回答"别的场景怎么办"的核心 anchor——把"场景"形式化为 `(I, S, V, T, O)` 5 维空间,告诉 pipeline 如何理解、分类、对齐**任意**场景。
> **来源**:综合 SubAgent 3 调研(DRBench / Spider 2.0 / WorkArena++ / AgentBench / RealMem 反面教材)+ 任子谦毕设的算子/失败模式标签。
> **关键决策**:用户**不直接输入维度**(避免 RealMem 11 个手工模板的陷阱),pipeline 从 description + corpus_samples 自动推断。

---

## 1. 设计动机

### 1.1 RealMem 的反面教材
RealMem(arXiv 2601.06966)人工设计了 11 个场景:Travel Planning / Financial Planning / Project Management / Academic Writing / Literary Creation / Career Development / Knowledge Learning / Code Architecture / Fitness / Mental Health / Health Consultation。但:
- **11/11 都是单用户长程对话**(I=对话,S=个人,V=first-person)
- 只换 persona/项目主题,ingest 形态零差异
- 它甚至有 "Code Architecture Design" 场景但**不用代码 ingest**,硬塞成对话
- 没有 scenario schema,没有"如何加第 12 个场景"的讨论

**根本问题**:把"场景"理解成"换个 topic",而不是"换个 (I, S, V, T) 组合"。

### 1.2 DRBench 的范式启示(金矿)
DRBench(arXiv 2510.00172)每个 task 是显式四元组 `(C, Q, ℐ, Files)`:
- **C** = 公司 profile + 用户 persona
- **Q** = research question
- **ℐ** = `{私有洞察, 干扰洞察, 公开洞察}`
- **Files** = **异构文件集**(PDF / DOCX / PPTX / XLSX / 邮件 / Mattermost 聊天 / Nextcloud 网盘)
- **3 行业 × 10 职能域** = 真正的多通道异构

DRBench 做的是 deep research,**不是 memory**。我们移植它的范式:
- C → 评测视角 + 记忆主体 (V + S)
- ℐ → 记忆图(带 update/conflict/forgotten/consolidates)
- Files → 按 ingest 通道分桶的异构 corpus (I)
- 加 (T) 时间动力学 + (O) 记忆操作配额 → 5 维

---

## 2. 5 维正交空间

```
Scenario := (I, S, V, T, O)
```

| 维度 | 名称 | 含义 | 离散取值(可扩展) |
|---|---|---|---|
| **I** | IngestChannels | ingest 通道集合(幂集) | `{对话, 会议纪要, 周报/日报, 长文档(SOP/合同/财报), 邮件/IM, 代码diff/PR, 工单/CRM, 表格/仪表板, 语音转写, ...}` |
| **S** | MemorySubject | 记忆主体("记的是谁/什么") | `{个人, 客户, 项目, 业务线, 团队, 合同/matter, 代码仓, 产品 SKU, 案件, 合规事项, ...}` |
| **V** | Perspective | 评测视角("以谁的身份问") | `{first-person, 直属负责人(line-manager), 跨业务线 leader, partner/客户, 审计/合规, 接班人, 外部分析师, ...}` |
| **T** | TemporalDynamics | 时间动力学 | `{偏好渐变, 项目阶段切换, 工单状态机, 版本里程碑, 财报周期, 突发事件, ...}` |
| **O** | MemoryOpsProfile | 记忆操作密度配额向量 | `(𝓕, 𝓔, 𝓠) × (M1-M4)` 15 cell 配额向量(见 [[failmode_taxonomy]]) |

### 2.1 正交性论证(SIGMOD 审稿人必问)
5 维相互独立可证:
- **同一 I 可换 S**:同一堆会议纪要,从"项目"主体看 vs 从"客户"主体看
- **同一 S 可换 V**:项目主体下,PM 视角问"任务/blocker" vs 业务线 leader 视角问"里程碑/趋势"
- **同一 V 可换 T**:PM 视角看周迭代 vs 看里程碑
- **同一 (I,S,V,T) 可调 O**:同份 corpus,失败模式配额不同 → 题集不同

→ **N 场景 ≠ N 维乘积穷举,而是 5 维空间采样的 N 个点**

→ 直接破解审稿人 "为什么是 11 不是 12" 的死亡问题

### 2.2 现有 benchmark 在 5 维空间的覆盖图

| benchmark | I | S | V | T | O |
|---|---|---|---|---|---|
| LoCoMo | `{对话}` | `{个人}` | `{first-person}` | 偏好渐变 | 单一(召回为主) |
| LongMemEval | `{对话}` | `{个人}` | `{first-person}` | 偏好渐变 | 5 维(IE/MR/TR/KU/ABS) |
| RealMem | `{对话}` | `{个人}` | `{first-person}` | 11 种 topic | 4 query 类型 |
| MemoryAgentBench | `{对话, 长文档}` | `{个人}` | `{first-person}` | 多种 | 4 维(AR/CR/TTL/LR) |
| OfficeMem(参考) | `{周报, 长文档}` | `{项目}` | `{first-person?}` | 周迭代 | 单一(CR) |
| AMA-Bench | `{程序化环境}` | `{个人}` | `{first-person}` | in-episode | retrieve+update |

**它们都堆在 `I={对话}, S=个人, V=first-person` 那个角落**(SubAgent 3 调研明确证实)。

OfficeMem 已经走出 `I={对话}` 一步(周报+长文档),但 `S/V/T` 仍未维度化。

**我们的空位** = 真正的 `I` 多通道 × `S` 多主体 × `V` 多视角 × `T` 多动力学 × `O` 多算子配额 组合。

---

## 3. 维度自动推断(Pipeline Stage A 内部)

用户**只给 description + corpus_samples**,pipeline 内部从这两个信号推断 5 维:

| 维度 | 推断信号 | 推断方法 |
|---|---|---|
| **I** | `metadata.doc_type` + content 结构(表格/列表/段落比例)+ 文件后缀 | LLM 分类 + 启发式规则 |
| **S** | 实体频次(谁/什么在 corpus 里反复出现)+ description 显式提示 | NER + LLM judge |
| **V** | description 显式提示 + author/audience 关系 | LLM 推断 |
| **T** | `metadata.timestamp` 的节律(每天/每周/事件驱动)+ 文档之间的引用关系 | 时序统计 + LLM |
| **O** | 由 (I, S, V, T) 推断 + 该场景常见失败模式预测 | 启发式映射表(下表) |

### 3.1 启发式映射表:(I, S, V) → 推荐 O 配额

| (I, S, V) 典型组合 | 推荐 O 配额重点 | 真实场景对应 |
|---|---|---|
| (对话, 个人, first-person) | M3 冲突默认化(LoCoMo 风格红海) | 个人助理 |
| (周报+长文档, 项目/业务线, leader) | M2 证据塌陷 + M3 冲突默认化 | OfficeMem 工程周报 |
| (邮件+工单, 客户, 审计) | M1 计划过拆 + M5 格式错位 | 客服 ticket 系统 |
| (代码 diff+PR, 代码仓, 接班人) | M2 证据塌陷 + M4 干扰项改写 | Code review / 交接 |
| (合同+财报+邮件, matter, partner) | M2 证据塌陷(异构源)+ M3 | Legal agent(Harvey 风格) |
| (会议纪要+周报+IM, 团队, leader) | M2 + M3 + M4 综合 | 项目交接 |

---

## 4. 实验设计:维度敏感性 ablation(SIGMOD 卖点核心)

### 4.1 维度敏感曲线(W3-W4)
**同一份 OfficeMem corpus,只换 V**:
- PM 视角问"任务/blocker/owner" → 题集 A
- 业务线 leader 视角问"里程碑/趋势/资源" → 题集 B
- 跑 9 baseline × 3 R 范式,看 cells 分布
- **如果 A vs B 的系统排名 Spearman < 0.5,证明视角变了 benchmark 真的变了**

### 4.2 维度组合采样(W4-W5)
从 5 维空间显式采样 N 个不同组合:
```
1. (对话, 个人, first-person, 偏好渐变, M3-heavy)     ← LoCoMo 复现 baseline
2. (周报, 业务线, leader, 项目阶段, M2+M3)            ← OfficeMem 风格
3. (邮件+工单, 客户, 审计, 工单状态机, M1+M5)         ← 客服场景
4. (代码 diff+PR, 代码仓, 接班人, 版本里程碑, M2+M4)  ← 代码 review
5. (合同+财报+邮件, matter, partner, 突发事件, M2+M3) ← Legal
...
```

每个组合跑完整 pipeline → 9 baseline × 3 R = 27 cells → 失败模式归因表

### 4.3 覆盖率图(SIGMOD 论文标志性图)
在 5 维空间画出:
- 现有 benchmark(LoCoMo / LongMemEval / RealMem / MemoryAgentBench / AMA-Bench)的覆盖区域(全堆在一个小角落)
- 我们 N 个场景的覆盖区域(铺开)
- 视觉直观的"我们覆盖了新格子"

→ 这是审稿人最买账的"非 cherry-pick"硬证据,**没有这张图论文撑不起来**。

---

## 5. 与现有 anchor 的关系图

```
ScenarioSpec(输入)                          Benchmark Output(输出)
  description                              Question.formation_op  ┐
  corpus_samples                           Question.evolution_op  ├─ O 维度
                                           Question.query_op      │
                                           Question.target_failmode┘
       │                                            ▲
       │ Stage A:维度自动推断                       │
       ▼                                            │
  (I, S, V, T) ─── 启发式映射表 ──→ O 配额 ─────────┘
       │                                            
       │ Stage B-E:按 O 配额签发题
       ▼
  Benchmark JSON
```

| anchor | 涉及维度 |
|---|---|
| [[operator_taxonomy]] | (𝓕, 𝓔, 𝓠) = O 维度的底层操作 |
| [[failmode_taxonomy]] | M1-M6 = O 维度的输出标签 |
| [[scenario_spec]] | I/S/V/T 自动推断的【输入接口】 |
| [[benchmark_output_schema]] | 每道题带的标签 = O 维度的【输出格式】 |
| **本 anchor** | **I/S/V/T/O 五维空间的整体抽象** |

---

## 6. 对 W1-W6 的具体指引

| 阶段 | 维度相关动作 |
|---|---|
| **W1** | Pipeline 必须实现 **Stage A 维度推断模块**(description + corpus → (I, S, V, T))+ 启发式映射表 |
| **W2** | 用 OfficeMem corpus 测试维度推断准确率(人工标注 5 维 ground truth vs pipeline 推断) |
| **W3** | **维度敏感性 ablation**:同 OfficeMem corpus 不换 V 跑两份题集,看排名 Spearman |
| **W4** | **维度组合采样**:从 5 维空间采至少 4 个不同组合,验证 pipeline 真泛化 |
| **W5** | **覆盖率图**:5 维空间里现有 benchmark vs 我们的覆盖区域可视化 |
| **W6** | 论文写作的核心 figure 与 framing 围绕本 anchor 的 5 维抽象展开 |
