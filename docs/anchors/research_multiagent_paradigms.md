# 多智能体设计范式 · 调研报告

> **目的**:为 `redesign_factory_v2.1.md §11.7`(世界生成应引入 multi-agent)补齐我们欠缺的范式知识。
> **来源**:4 个并行调研 subAgent(协作范式 / 框架 / 世界构建 / 何时该上),本文综合 + 引用保留。
> **一句话**:**别为多智能体而多智能体。** 近一年最强证据显示"多 agent 辩论"常打不过"强单 agent + 多采样投票";真正的杠杆是 **① 角色异质性 ② 发散用并行、收敛用单一权威 ③ 把代码校验器当 critic 回灌**。

---

## §0 TL;DR —— 四路调研收敛到同一个推荐架构

```
并行【异质】议会(发散,read-like)
        │  各自独立产出、不互看(防谄媚趋同)
        ▼
单 architect 收敛成自洽世界(write,★绝不投票)
        ▼
代码校验器驱动的精修轮(CRITIC 式:缺陷回灌 → 定向重生成)
        ▼
主动护多样性(verbalized sampling + 相似度惩罚)
```
- **发散是 read-like → 可并行;最终世界是一个必须自洽的 write → 单一权威拍板**(Anthropic"fan-out 只在可并行+读多+共享状态少时"× Cognition"interdependent write 别拆"的共识)。
- **★这正是你们中央办公室已有的骨架**(6 视角并行 → 代码装配 → 1 次批判)——世界生成直接复用,无需引新框架。
- **准入门槛**:能用"单 architect + best-of-N 采样"廉价拿到的多样性,**就别上多智能体**。

---

## §1 协作范式速查(机制 / 适配世界生成)

| 范式 | 机制 | 适配 | 关键出处 |
|---|---|---|---|
| **Generator–Critic / 自精修** | 生成→自评/外评→修订,迭代 1–3 轮 | ★**最高**:你们的代码校验器(单调轨迹等)正是 **CRITIC 式外部 critic 信号**,别丢、回灌 | Self-Refine [2303.17651]、Reflexion [2303.11366]、**CRITIC [2305.11738](用外部工具而非自省)** |
| **多智能体辩论 MAD** | N agent 多轮互批→共识 | ⚠ 中低:成本高 O(N×R),且复评显示常不如 self-consistency;仅用于**有客观对错的局部裁决** | Du [2305.14325];复评 [2511.00751] |
| **角色扮演 Role-Play** | 异质角色配对自主协作产数据 | 高:设地理/经济/历史/势力等**异质 lens**,补当前 one-shot 同质生成之贫 | CAMEL [2303.17760] |
| **Orchestrator–Worker / 分层** | lead 分解→并行 worker→装配 | 高(顶层骨架):契合你们"代码确定性装配" | Anthropic multi-agent research system |
| **黑板 / Society of Mind** | 半自治 agent 围绕**共享工作区**读写,谁能谁响应 | 中高:把"世界状态"做成黑板根治**全局一致性**;校验器常驻监督 | LLM Blackboard [2507.01701](报告 +13~57%) |
| **Mixture-of-Agents (MoA)** | 分层聚合,每层读上层全部输出再生成 | 中:聚合层思路可融 6 视角;但贵于直接代码装配 | MoA [2406.04692] |
| **Self-Consistency** | 纯多采样投票,**无交互** | ★必做**对照基线**(最便宜的"伪多智能体") | More Agents [2402.05120] |

---

## §2 框架:借模式 vs 借控制(我们要"嵌一段有界 multi-agent 进确定性 stage")

**研究型框架(借模式,别借代码)**:
- **MetaGPT** [2308.00352]:SOP 流水线,角色间传**标准化产物(typed 文档)而非对话** —— ★这条哲学直接契合"代码控流程、LLM 填内容";agent 间传结构化 world 产物,大幅降歧义/不一致。
- **CAMEL** [2303.17760] / **ChatDev** [2307.07924]:role-play / 软件公司瀑布;本质是数据合成器,坑=角色漂移、对话发散停不下来。

**工程编排框架(借控制力)**:
- **LangGraph**(★首选嵌入):有向图 + 显式 TypedDict 共享状态 + 条件分支/循环/并行/checkpoint。一个 stage 内部 = 一张子图`生成→批判→(条件)精修→终止`,轮数/质量阈值即终止,可审计可 resume。多份对比点名它为"在确定性 pipeline 里嵌受控 agent 子步骤"的最优解。
- **OpenAI Agents SDK**(次选):`agents-as-tools` 把每个 world-gen 角色**当函数在你现有 stage 代码里直接调**,不交出主控流;guardrails 做精修门禁。
- **AutoGen→AG2 的自由 `GroupChat`**:⚠ **明确避免**——假设涌现式对话,破坏 stage 确定性,O(N²) 通信 + token 爆炸 + 不可控终止。

**范式对比**:确定性图/状态机(可预测、可调试、可 resume)vs 自由对话涌现(灵活但难控、易跑偏)。业界共识=**混合**:关键路径确定性,探索子环节放开。→ 你们要的正是这个形态,而你们的中央办公室已经是"固定拓扑 + 有界批判"的好范本。

---

## §3 用于生成 / 世界构建(最相关)

- **Generative Agents("Smallville",[2304.03442])**:多 agent + `记忆流→反思→规划`循环 → coherence 与实体间关系**从交互涌现**,而非 one-shot 独立生成。规模化已验证(1000 人模拟 [2411.10109] ~85% 复现;AgentSociety 万级 [2502.08691])。
- **★StoryBox([2510.11618],最接近我们的任务)**:**hybrid 自顶向下 + 自底向上** —— architect 出骨架(实体/关系/冲突/章节)+ 角色在分层世界状态树里**按天 sim**(带"异常行为因子"注入意外/冲突)+ retrieval 缝连贯 + critic。**12k 词连贯 vs one-shot 1k;7 天 sim 是甜点(再长边际递减)—— 正好对应我们"按周演化"。** 经验:**架构师骨架要可改,不是冻结的 spec**(纯 top-down 会压平、纯 bottom-up 会漂)。
- **AgentInstruct(微软,[2407.03502])**:`transform→generate→refine` 多 agent 流,产数据质量**超基座 LLM**,关键在 **reflection + critic 迭代** → 验证"该有显式 critic/refiner"。
- **persona 自博弈**:CAMEL / MADS [2510.05124] / OpenCharacter [2501.15427] —— 角色驱动 agent 自博弈生成实体间交互数据。
- **★反例(必须防)**:**表征塌缩**([2604.03809]:独立初始化的 agent 收敛到 cosine≈0.888、有效秩 2.17/3);**mode collapse**。→ **多 agent 不保证多样,要工程化护**:Verbalized Sampling([2510.01171],让模型显式给 N 候选的概率分布,训练-free 恢复 2–3× 多样性)+ 相似度惩罚剔冗余 agent。

---

## §4 何时【别】上 + 失败模式(冷静的反方)

- **《Stop Overvaluing Multi-Agent Debate》([2502.08788])**:5 种 MAD × 9 benchmark × 4 模型,MAD **几乎打不过 CoT**(36 场景无一胜率 >20%),等 token 预算下**更差于 self-consistency**;唯一普适解药 = **模型异质性**(不同底座)。
- **《More Agents Is All You Need》([2402.05120])**:很多"多 agent"收益其实来自**多采样投票**,5 个 agent 后边际递减——不需要真"辩论"。
- **失败模式**:① **谄媚趋同**——辩论后约 24% 初始分歧题收敛到**一致但错误**的共识,correct→wrong **多于**反向([2509.05396]);RLHF 训出的"爱附和"使强 agent 向弱 agent 投降;② **错误级联**——MAST 失败分类法([2503.13657],NeurIPS'25)归纳 14 种失败,子产出"带病"被下游当事实采纳,**串行链上错误复合而非抵消**。
- **关键设计轴**:**3–5 agent**(>5 递减);**★角色异质性 = 最大杠杆**(同质 agent ≈ 多采样);**先并行独立、后聚合**(优于全连接多轮互看);**别等"共识"**(共识常是趋同的伪装,用固定轮数/外部验证停);**裁决交给强 architect / 程序化校验,不投票**(LLM judge 会被"自信的胡说"带偏)。
- **生成类的核心张力**:fidelity/coherence ↔ diversity。并行议会**增多样**但若协商趋同则**两头不讨好**;单 architect **增一致**但易塌缩成模板化低熵世界 → 所以要"并行发散 + 单一收敛"两段分治。

---

## §5 对我们世界生成的落地建议(综合 → 接 §11.7 的 L1/L2)

- **L1(最划算,先做)= CRITIC 式修复轮**:`build_world` 后,把 `assemble_world` **已经算出的**确定性缺陷(单调轨迹/伪 evolving/absent 矛盾)+ 白皮书 `traps/change_density` 作为 **critic 信号定向喂回**,只重生成那几个坏字段,迭代 1–3 轮。**复用中央办公室骨架,零新框架。** ← 4 路调研一致点名的最高 ROI(治"算了却丢弃"的闭环)。
- **L2(大世界才值)= StoryBox 式 hybrid**:architect 出**可改**骨架(实体/关系/弧线/陷阱布局)→ 按周 bottom-up 生成(**异质专家角色,各自独立产出防趋同**)→ retrieval+summary 维持跨周连贯 → critic 验弧线/落陷阱/消矛盾。**主动护多样性**(verbalized sampling + 相似度惩罚)。
- **★准入门槛**:先用"单 architect + best-of-N 采样"做对照;**只有多样性确为瓶颈、且 best-of-N 不够,才升 L2**。能多采样解决的不要上多智能体。
- **工程形态**:不引自由对话框架;**LangGraph 式有界子图**或直接复用中央办公室"并行→装配→批判";agent 间**传结构化 world 产物(MetaGPT)非对话**;裁决用**单 architect/程序化校验**而非投票。

---

## 引用速查(arXiv / 链接)

**协作范式**:Self-Refine 2303.17651 · Reflexion 2303.11366 · CRITIC 2305.11738 · Multi-Agent Debate 2305.14325 · CAMEL 2303.17760 · Blackboard-LLM 2507.01701 · MoA 2406.04692 · More-Agents 2402.05120
**框架**:MetaGPT 2308.00352 · ChatDev 2307.07924 · AG2 github.com/ag2ai/ag2 · LangGraph(docs) · OpenAI Agents SDK(openai.github.io/openai-agents-python) · CrewAI github.com/crewaiinc/crewai
**世界构建/生成**:Generative Agents 2304.03442 · 1000-People 2411.10109 · AgentSociety 2502.08691 · **StoryBox 2510.11618** · Dynamic-Hierarchical-Outlining 2412.13575 · AgentInstruct 2407.03502 · MADS 2510.05124 · OpenCharacter 2501.15427
**反方/失败/多样性**:Stop-Overvaluing-MAD 2502.08788 · Talk-Isn't-Cheap 2509.05396 · MAST 2503.13657 · Reevaluating-Self-Consistency 2511.00751 · Representational-Collapse 2604.03809 · Verbalized-Sampling 2510.01171 · MetaSynth 2504.12563
