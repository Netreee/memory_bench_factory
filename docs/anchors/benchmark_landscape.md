# 主流 memory benchmark 对标 + 我们的定位（2026-06-02）

> 5 个 subAgent 并行 clone 真仓 + 逐字抽样 instance + 读全文，对标 16+ 个主流 memory benchmark 的出题法。
> 目的：用证据回答"我们的 7 能力选型对不对、MR 是不是伪题型、该补什么"。
> 配套 empirical：真记忆系统(EmbedMemory/simpleMem)在我们 benchmark 上的实测(见 §6)。

## 0. 一句话结论
- **MR(对数值序列求 max/min/trend) 不是伪题型,但小众**：12+ 主流里只有 **Memora(ACL2026)** 一家做、BEAM 2000 题里 1 条。真问题是 ① 撞名(领域"MR"=Multi-hop Reasoning) ② 我们做成了**单调退化版**(Memora 不会) ③ 算术污染风险。
- **离散变更核心(IE/KU/TR/CONFLICT/FORGET/ABS) 1:1 对齐主流,地基稳。**
- **真正缺的是前沿**：multi-hop 跨会话、隐式偏好/认知约束、Event Ordering —— 比数值 MR 重要得多。
- **真系统实测：benchmark 不 trivial**(crm 真系统 0.59 vs oracle 0.82,TR/IE 真难)——白痴 agent 的"太易"是 oracle 幻觉。

---

## 1. 题型范式总表（核心：用不用"数值序列聚合"）

| Benchmark | 出处 | 题型/能力 | 数值聚合 MR? | 出题法 | 真实 instance（节选） |
|---|---|---|---|---|---|
| **AMemGym** | 毕设主评 | 情境化偏好5选1(按当前离散状态选) | ❌ 848 state 值 numeric=0 | LLM造state_schema(枚举)+程序gt | "桥牌聚会出新活动?"→选项绑当前 guest_age/group_size 状态 |
| **LoCoMo** | 2402.17753 | single/multi-hop/temporal/open/**adversarial拒答** | ❌ "highest/how many"=召回单条事实/数离散次数 | persona+因果图生对话,人工编辑15% | "How many times Nate in tournaments?→nine"(9个离散轮次) |
| **LongMemEval** | 2410.10813 | single/multi-session/**knowledge-update**/temporal/preference/**abstention** | ❌ KU=取最新声明值(25:50<27:12 仍取"新"非min) | 属性受控合成,埋进500session草堆 | "personal best 5K?→25:50"(27:12→25:50跨session更新) |
| **LongMemEval-V2** | 2605.12493 | static/**dynamic-state-tracking**/workflow/gotchas/premise | ❌ 作者明确把aggregation划给"早期long-doc"、与memory切割 | web agent轨迹人工标注 | "标记Duplicate后State下方字段?→Resolution Code" |
| **LoCoMo-Plus** | 2602.10715 | factual(复用LoCoMo)+**cognitive**(causal/state/goal/value隐式约束) | ❌ 反方向走隐式认知 | cue→trigger语义脱节,BM25过滤表面相似 | cue"备考想少分心"→trigger"该追新剧吗?"(无显式事实可查) |
| **MEME** | 2605.12477 | **Tr/Cas/Abs/Del**/Agg/ER | ❌ 故意避数值;Tr列离散标签(TV/book/podcast) | ★状态机+state-diff(同我们);before/after gating | "List all media over time→Whispers(TV),Quorath(book),podcast" |
| **Memora** | 2604.20006 | Remembering/**Reasoning**/Recommending | ✅★唯一做MR的 | 结构化ledger注入数值+程序聚合gt | "哪周步数最多?→第1周61,228"(argmax);"总食物支出→$1525.46"(sum) |
| **EvolMem** | 2601.03543 | retrieval/summarization/isolation/inference/learning/habituation | ❌ 语义/习惯演化,非数值 | LLM合成+难度过滤(≥0.8丢) | Habituation:始终保持formal改写风格 |
| **MEMTRACK** | 2510.01353 | 跨平台memory/冲突消解/代码introspection | ❌ 数字=对当前代码快照grep\|wc,非over-time | 真PR反推+真Gitea事件 | "多少Python文件import kaggle?→9"(当前快照计数) |
| **MEMENTO** | 2505.16348 | object-semantics/user-patterns(具身) | ❌ 空间grounding,零数值 | GPT-4o生个性化+模拟器gt | "拿我最爱的杯子准备早餐"→召回blue cup+bowl+apple |
| **MemoryAgentBench** | 2507.05257 | **AR/TTL/LRU/CR(冲突消解)** | ❌ CR=后序号覆盖取最新 | 改写既有+新构EventQA/FactConsolidation | "Hines Ward position?"(fact36覆盖fact3) |
| **MemBench** | 2506.21605 | 8类(含aggregative/comparative/**KU**) | ❌ aggregative=count;comparative=二元比较 | relation graph采样+全MCQ | "Philadelphia住几人?→2"(count);"谁更老?→Clara" |
| **BEAM** | 2510.27246 | 10能力(IE/**KU**/contradiction/**event-ordering**/temporal/**abstention**/multi-hop/...) | ⚠️ 2000题里恰好1条(标complex_trend_analysis,hard) | LLM plan→人工校验,nugget/rubric判分 | KU:"连接池max size?→75"(50→75更新,旧值当陷阱) |
| **Mem2ActBench** | 2601.19935 | memory→tool-call(非QA) | ❌ Fact Evolution Chain离散覆盖 | 三元组→拓扑排序演化链;★Blinded Discriminator删可无记忆解的题 | underspec指令→从记忆补参数生tool call |
| **AMABench** | 2602.22769 | recall/causal/**state-update**/abstraction | ❌ count/+1 delta,非max/min/trend | 研究生标注+POMDP程序gt | "Step14→21 Users count变化?→+1" |
| **MemoryArena** | 2602.16313 | agentic多session任务(因果依赖) | ❌ "3-5候选挑highest-rated"=离散选择 | 环境改造+人工;★"单轮能答就删" | 买兼容配件链;行程槽位填充 |
| **RealMem** | 2601.06966 | static-retrieval/**dynamic-updating**/proactive/temporal | ❌ dynamic=计划状态覆盖+冲突消解 | 三段多agent合成;query自然内生 | "保持12天但加西海岸"→retrieve冲突+改写状态 |

---

## 2. MR 专题：保留还是砍？

**证据分歧(最值钱)**：
- **反方(7+家)**：LoCoMo/LongMemEval/MAB/MemBench/MemoryArena/RealMem/AMemGym/MEME/EvolMem/MEMENTO 全 0 数值MR。
- **正方(1家硬先例)**：**Memora Reasoning = MR 逐字翻版**(argmax-week/sum/count-threshold over 数值序列,程序gt)。BEAM 1/2000。

**判定**：MR = **小众但合法**(有 Memora 背书,非臆造)。三个真问题：
1. **撞名**：领域"MR"普遍指 Multi-hop Reasoning(BEAM/LoCoMo/LongMemEval) → 我们的"数值聚合MR"同名异义,审稿人会误读。**改名**(如 NumAgg/ValueTrend)。
2. **★实现退化**：Memora 把数值**嵌进自然多session+filler、max/sum/count多算子混用、gt程序化**;我们是**单调序列+纯max**→极值永在端点。**病不在MR概念,在我们做退化了。**
3. **算术污染**：完美recall全序列后求max是reasoning非memory。

**两条路**(待真系统在office/legal上的MR实测定夺)：
- (A) **按 Memora 做实**：非单调轨迹(峰/谷/平台)+数值嵌叙述+max/min/sum/count多算子+程序gt。保留差异化(可加 Memora 没有的 trend)。
- (B) **降级**：改成 count-of-discrete-events / event-interval(归 TR),对齐主流。

---

## 3. 我们 vs 主流：gap 清单

**强对齐(保留)**：IE=single-hop、**KU=knowledge-update(LongMemEval一级题型)**、TR=temporal/event-ordering、FORGET=fact-stop、**ABS=abstention(四家标配,建议加重)**。MEME 用的就是我们这套状态机+state-diff。

**缺的前沿(建议补,比MR重要)**：
- **multi-hop 跨会话综合**：LoCoMo 321题 + LongMemEval 133题,大头。我们无独立题型。
- **隐式偏好/认知约束**：LongMemEval preference、LoCoMo-Plus整篇cognitive、RealMem proactive-alignment。记忆评测真前沿。
- **Event Ordering(变更顺序)**：BEAM 独立能力,Kendall-tau 评分。

**要校准**：
- **CONFLICT gt**：主流两味——(a) MAB/Mem2Act"后者覆盖→答最新"(≈KU) 或 (b) BEAM"检测硬矛盾→点名两条→要求澄清/拒答"。我们"各期是否一致→不一致"**主流无直接对应物**,得二选一。

**自我背书(机制撞SOTA)**：硬判别器=MemoryArena"单轮能答就删"+Mem2Act"Blinded Discriminator";state-diff gt=MEME/Memora/AMABench;防剧透=session-local 渲染。

---

## 4. 可直接抄的机制（生成方法论）

- **AutoBencher(2407.08351)**：① 把难度/区分度从**事后体检**升级成**出题时优化目标 + adaptive-search**(拿baseline准确率反馈,专挑能压低准确率的实体/算子/草堆组合再生成);② **Novelty 指标**=新题诱导的模型排名与既有题排名的相关性,惩罚"和已有题考同一个东西"。
- **YourBench(2504.01833)**：① grounding 升级成**逐字 citation span + Levenshtein≥0.85**(比"值出现在证据周"严);② **Sentence-BERT + DBSCAN(cos>0.9)语义去重**+簇权重;③ **新鲜/虚构实体防污染**(杜绝靠世界知识蒙)。
- **DataMorgana(2501.12789)**：把**问法多样性**做成正交可配比旋钮(phrasing、与证据词汇距离 similar↔distant 逼语义跳转、user persona)。

---

## 5. clone 落地（refs/survey/）
已成功 clone:`refs/survey/{locomo, LongMemEval, MemoryAgentBench, BEAM}`(BEAM 2000题真数据在 chats/*/probing_questions.json)。MEME/Memora 在 `refs/{MEME,Memora}`。其余(MemBench/Mem2Act/AMABench/EvolMem/MEMTRACK/MEMENTO)clone 不动,用 PDF + HF 实拉数据补齐。

## 6. 真系统 empirical（EmbedMemory = simpleMem F1+E1+Q1+R1）
- **crm**：真系统 **0.59** vs oracle proxy 0.82 vs no_context 0.10(落差+0.23=检索瓶颈代价)。
  - 各能力:KU 6/6、**TR 5/11**、**IE 0/2**、CONFLICT 1/2、FORGET 1/1。
  - ★真系统在草堆里**定位不到正确的周**(TR"首次变化第2周"答第6周、IE"第5周值"答不知道)——这是 oracle proxy 测不出、真系统才暴露的难度。**证明 benchmark 不 trivial。**
  - 注:crm 无 MR 题,"单调MR易不易"需 office/legal(有MR)的真系统实测补上。

## 7. 待办
1. 在 5 个场景全跑真系统评测(尤其有 MR 的 office/medical/itops/legal),定夺 MR。
2. 据此决定 MR:做实(Memora式) or 降级。
3. 补 multi-hop / 隐式偏好 / event-ordering 三个能力。
4. CONFLICT gt 二选一对齐。
5. 抄 AutoBencher(adaptive+novelty)/YourBench(citation grounding+去重+新鲜实体)。
