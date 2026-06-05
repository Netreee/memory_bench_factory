# 失败模式分类:M1–M6

> **来源**:任子谦《大模型记忆系统评测基准研究》(SJTU 学士学位论文,2026-05-13) 第 5.4 节 + 第 5.6 节。
> **用途**:我们 benchmark 生成 pipeline 的【题目失败模式标签】——每道题预先标注它考的是哪个失败模式,并通过"诱发该失败模式"的题型设计反向构造可诊断的 benchmark。

---

## 1. 概览

毕设在 9 系统 × 3 推理范式 × 4 基准 = 108 cells 的实验中,识别出 5 类反复出现的、与 R 轴升级直接关联的失败模式 (M1–M5),以及 1 类与评测设计耦合的横向问题 (M6)。每个 cell 由不同模式主导,**不存在通杀 R3 的失败原因**。

| 编号 | 名称 | 触发位置 | 典型 (system, task) | 与 R 升级的关系 |
|---|---|---|---|---|
| **M1** | 计划过拆 | R3 规划阶段过度细分 | AR / single-hop 上的 Plan-and-Act | R3 引入 |
| **M2** | 证据塌陷 | R3 检索阶段子查询改写 | RAPTOR × CR | R3 引入 |
| **M3** | 冲突默认化 | R3 反思阶段挑"自洽"版本 | **simpleMem × CR** | R3 引入 |
| **M4** | 干扰项改写 | R3 反思阶段倾向"语义近似" | **MemGPT × AR** | R3 引入 |
| **M5** | 格式错位 | 评测口径 vs 系统输出风格不一致 | 全 R 通用 | 与 R 无关 |
| **M6** | 多选偏置 | 评测设计 vs LLM 输出习惯 | AMemGym 全系统 | 与 R 无关 |

---

## 2. 每个失败模式详解

### M1 计划过拆 (Plan over-decomposition)

**触发条件**:R3 Plan-and-Act 把单跳事实题当作多跳推理题处理。

**机制**:
- 例:查询 "X 的配偶是谁" 这类原本一步可答的问题被拆成 "X 是谁" 与 "X 的配偶是谁" 两步
- 第一步过于宽泛的子查询召回了一组泛实体证据,反而把 R1 一步命中的目标切片挤出 top-K

**出现位置**:AR / single-hop 类查询;对多选题影响较小。**由规划阶段过度细分引起**。

**生成时怎么诱发**:出"一步可答但表面看起来需要分解"的事实题,如 "X 在哪个城市?",其中 X 是一个复合短语会被拆成"X 是谁/什么" + "X 的城市"。

---

### M2 证据塌陷 (Evidence collapse)

**触发条件**:层次摘要 (F4) 系统在多步检索 (R3) 下出现"证据收窄"。

**案例 (RAPTOR × CR, inst 5, question 17)**:
- 问 "Who is Charles Darwin married to?",CR 集注入答案 **Amala Paul**(标准答案被改成这个反常识值)
- R1 一步检索 (steps=1) 即命中目标切片 (chunk),输出 Amala Paul(正确)
- R3 经过 4 步检索、2 次重规划 (replan) 后,输出 "No relevant information available in the knowledge pool."(交白卷)

**机制**:每次检索调用更精细的子查询召回,UMAP 摘要节点把这条对抗性注入事实压缩进了较高层摘要,二次精化的子查询落到细节叶节点后反而召不到,候选集逐步偏离 R1 一步命中的那个切片,最终交白卷。**由检索阶段子查询改写引起**。

**出现位置**:F4 (RAPTOR/MemOS) × CR (含对抗性注入) × R3。

**生成时怎么诱发**:出"答案在低层叶节点(单个切片)但与高层摘要语义偏离"的题,迫使多步检索把候选集逐层抽离正确切片。要点:答案要"局部命中、全局偏离"。

---

### M3 冲突默认化 (Conflict defaulting) ⭐ 关键模式

**触发条件**:扁平 / Q1 + R3 反思阶段挑常识"自洽"版本,而非反常识修正版。

**案例 (simpleMem × CR, inst 7, question 78)**:
- 问 "Which city is the headquarter of University of Washington located in?"
- CR 集把标准答案从世界常识值 **Seattle** 修改为注入值 **Nuapada**
- R1 检索后直接输出 Nuapada(正确,因为忠实于注入证据)
- R3 经反思后输出 "The headquarters of University of Washington is located in the city of **Seattle**.",**并自作补充 "Note: Among the collected evidence, entry 3531 in [2] states this information",伪造了一个并不存在的证据编号**来支撑回退到常识默认值的结论

**机制**:R3 的 Plan-and-Act 末段含一个反思步骤 (reflection step),要求模型选出"最自洽"的候选;"自洽"在常识层面意味着挑常见版本而非反常识修正版。在 simpleMem × CR 这种 sub-match 评分较严苛的 cell 上,本案例呈现的 **"默认化 + 伪引用自证" 复合误差**是反协同 cell 的主要失败模态之一。

**出现位置**:F1/F2 + E1/E4 (扁平) × CR × R3。

**生成时怎么诱发**(这是我们 CR benchmark 的核心题型):
- 注入值与常识值必须不同(且常识值是 LLM 训练分布中的高频答案)
- 注入证据要清晰可定位(避免与 M2 误归因)
- **评测时要检测"是否伪造证据编号"作为额外的失败信号**(这是 M3 的指纹)

---

### M4 干扰项改写 (Paraphrase distractor)

**触发条件**:R3 多步反思过程倾向"语义近似"判断,把同义改写干扰项误读为目标答案。

**案例 (MemGPT × AR, inst 4, question 88)**:
- AR 任务为 6 选 1 事件续写多选题
- 正确选项 "Angel was confined to her bed due to fatigue." 是原文逐字 (verbatim) 片段
- 干扰项均为该句的一对一同义改写
- R1 (steps=1) 直接选中逐字选项(正确)
- R3 (steps=4) 选中干扰项 "Angel was restricted to her chair as a result of her illness.",逐词替换三处 (`confined → restricted, bed → chair, fatigue → illness`),同义改写完整命中干扰项

**机制**:AR 任务期待逐字字面回填,R3 的多步反思过程倾向偏向"语义近似"判断;当原文与同义改写并列为选项时,反思 (reflection) 阶段会把"更概念抽象"的改写版误读为目标答案。即便答案以明示选项给出,Plan-and-Act 同样倾向同义改写干扰项。

**出现位置**:F1/F2 系统(尤其 MemGPT)× AR(逐字回填)× R3。

**生成时怎么诱发**:出 "原文逐字 vs 同义改写" 的多选题,看系统是否被 R3 反思引导到同义干扰项。要点:干扰项必须是正确项的逐词同义替换。

---

### M5 格式错位 (Format mismatch)

**触发条件**:评测口径与系统输出之间的不一致。

**案例**:系统答出 "UK" 而标准答案标注 "United Kingdom",字面 sub-match 判错而语义实际正确。

**机制**:该模式与 R 升级无直接因果——R1 / R3 都可能踩。可通过模糊匹配 (fuzzy match) 或基于大模型的判分 (LLM-as-Judge) 修复。在跨系统比较绝对准确率时会带来一致的低估。

**对我们的启示**:
- **不是要"诱发"M5,而是要"避免"M5**(避免 benchmark 答案标注与系统输出风格耦合)
- 设计 verifier 时应该提供 **EM + sub-match + LLM-judge 多口径**,让下游可以分离评估
- 提供"等价表达"集合(如 `{"UK", "United Kingdom", "U.K.", "英国"}` 都标为对)
- 是 SIGMOD 论文里"评测口径"专题讨论的支点(毕设 6.3 节作铺垫)

---

### M6 多选偏置 (Option-A Bias)

**触发条件**:LLM 在多选题任务上倾向于输出靠前的选项,叠加评测时选项顺序未按题随机化。

**案例 (AMemGym, 全系统)**:
- AMemGym 是 6 选 1 多选题,均匀随机基线 16.7%
- 标准答案 (gold) 标签分布严重不均衡:`correct_idx` 取值 0/1/2/3/4/5 的频次分别是 28.5% / 23% / 23% / 16% / 8% / 1.5%
- "恒选 idx=0" 的常数预测器就能拿到 28.5%
- A-MEM 对 200 道题中有 **74% 输出 idx=0**,HippoRAG 与 RAPTOR 分别为 47.5% 和 47.0%
- A-MEM 几乎从不输出 idx=1 (0%) 或 idx=5 (0%)
- 条件概率 `P(correct | pred=0) ≈ 29%`,与标准答案 = 0 的边际概率 28.5% 在统计上无差异
- **当这些系统"选 A"时,并不是在用检索证据做判断,而是在做与标准答案边际分布一致的常数预测**
- 9 系统中只有 Mem0 (离策略 off-policy 评分 36.5%) 显著突破恒选 A 基线

**机制**:评测设计与 LLM 输出习惯耦合的横向问题。使评分指标坍缩为"系统是否复现标准答案边际分布"而非"系统是否做对推理"。

**对我们的启示**:
- **绝对避免** 在我们生成的 benchmark 里用多选 + 固定选项顺序的题型
- 如果一定用多选,**选项顺序必须按题随机化**,且报告时要分离 `P(correct | pred=idx)` 与 `P(gold=idx)` 的边际/条件概率
- 优先用 **开放式问答 + LLM-judge** 或 **抽取式 EM** 来规避此偏置

---

## 3. M-R 关系汇总

毕设第 5.4 节明确:"不同 (system, task) cell 由不同模式主导,并不存在一个通杀 R3 失败原因"。

| 失败模式 | M 形态偏好 | 触发 R 阶段 | 缓解策略(对系统) |
|---|---|---|---|
| M1 | 任意 | R3 规划过细分 | 题型路由(单跳题降级回 R1) |
| M2 | F4(分层) | R3 检索改写 | R2 适度迭代 |
| M3 | F1+E1(扁平 + 只增不减) | R3 反思 | E3+E4+Q2 复合演化 |
| M4 | F1/F2 | R3 反思 | EM-style 评分而非语义近似 |
| M5 | 全 | 任意 R | 评测口径多重判分(EM + LLM-judge) |
| M6 | 全 | 任意 R | 多选随机化 + 边际分布对照 |

---

## 4. 对我们 Pipeline 的具体应用

### 4.1 失败模式 × 算子 签发矩阵

```
            生成时该测的 (F, E, Q) 算子组合     诱发该失败模式的题型设计
M1 计划过拆  全 𝓕 × R3 升级                   "看似多跳实则单跳"的事实题
M2 证据塌陷  F4 × R3                          答案在叶节点但与高层摘要偏离
M3 冲突默认  F1/F2 + E1 × R3 × CR             注入"反常识"答案 + 清晰证据
M4 干扰改写  F1/F2 × R3 × AR                  原文逐字 vs 同义改写多选
M5 格式错位  评测口径,非生成                  提供等价表达集合
M6 多选偏置  评测设计,非生成                  避免固定顺序多选
```

### 4.2 题目签发的数据结构(对应 schema.py 设计)

```python
@dataclass
class Question:
    qid: str
    question: str
    gold_answer: str | list[str]    # 支持等价表达集合,避免 M5

    # 算子标签(来自毕设 3.3.1 节)
    formation_op: Literal["F1", "F2", "F3", "F4"]
    evolution_op: Literal["E1", "E2", "E3", "E4"] | None
    query_op: Literal["Q1", "Q2", "Q3"]

    # 失败模式标签(预测它会触发哪个 M)
    target_failmode: Literal["M1", "M2", "M3", "M4"] | None
    failmode_signature: dict | None  # 如 M3 的"伪引用证据编号"指纹

    # MAB 4 子任务(来自毕设 4.2 节)
    task_type: Literal["AR", "CR", "TTL", "LR"]

    # 多口径判分(避免 M5)
    judge_modes: list[Literal["EM", "sub_match", "fuzzy", "llm_judge"]]
```

### 4.3 预测对照表(SIGMOD 实验关键)

我们生成的 benchmark 的预测 vs 毕设实测:

| target_failmode | 应该在哪些系统上触发(预测) | 对应毕设观察 |
|---|---|---|
| M1 | 全系统 × R3 | 毕设第 5.4.4 节 |
| M2 | RAPTOR / MemOS × R3 | 毕设第 5.4.2 节(F4+R3 证据收窄) |
| M3 | simpleMem / Mem0 / MemGPT / MIRIX / A-MEM(扁平)× R3 × CR | 毕设第 5.4.1 节(simpleMem × CR) |
| M4 | MemGPT × R3 × AR(典型),全 F1/F2 系统 | 毕设第 5.4.3 节 |

**实验验证**:跑 9 系统 × R1/R2/R3 看是否符合预测 → 如果符合,**证明我们的失败模式标签有效**,这是 SIGMOD 卖点的硬证据。

参见 [[operator_taxonomy]]。	
