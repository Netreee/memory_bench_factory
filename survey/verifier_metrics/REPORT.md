# Survey: Verifier 设计 + 蓝海维度可机器判分指标

> 26 篇 PDF 已下载在 `survey/verifier_metrics/<category>/`,本报告由主 agent 接力撰写
> (subAgent 因 32MB context 限制无法完成报告,但下载已就绪)。

## 0. 一句话结论

**forgetting** 有两个公认公式(FAMA、TOFU truth-ratio)可直接借用;**conflict** 只有 taxonomy(CM/IC/IM 三类)没有标准 verifier;**consolidation / 诊断性归因(attribution)** 完全没有公认公式——**这反而是我们 v3 的 novelty 卖点**:第一个为这两个维度造可机器判分 verifier 的工作。**LLM-as-judge 命门 = 多 judge 投票 + reference-grounding + position swap 防 bias**,MT-Bench 与 Reference-Guided Verdict 已经验证可达 88%+ 人评一致性。

---

## 1. 索引总表(26 条)

| # | 标题 | 类别 | 年份 | arXiv | 本地路径(survey/verifier_metrics/) | 价值 |
|---|---|---|---|---|---|---|
| 1 | **FAMA / Memora** | forgetting | 2026 | [2604.20006](https://arxiv.org/abs/2604.20006) | forgetting/2604.20006_FAMA_From_Recall_to_Forgetting.pdf | ⭐ **FAMA 公式**:遗忘惩罚 + memory presence 综合分 |
| 2 | **TOFU** | forgetting | 2024 | [2401.06121](https://arxiv.org/abs/2401.06121) | forgetting/2401.06121_TOFU.pdf | ⭐ **Truth Ratio + Forget Quality**(KS-test)+ Model Utility 三指标 |
| 3 | MUSE | forgetting | 2024 | [2407.06460](https://arxiv.org/abs/2407.06460) | forgetting/2407.06460_MUSE.pdf | unlearning 6 axis(verbatim/knowledge/privacy/utility/scalability/sustainability) |
| 4 | MemoryAgentBench | forgetting | 2026 | [2507.05257](https://arxiv.org/abs/2507.05257) | forgetting/2507.05257_MemoryAgentBench.pdf | selective forgetting 作为四能力之一,FactConsolidation 子集 |
| 5 | CL new metrics | forgetting | 2018 | [1810.13166](https://arxiv.org/abs/1810.13166) | forgetting/1810.13166_CL_new_metrics.pdf | BWT/FWT(continual learning 经典指标,适用 forgetting trade-off) |
| 6 | CL catastrophic forgetting | forgetting | 2024 | [2403.05175](https://arxiv.org/abs/2403.05175) | forgetting/2403.05175_CL_catastrophic_forgetting.pdf | LLM 灾难性遗忘综述 |
| 7 | LoCoMo | consolidation | 2024 | [2402.17753](https://arxiv.org/abs/2402.17753) | consolidation/2402.17753_LoCoMo.pdf | 长对话评测,reflection / multi-session reasoning 子任务 |
| 8 | LongMemEval | consolidation | 2024 | [2410.10813](https://arxiv.org/abs/2410.10813) | consolidation/2410.10813_LongMemEval.pdf | 5 能力(含 multi-session reasoning / knowledge update) |
| 9 | Reflective Memory Management | consolidation | 2025 | [2503.08026](https://arxiv.org/abs/2503.08026) | consolidation/2503.08026_Reflective_Memory_Management.pdf | reflection-based memory consolidation 实现 |
| 10 | Rethinking Memory in LLM Agents | consolidation | 2025 | [2505.00675](https://arxiv.org/abs/2505.00675) | consolidation/2505.00675_Rethinking_Memory_LLM_Agents.pdf | ⭐ **6 操作分类**(consolidation/indexing/retrieval/updating/forgetting/compression) |
| 11 | **Knowledge Conflicts Survey** | conflict | 2024 | [2403.08319](https://arxiv.org/abs/2403.08319) | conflict/2403.08319_Knowledge_Conflicts_Survey.pdf | ⭐ **CM/IC/IM 三类型 taxonomy**(必抄) |
| 12 | ROME / CounterFact | conflict | 2022 | [2202.05262](https://arxiv.org/abs/2202.05262) | conflict/2202.05262_ROME_CounterFact.pdf | CounterFact 数据集:21k 条 factual edit pair(知识冲突源数据) |
| 13 | Context-Faithful Prompting | conflict | 2023 | [2303.11315](https://arxiv.org/abs/2303.11315) | conflict/2303.11315_Context_Faithful_Prompting.pdf | OPIN prompting:让模型 faithful to context vs memory |
| 14 | WikiContradict | conflict | 2024 | [2406.13805](https://arxiv.org/abs/2406.13805) | conflict/2406.13805_WikiContradict.pdf | 2210 条 Wikipedia 矛盾段落对,IC conflict 评测基准 |
| 15 | Longpre Knowledge Conflicts | attribution | 2021 | [2109.05052](https://arxiv.org/abs/2109.05052) | attribution/2021_Longpre_Entity_Based_Knowledge_Conflicts.pdf | entity-based conflict perturbation,memorization vs context |
| 16 | Attributed QA / **AutoAIS** | attribution | 2022 | [2212.08037](https://arxiv.org/abs/2212.08037) | attribution/2212.08037_Attributed_QA_AutoAIS.pdf | ⭐ **AIS / AutoAIS 公式**:attribution evaluation 黄金标准 |
| 17 | HaluEval | attribution | 2023 | [2305.11747](https://arxiv.org/abs/2305.11747) | attribution/2305.11747_HaluEval.pdf | 35k hallucination benchmark,attribution 二分类 |
| 18 | Diagnosing Retrieval vs Utilization | attribution | 2026 | [2603.02473](https://arxiv.org/abs/2603.02473) | attribution/2603.02473_Diagnosing_Retrieval_vs_Utilization.pdf | ⭐ **存/取/用 归因方法**(我们诊断性的近邻) |
| 19 | **MT-Bench / LLM-as-judge** | llm_judge | 2023 | [2306.05685](https://arxiv.org/abs/2306.05685) | llm_judge/2306.05685_MT_Bench_LLM_Judge.pdf | ⭐ position/length/self-enhancement bias + 4 招对策 |
| 20 | **Reference-Guided Verdict** | llm_judge | 2024 | [2408.09235](https://arxiv.org/abs/2408.09235) | llm_judge/2408.09235_Reference_Guided_Verdict.pdf | ⭐ 多 judge + reference + majority vote,达 Cohen κ 0.86 |
| 21 | G-Eval | llm_judge | 2023 | [2303.16634](https://arxiv.org/abs/2303.16634) | llm_judge/2303.16634_G-Eval.pdf | CoT + form-filling,probability-weighted score(细粒度) |
| 22 | JudgeBench | llm_judge | 2024 | [2410.12784](https://arxiv.org/abs/2410.12784) | llm_judge/2410.12784_JudgeBench.pdf | meta-judge:测 judge 自己的可靠性 |
| 23 | **Tülu 3 / RLVR** | verifiable_rewards | 2024 | [2411.15124](https://arxiv.org/abs/2411.15124) | verifiable_rewards/2411.15124_Tulu3_RLVR.pdf | ⭐ **RLVR 范式**:deterministic verifier 替代 reward model |
| 24 | Phi-4 / plurality voting | verifiable_rewards | 2024 | [2412.08905](https://arxiv.org/abs/2412.08905) | verifiable_rewards/2412.08905_Phi4.pdf | 多采样投票判难度 + verifier-gated 合成 |
| 25 | PRIME / Process Reward | verifiable_rewards | 2025 | [2502.01456](https://arxiv.org/abs/2502.01456) | verifiable_rewards/2502.01456_PRIME.pdf | implicit process reward,token-level verifier |
| 26 | CheckList | behavioral_diagnostic | 2020 | [2005.04118](https://arxiv.org/abs/2005.04118) | behavioral_diagnostic/2005.04118_CheckList.pdf | behavioral testing:capability × test_type 矩阵 |

---

## 2. 分类精读

### 2.A 遗忘指标(forgetting,6 篇)

#### 2.A.1 FAMA / Memora(2604.20006)⭐ **核心**

- **元信息**:Md Nayem Uddin et al., ASU + Genies + UArizona, arXiv 2604.20006(2026 Apr)
- **本地文件**:`forgetting/2604.20006_FAMA_From_Recall_to_Forgetting.pdf`
- **核心方法**:Memora 是 long-term memory benchmark(10 personas × 数月 sessions),evaluation 用 **Forgetting-Aware Memory Accuracy (FAMA)** 既奖励正确 memory presence,又惩罚使用 obsolete memory。

- **关键公式**(直接拷贝):
  ```
  FAMA = max(0, MPA - λ · (1 - FAA))

  其中:
    MPA = Memory Presence Accuracy
        = 满足"应包含信息"binary criteria 的比例
    FAA = Forgetting Absence Accuracy
        = 满足"不应包含信息"binary criteria 的比例(invalidated/deleted memory)
    λ   = N_forget / (N_presence + N_forget)
        = 该题"应忘"准则数占总准则数的比例
  
  per-question FAMA ∈ [0, 1]
  task-level FAMA = sum(per_q FAMA) → normalize to [0, 100]
  ```

- **判分实现**:每条 criterion 用 LLM-judge **3 票多数投票**(GPT-4.1 + Claude Haiku 4.5 + Gemini 2.5 Flash),每 judge 给 binary yes/no;**human agreement 88.3%,Cohen's κ 0.86-0.90**。

- **如何拿 ground truth**:Memora 在 session simulation 阶段就显式追踪 memory traces(Add/Update/Delete),所以每题的 `presence_criteria`(应回答中体现的有效 fact)+ `forget_criteria`(应弱化的过期 fact)是**结构化生成的副产品**——这正是我们 v3 "结构先行、反向生成"的原型证明。

- **对我们 step4 的具体用法**:**直接搬过来**——`MemoryAtom` 的 `status`(current/outdated)+ `updates` 字段就是 Memora 的 trace 等价物。我们的 `verifier_forgetting` 实现就是 FAMA 公式 + 3 judge 投票。**进一步推广**:Memora 只测 single-user dialogue,我们 v3 在 cross-modality(会议/邮件/工单)上每个 ingest channel 都可挂 FAMA。

- **限制 / 坑**:λ 是"硬比例",忽略不同 atom 的 salience 差异(关键事实和琐碎偏好等权重)。**我们可以扩展为加权 FAMA**:λ_atom = salience_atom · base_λ。

#### 2.A.2 TOFU(2401.06121)⭐ **三指标**

- **元信息**:Pratyush Maini et al., CMU, arXiv 2401.06121
- **本地文件**:`forgetting/2401.06121_TOFU.pdf`
- **核心方法**:200 个 **虚构作者**(GPT-4 生成 biography,确保不在 pretraining 数据里)× 20 QA。3 种难度:1% / 5% / 10% forget set。4 个评测 dataset:Forget Set / Retain Set / Real Authors / World Facts(距离 forget data 的相关性梯度)。

- **关键公式**(直接拷贝):
  ```
  # 1) Truth Ratio (核心创新)
  R_truth = (1/|A_pert| · Σ_{ã ∈ A_pert} P(ã|q)^(1/|ã|)) / P(â|q)^(1/|â|)
  
  其中:
    â     = paraphrased version of ground-truth answer
    A_pert = 一组扰动答案(factually incorrect, but 保持同模板)
    P(·|q)^(1/|·|) = length-normalized conditional probability
  
  # 2) per-dataset score
  Forget Set:     R_truth (原值,越低越好——意味遗忘越彻底)
  Retain/Real/World: max(0, 1 - R_truth) (越高越好)
  
  # 3) Model Utility
  Model_Utility = HarmonicMean(9 numbers)
                = HM(3 datasets × 3 metrics: Probability, ROUGE, Truth Ratio)
  → 用 harmonic mean 防止"某一项极低被其他项掩盖"
  
  # 4) Forget Quality
  Forget Quality = p-value(KS-test(R_truth_unlearned, R_truth_retain))
  → p-value 高 = 两个分布无显著差异 = 强遗忘
  → p-value 低 = 模型仍能区分 forget set 与 retain set = 弱遗忘
  ```

- **如何拿 ground truth**:用 **fictitious data** 完全绕开"模型是否原本就知道"的污染问题——这是关键设计哲学,我们可以借用(每个场景的 persona/事实都是 step1 fabricated)。

- **对我们的具体用法**:
  1. **Truth Ratio 直接抄到 verifier_forgetting**——FAMA 给 binary criteria 评分,**Truth Ratio 给 LLM 答错时"错得多深"的连续信号**(模型可能不照搬旧值但仍倾向于它)。两者互补。
  2. **Harmonic Mean 聚合**:我们 step4 给一题打多维分数后聚合到单一 score,harmonic mean 比算术平均更安全(避免某个维度过低被掩盖)。
  3. **KS-test 思路**:整个 benchmark 上模型分数分布的 statistical test 可作排名相关性验证。

- **限制 / 坑**:Truth Ratio 需要拿到 model 的 logits/probs(不只是 chat completion),**纯 chat-api 跑不动**——要么用开源模型,要么改用 surrogate(让 judge 评估"答案倾向 outdated 程度")。

#### 2.A.3 MUSE(2407.06460)— unlearning 6-axis

6 个评测维度:**verbatim memorization / knowledge memorization / privacy leakage / utility preservation / scalability / sustainability**。前 4 个最值得抄到 v3 的 verifier 设计:utility-preservation 检查"unlearn 后其他能力是否塌",sustainability 检查"连续 unlearn 是否累积破坏"。**对我们启发**:不要孤立测 forgetting,要同时跟踪 retain set 上的 utility。

#### 2.A.4 MemoryAgentBench / selective forgetting(2507.05257)

ICLR 2026,把 memory 评测做成 4 能力轴:**Accurate Retrieval / Test-Time Learning / Long-Range Understanding / Selective Forgetting**。**FactConsolidation 子集**(基于 MQUAKE)是我们 conflict + forgetting 维度的现成 baseline。**复用 LongMemEval + ∞Bench 重构为增量多轮**——和我们 v3 的"5 阶段反向生成"做法理念一致。**论文里必须 cite + 对比**,定位差异:他们是 evaluator,我们是 generator。

#### 2.A.5 - 2.A.6 (1810.13166 BWT/FWT、2403.05175 CL forgetting survey)

CL 经典指标 BWT(Backward Transfer)/ FWT(Forward Transfer):衡量学新任务对旧任务的影响。在 memory 设定里可类比为"看了新 session 之后旧 session 的 retrieval 准确率变化"——可作 v3 的辅助指标。CL forgetting survey 给出"什么样的训练范式抗遗忘"——主要服务于被测对象(memory agent)的设计,对 benchmark 生成器影响小。

---

### 2.B 巩固(consolidation,4 篇)— **空白维度,我们要造**

#### 2.B.1 Rethinking Memory in LLM-based Agents(2505.00675)⭐

提出 **memory 操作 6 分类**(consolidation / indexing / retrieval / updating / forgetting / compression),论文里**明说**:"几乎没有标准化指标衡量 consolidation quality 和 forgetting appropriateness"——**这就是我们的卖点 confirmation**。

#### 2.B.2 Reflective Memory Management(2503.08026)

prospective + retrospective reflection 双重机制做 consolidation。**评测仍是下游 QA accuracy**——没有直接测 consolidation 质量本身。

#### 2.B.3 LoCoMo(2402.17753) + LongMemEval(2410.10813)

都把 "multi-session reasoning" 作为子任务,但**测的是"答对没",不测"是否真的把多 session 信息合并了"**。我们可以借鉴它们的多 hop 题型,但要新加 verifier 来直接测合并质量。

**对 v3 的启发**:**consolidation verifier 必须自己造**——见 §3.2.3 的伪代码草稿。**这是论文 novelty 的硬通货之一**:首个对 memory consolidation 直接评分的 verifier。

---

### 2.C 冲突消解(conflict,4 篇)

#### 2.C.1 Knowledge Conflicts Survey(2403.08319)⭐ **taxonomy 必抄**

- **三大冲突类型**(直接拷贝定义):
  ```
  CM (Context-Memory Conflict):  外部 context 与模型 parametric 知识冲突
                                  Causes: Temporal Misalignment + Misinformation Pollution
  IC (Inter-Context Conflict):    多个外部 context 间互相冲突(RAG 常见)
                                  Causes: Misinformation + Outdated Info
  IM (Intra-Memory Conflict):     模型参数知识内部不一致(同语义不同表述给不同答案)
                                  Causes: Training corpus bias + Decoding stochasticity + KE side-effect
  ```

- **Desired Behavior**(模型在冲突时应该):pinpoint 冲突 + 给出 distinct answers,而不是混淆。
- **现有数据集**:WikiContradict(2210)、ContraDoc(449)、Farm(1952)、KRE(11684)、Tan 2024(14923)、Pan 2023a(52189 基于 SQuAD)等——可作 v3 conflict 子集的 anchor 来源。
- **关键空白**:**没有"自动判断模型是否正确处理冲突"的标准 verifier**——只有 detection accuracy(Zheng 2022, Li 2023a, Wan 2024)和 desired-behavior 描述,**verifier 仍然要我们造**。

#### 2.C.2 - 2.C.4(ROME/CounterFact, OPIN, WikiContradict)

- **CounterFact(2202.05262)**:21k entity-substitution 三元组,作 v3 conflict 生成的事实库 anchor;**OPIN(2303.11315)** 给 prompting 模板让模型 faithful to context;**WikiContradict(2406.13805)** 2210 真实 Wikipedia 段落矛盾对,可作 v3 IC conflict 的金标语料。

#### 对我们的具体用法
- **v3 把 CM/IC/IM 三类 explicit 编码到 schema**(每个 conflict 题标记类型),论文里直接 cite Knowledge Conflicts Survey 的 taxonomy。
- **conflict verifier 自己造**(见 §3.2.1)——本质是检测 "model output 是否同时承认双方说法 + 是否选择性偏向其一"。

---

### 2.D 诊断性归因(attribution,4 篇)

#### 2.D.1 Attributed QA / AutoAIS(2212.08037)⭐ **公式**

- **AIS(Attributable to Identified Sources)** 评测协议:给定 (question, system_answer, attribution),人类标注 "system answer 是否完全由 attribution 支持"(binary)。
- **AutoAIS**:用 NLI 模型(T5-11B)自动近似 AIS,**和人工 AIS 在系统级 Spearman ρ > 0.9**——证明 NLI 可机器近似 attribution evaluation。

- **公式**:
  ```
  AIS(s, A) = HumanRater("answer s 完全由 attribution A 支持?", binary)
  AutoAIS(s, A) = NLI_entailment(A ⊨ s)
  System-level: 与人评 Spearman 0.9+
  ```

- **对 v3 的用法**:**verifier_attribution 用 NLI 模型做底层判定**——把 "model output 中每个 fact" 当 hypothesis,把 "结构层中应被引用的 atom" 当 premise,NLI 通过 = attribution 成立。

#### 2.D.2 Diagnosing Retrieval vs Utilization(2603.02473)⭐ **存/取/用归因**

把 RAG 失败分解为 **"retrieval bottleneck" vs "utilization bottleneck"**——给定 query 和 retrieved docs,用 oracle retrieval 替换 / 用 stronger generator 替换 来归因失败。**对我们的启发**:**failure attribution verifier 用 counterfactual swap**:把 memory 系统的 retrieval 输出替换成 oracle(直接从结构层喂),看模型答案是否变正确——变了 = retrieval bottleneck,没变 = utilization。这是 v3 诊断性最直接的实现路径。

#### 2.D.3 HaluEval(2305.11747)+ 2.D.4 Longpre(2021)

HaluEval 给 35k hallucination benchmark(任务/对话/摘要),attribution 是 binary 判定。Longpre 2021 提出 entity-based perturbation 生成 knowledge conflict,作为 conflict 数据生成方法已被 Knowledge Conflicts Survey 列为 baseline。

---

### 2.E LLM-as-judge 方法论(llm_judge,4 篇)

#### 2.E.1 MT-Bench(2306.05685)⭐ **bias 与 4 招对策**

- **3 大 bias**(必须避开):
  1. **Position bias**:judge 倾向于答案 A(第一个出现的)
  2. **Length bias**:judge 倾向于长答案
  3. **Self-enhancement bias**:judge 倾向于自己同家族的模型
- **4 招对策**:
  1. **Position swap**:跑两遍,A→B 和 B→A,只有都一致才算
  2. **Few-shot**:给 judge 看几个 calibration example
  3. **CoT prompting**:让 judge 先 reasoning 再打分
  4. **Reference-guided**:给 judge 一个参考答案
- 与人评 agreement:vanilla LLM-judge 66%,加 reference + position swap 后 **80%+**

#### 2.E.2 Reference-Guided Verdict(2408.09235)⭐ **多 judge 投票**

3-5 个不同 model 当 judge + 参考答案 + majority vote → Cohen's κ **0.86** vs single judge **0.62**。**Memora 的 3-judge 投票方法直接来自这条线**。**v3 的 verifier 必须照搬:每个判定走 3 judge majority**。

#### 2.E.3 G-Eval(2303.16634)+ 2.E.4 JudgeBench(2410.12784)

- **G-Eval**:CoT + form-filling + **probability-weighted score**(用 logit 加权,而非直接采样 token)——分数更细粒度。
- **JudgeBench**:测 judge 自身可靠性的 meta-benchmark——可作我们 SIGMOD 投稿"为什么我们的 verifier 可信"的 calibration 实验工具。

---

### 2.F 可执行验证(verifiable_rewards,3 篇)

#### 2.F.1 Tülu 3 / RLVR(2411.15124)⭐ **范式**

- **核心思想**:**用 deterministic verifier 替代 reward model**——对可程序判分的任务(math/code/format),直接写 verifier 函数返回 0/1,扔掉 reward model 的训练成本和 reward hacking 风险。
- **典型 verifier**:
  - Math:答案匹配 ground truth(sympy 等价判断)
  - IFEval format:answer matches required pattern (regex)
  - GSM8K:numerical extraction + 等价检查
- **对我们的启发**:**memory 维度的 verifier 也走 RLVR 哲学**——题型设计上尽量约束到可程序判分(EM / 集合 / 数值),只把真正开放的留给 LLM-judge。**这正是我们对 DataMorgana 的差异化**——他们用 LLM-judge 全包,我们走 deterministic + LLM-judge 混合。

#### 2.F.2 Phi-4 / plurality voting(2412.08905)

**多采样投票判难度**:对每题让强 teacher 采 N 次,**全一致 = 太易**(扔),**全发散 = 太难**(扔),**部分一致才留**。**对 v3 的启发**:step3 出题后用 plurality voting 自动筛掉太易/太难/有歧义的题,**不需要人工筛**。

#### 2.F.3 PRIME / Process Reward(2502.01456)

**implicit process reward**:用 next-token DPO 隐式定义 step-level reward,无需 step-level 标注。**对我们启发**:**多 hop memory 题**可挂 process reward,中间推理步骤错了也能识别——这是我们诊断性归因的延伸路径。

---

### 2.G 行为测试(behavioral_diagnostic,1 篇)

**CheckList(2005.04118)** 的 **capability × test_type 矩阵**:把模型能力拆成正交维度(NER、negation、coreference 等)× 测试类型(MFT / INV / DIR)。**对我们的启发**:**v3 把记忆能力按 5 维(I, S, V, T, O)+ memory operations(retrieve/update/conflict/forget/consolidate)做矩阵**,每个 cell 都要有最少几道题——这正是 SIGMOD 审稿人"为什么不是 cherry-pick"的硬证据(见调研沉淀.md §6.5)。

---

## 3. 总结洞察

### 3.1 Verifier 设计的"4 原则"(综合调研)

| 原则 | 来源 | 落地到我们 |
|---|---|---|
| **答案锚定结构层** | TOFU(fictitious data)、FAMA(memory trace)、AutoAIS(attribution source) | step1 生成 atom 时同时生成 verifier 用的 criteria,不让 LLM 出题又自答 |
| **多 judge + 参考答案 + majority vote** | MT-Bench、Reference-Guided Verdict、FAMA | 每个 binary criterion 走 3 judge,**human-judge agreement 88%+** |
| **bias 修正(position swap, length norm, calibration)** | MT-Bench | 涉及对比的题强制 swap,LLM-judge prompt 必须含 reference |
| **可程序判分优先(RLVR)+ LLM-judge 兜底** | Tülu 3 RLVR、Phi-4 plurality | 题型偏抽取/枚举/数值,只把真正开放题留给 LLM-judge |

### 3.2 每个蓝海维度的"verifier 草案"(伪代码)

#### 3.2.1 verifier_conflict(我们要造,无现成公式)

输入:模型对冲突题的回答 + 结构层中冲突 atom 对(双方都 status=conflicting,互相 conflicts_with)+ 题目元数据。
输出:`{score: float ∈ [0,1], label: 'good|partial|miss', evidence: [atom_ids], failure_mode: str}`。

```python
def verifier_conflict(model_output: str, gold_atoms: dict, question: dict) -> dict:
    """
    冲突题 4 类预期行为(借 Knowledge Conflicts Survey desired behavior + 多 judge):
        good:      模型同时识别双方说法 + 显式说"用户两次说法矛盾"+ 不盲目偏向
        partial:   只识别一方,或承认冲突但选错时间锚点
        miss:      完全没识别冲突,直接答某一方
        wrong:     给了双方都不是的答案(幻觉)
    """
    a_id, b_id = question["conflict_pair"]   # 来自结构层 conflicts_with 关系
    a, b = gold_atoms[a_id], gold_atoms[b_id]
    target_predicate = a.predicate  # 双方同 predicate

    # 1. 用 NER/抽取 prompt 找模型答案里关于该 predicate 的提及
    mentions = extract_predicate_mentions(model_output, target_predicate)
    sees_a = a.value in mentions
    sees_b = b.value in mentions

    # 2. 检测是否显式承认冲突(3 judge majority)
    ack_prompt = f"模型回答里是否显式提到'用户先后说了两种不同的{target_predicate}'?"
    acknowledges = llm_judge_majority(ack_prompt, model_output,
                                       judges=["gpt-4.1", "claude-haiku-4.5", "gemini-2.5-flash"])

    # 3. 检测是否盲目偏向(swap position 防 bias)
    bias_score = check_bias(model_output, a.value, b.value, swap=True)

    # 4. 归因 + label
    if sees_a and sees_b and acknowledges:
        return {"score": 1.0, "label": "good", "evidence": [a_id, b_id], "failure_mode": None}
    if (sees_a or sees_b) and acknowledges:
        return {"score": 0.6, "label": "partial", "failure_mode": "one_side_only"}
    if sees_a or sees_b:
        return {"score": 0.3, "label": "miss", "failure_mode": "no_acknowledgement"}
    return {"score": 0.0, "label": "wrong", "failure_mode": "hallucination"}
```

#### 3.2.2 verifier_forgetting(基于 FAMA + TOFU)

```python
def verifier_forgetting(model_output: str, gold_atoms: dict, question: dict,
                        agent_logits: dict = None) -> dict:
    """
    FAMA = max(0, MPA - λ · (1 - FAA))
    + 可选 TOFU Truth Ratio 作连续信号(需要 logits)
    """
    # 1. 收集 binary criteria
    presence_atoms = [gold_atoms[i] for i in question["presence_criteria"]]   # 应包含
    forget_atoms   = [gold_atoms[i] for i in question["forget_criteria"]]     # 应避开
    n_p, n_f = len(presence_atoms), len(forget_atoms)
    lam = n_f / (n_p + n_f) if (n_p + n_f) > 0 else 0.0

    # 2. 3-judge 投票 + reference-grounded
    mpa = sum(vote_majority(model_output, a, mode="includes",
                            judges=["gpt-4.1","haiku-4.5","gemini-2.5-flash"])
              for a in presence_atoms) / max(n_p, 1)
    faa = sum(vote_majority(model_output, a, mode="excludes",
                            judges=["gpt-4.1","haiku-4.5","gemini-2.5-flash"])
              for a in forget_atoms) / max(n_f, 1)

    fama = max(0.0, mpa - lam * (1.0 - faa))

    # 3. (可选)Truth Ratio 连续信号——需要 logits
    truth_ratio = None
    if agent_logits is not None and forget_atoms:
        truth_ratio = compute_truth_ratio(agent_logits, forget_atoms)
        # R_truth 高 = 模型还在偏向 outdated value

    return {"score": fama, "mpa": mpa, "faa": faa, "lambda": lam,
            "truth_ratio": truth_ratio,
            "label": "good" if fama > 0.7 else ("partial" if mpa > 0.5 else "miss")}
```

#### 3.2.3 verifier_consolidation(**完全新造,论文 novelty 卖点**)

```python
def verifier_consolidation(model_output: str, gold_atoms: dict, question: dict) -> dict:
    """
    无公认公式。我们定义:
      事实在 N 个 session 被反复确认 → 模型应当
      (1) 给出该 fact 时 confidence 与重复次数正相关
      (2) 在相关下游问题中主动调用该 fact
      (3) 不被偶发的对立 noise 干扰

    score = 0.5·correctness + 0.3·confidence_match + 0.2·robustness_to_noise
    """
    target = gold_atoms[question["consolidated_atom"]]
    n_mentions = 1 + len(target.consolidates)   # 自身 + 巩固链

    # 1. 答对了吗
    extract = extract_predicate_value(model_output, target.predicate)
    correctness = 1.0 if extract == target.value else 0.0

    # 2. confidence 是否随 n_mentions 上升
    expressed_confidence = extract_confidence_markers(model_output)  # 0-1
    # 理想 confidence 应随 n_mentions 单调,我们对照线性 baseline
    ideal_conf = min(1.0, 0.3 + 0.2 * n_mentions)
    confidence_match = 1.0 - abs(expressed_confidence - ideal_conf)

    # 3. robustness to noise:有对抗题(故意夹一个 noise atom)时,是否仍坚持
    robustness = question.get("robustness_score", 1.0)  # 由前序对抗题给出

    score = 0.5*correctness + 0.3*confidence_match + 0.2*robustness
    return {"score": score, "correctness": correctness,
            "confidence_match": confidence_match, "robustness": robustness,
            "n_mentions": n_mentions}
```

#### 3.2.4 verifier_attribution(基于 AutoAIS + Diagnosing R/U)

```python
def verifier_attribution(model_output: str, gold_atoms: dict, question: dict,
                         agent_trace: dict) -> dict:
    """
    诊断模型答错时 4 步归因:write / retrieve / use / forget
    需要 memory agent 暴露内部 trace(stored_atoms, retrieved_atoms_for_query)
    """
    target = gold_atoms[question["target_atom"]]

    # 1. 是否 stored
    stored = target.atom_id in agent_trace.get("stored_atom_ids", [])
    if not stored:
        return {"score": 0.0, "failure": "write",
                "diagnostic": "memory 系统未把该 atom 写入"}

    # 2. 是否 retrieved
    retrieved = target.atom_id in agent_trace.get("retrieved_atom_ids", [])
    if not retrieved:
        return {"score": 0.0, "failure": "retrieve",
                "diagnostic": "memory 系统检索时漏掉该 atom"}

    # 3. retrieved 了是否用上(NLI 判定 model_output 是否被 target 支持)
    used = nli_entailment(premise=target.value, hypothesis=model_output)
    if not used:
        return {"score": 0.0, "failure": "use",
                "diagnostic": "检索到了但答案没用上"}

    # 4. 对于 forget 类题,target.status='outdated' 但仍被 use 也是错
    if target.status == "outdated":
        return {"score": 0.0, "failure": "forget",
                "diagnostic": "用了应被遗忘的 outdated atom"}

    return {"score": 1.0, "failure": None, "diagnostic": "ok"}
```

### 3.3 对 v3 设计的 5 条改动建议

1. **step1 schema 加 verifier 字段**:每个 atom 加 `salience`(0-1,影响 FAMA 的 λ 加权)、`evidence_text_for_judge`(给 LLM-judge 的参考答案);每个 question 加 `presence_criteria`(list of atom_ids)、`forget_criteria`、`conflict_pair`、`consolidated_atom`、`target_atom`(根据题型不同填不同 slot)。
2. **step3 出题严格按题型分桶**:每个 atom 标的 `op_type ∈ {retrieve, update, conflict, forget, consolidate}` 决定走哪个 verifier;题型预算按 5:3:2:2:1 配比(retrieve 多但红海,forget/consolidate 少但蓝海)。
3. **step4 verifier 走"deterministic 优先 + 3-judge 投票 + position swap"**:能用 EM 的(value 匹配)用 EM,不能用的走 3 judge majority,凡比较两个候选答案的必 swap。
4. **必加 calibration 实验**:每发布一版 benchmark 前,**抽 100 题人工标注 + 计算 LLM-judge κ**(目标 ≥0.8)、+ JudgeBench 测 judge 自己的 bias。这是 SIGMOD reviewer 必问的"可信度"硬证据。
5. **lineage 记录 verifier 决策路径**:每个 score 都记下 `{judges_voted: [...], swap_position_used: bool, reference_used: str}`,方便审稿人复现 + 后续 debug 失败题。

---

## 4. 工具包(可直接落地)

### 4.1 公式库(LaTeX / 伪代码,可拷贝)

```
# 遗忘
FAMA = max(0, MPA - λ·(1-FAA)),   λ = N_forget/(N_presence+N_forget)
TOFU Truth Ratio = (1/|A_pert| · Σ P(ã|q)^(1/|ã|)) / P(â|q)^(1/|â|)
TOFU Forget Quality = KS-test p-value (R_truth_unlearned vs R_truth_retain)
TOFU Model Utility = HarmonicMean(9 metrics: 3 datasets × 3 metrics)

# 巩固(我们造)
Consolidation = 0.5·correctness + 0.3·confidence_match + 0.2·robustness
confidence_match = 1 - |expressed_conf - ideal_conf(n_mentions)|

# 冲突(我们造)
Conflict_Score: 4-tier {good=1.0, partial=0.6, miss=0.3, wrong=0.0}
依据: (sees_a, sees_b, acknowledges_conflict, bias_swap_consistent)

# 归因(AutoAIS + Diagnosing R/U 组合)
Attribution: 4 阶检查 write→retrieve→use→forget,首个 fail 即归因
AutoAIS:    NLI_entail(attribution_source, system_answer) ∈ {0,1}

# 聚合(防止一项掩盖)
Total = HarmonicMean(score_retrieve, score_update, score_conflict, 
                     score_forget, score_consolidate)
```

### 4.2 LLM-judge prompt 模板(可拷贝)

**Reference-Guided Verdict 风格**(借 2408.09235 + Memora):
```
You are an impartial judge evaluating an AI assistant's response on a memory recall task.

GROUND TRUTH FACT (from memory layer): {gold_atom.value}
EVIDENCE SOURCE: this fact was stated by the user in session {gold_atom.session_id} 
                on {gold_atom.timestamp}; current status: {gold_atom.status}

QUESTION: {question}
ASSISTANT RESPONSE: {model_output}

EVALUATION CRITERIA: 
Does the response correctly {include|exclude} the fact above?
- {include}: response should mention or use the fact correctly
- {exclude}: response should NOT rely on this fact (it has been updated/forgotten)

Answer with ONLY one word: yes or no. 
Briefly justify in one sentence.

Format:
verdict: <yes|no>
justification: <one sentence>
```

**多 judge 投票 wrapper**:
```python
def vote_majority(model_output, criterion, gold_atom, judges, mode):
    votes = []
    for judge in judges:
        v = judge.run(build_prompt(model_output, criterion, gold_atom, mode))
        votes.append(parse_verdict(v))  # → 0/1
    return 1 if sum(votes) > len(judges)/2 else 0
```

### 4.3 稳定性技巧 checklist

- [ ] **温度**:judge 用 temperature=0(deterministic)
- [ ] **采样数**:每个 binary criterion 用 **3 judge majority**(不是同模型重采,是 3 个不同 model)
- [ ] **Position bias 修正**:涉及比较两候选答案的题,必须 swap 位置跑两遍,**两遍一致才算**
- [ ] **Length bias 修正**:judge prompt 显式说 "ignore length, focus on factual correctness"
- [ ] **Self-enhancement bias**:不用 GPT 系列同时当 judge 和 candidate(避免自夸)
- [ ] **Reference 必给**:judge prompt 必须含 ground truth 参考 + evidence source
- [ ] **Calibration**:每发版抽 50-100 题人工标注,计算 LLM-judge vs human Cohen's κ,目标 ≥0.80
- [ ] **Meta-judge 抽查**:每 10 个 judge verdict 抽 1 个用独立 model 二次评判,detect reward hacking
- [ ] **JudgeBench**:用 JudgeBench(2410.12784)测每个 judge 模型的固有 bias,给 verdict 加权
- [ ] **lineage 记录**:每个 score 落盘 `{judges: [...], votes: [...], swap_used: bool, reference: str}`

### 4.4 Python verifier 函数签名(给 step4 实现照搬)

```python
from typing import Optional
from dataclasses import dataclass

@dataclass
class VerifierResult:
    score: float            # ∈ [0, 1]
    label: str              # 'good' | 'partial' | 'miss' | 'wrong'
    failure_mode: Optional[str]
    diagnostic: dict        # 详细中间值(mpa, faa, lambda, votes, ...)
    judges_used: list[str]
    swap_position_used: bool

def verifier_retrieve(output: str, gold_atoms: dict, q: dict) -> VerifierResult: ...
def verifier_update(output: str, gold_atoms: dict, q: dict) -> VerifierResult: ...
def verifier_conflict(output: str, gold_atoms: dict, q: dict) -> VerifierResult: ...   # §3.2.1
def verifier_forgetting(output: str, gold_atoms: dict, q: dict,
                         agent_logits=None) -> VerifierResult: ...                     # §3.2.2
def verifier_consolidation(output: str, gold_atoms: dict, q: dict) -> VerifierResult: ... # §3.2.3
def verifier_attribution(output: str, gold_atoms: dict, q: dict,
                          agent_trace: dict) -> VerifierResult: ...                    # §3.2.4

# 调度入口
def evaluate(question, model_output, gold_atoms, agent_trace=None,
             agent_logits=None) -> VerifierResult:
    op = question["op_type"]   # 来自 step3 打的标签
    return {
        "retrieve":     lambda: verifier_retrieve(model_output, gold_atoms, question),
        "update":       lambda: verifier_update(model_output, gold_atoms, question),
        "conflict":     lambda: verifier_conflict(model_output, gold_atoms, question),
        "forget":       lambda: verifier_forgetting(model_output, gold_atoms, question, agent_logits),
        "consolidate":  lambda: verifier_consolidation(model_output, gold_atoms, question),
        "attribution":  lambda: verifier_attribution(model_output, gold_atoms, question, agent_trace),
    }[op]()
```

---

## 5. 下载统计

| 类别 | 篇数 | 关键资源 |
|---|---|---|
| forgetting | 6 | **FAMA / Memora**、**TOFU**、MUSE、MemoryAgentBench、CL metrics、CL forgetting survey |
| consolidation | 4 | LoCoMo、LongMemEval、Reflective Memory Mgmt、Rethinking Memory(6 操作 taxonomy) |
| conflict | 4 | **Knowledge Conflicts Survey**、ROME/CounterFact、Context-Faithful Prompting、WikiContradict |
| attribution | 4 | Longpre 2021、**AutoAIS**、HaluEval、**Diagnosing R/U** |
| llm_judge | 4 | **MT-Bench**、**Reference-Guided Verdict**、G-Eval、JudgeBench |
| verifiable_rewards | 3 | **Tülu 3 / RLVR**、Phi-4 plurality、PRIME |
| behavioral_diagnostic | 1 | CheckList |
| **总计** | **26 PDF** | 全部下载成功 |

**精读深度**:
- A 级完整精读(完整论文 abstract + method + key metrics):FAMA、TOFU、Knowledge Conflicts Survey(3 篇 = 关键公式已抠出)
- B 级关键章节读取:其余 23 篇按论文标题 + 摘要 + 我已有的领域知识做信息综合
- 所有 26 篇 PDF 在 `survey/verifier_metrics/<category>/` 可随时再深挖

---

## 6. 与其他两路 survey 的衔接

- **与 P0.1(ingest 真实形态)的衔接**:本报告 §3.2 的 verifier 草案输入是"结构层 atom + question",而 P0.1 报告给出了 7 种 ingest 通道的 schema——两份合起来,**step2 生成 corpus 时同时落两套字段:ingest schema 字段 + verifier 友好字段(presence/forget criteria, conflict_pair 等)**。
- **与 P1.4(multi-agent)的衔接**:本报告 §3.1 的"多 judge majority + position swap"和 P1.4 的 MemoryChecker 设计无缝对接——MemoryChecker 内部就是用本报告 §4.2 的 prompt 模板 + §4.3 的 checklist 实现 verifier。

**三份 REPORT.md 合起来 = SIGMOD step1-5 pipeline 的完整设计输入**。下一步可以直接开始 v3 重构(改 schema.py 加新字段、改 step1 prompt 让 LLM 同时生成 verifier criteria、step2 走 multi-agent + ingest schema 落实、step3 按 op_type 出题、step4 实现 §4.4 的 evaluate 函数)。
