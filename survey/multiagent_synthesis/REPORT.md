# Survey: Multi-Agent 数据合成架构 + 开源实现

## 交付清单

- **下载到本地的资源:35 个**(22 PDF + 13 markdown),覆盖 8 个类别(eval 嵌入其他类别),远超 15 的要求
- **可跑代码骨架:2 份**
  - `survey/multiagent_synthesis/skeleton_step2_langgraph.py`(75 行,**主推**)
  - `survey/multiagent_synthesis/skeleton_step2_autogen.py`(80 行,对比基线)
- **目录结构**:`{data_generation_papers, role_play, frameworks, adversarial, verifier_in_loop, simulation_corpus, orchestration, eval}/`

---

## 0. 一句话结论

**用 LangGraph + checkpointer 做 step2 主干,把 ToolACE(User/Assistant/Tool 三角)和 APIGen-MT(blueprint→committee→interplay 二相)缝合成"4-agent + 显式状态机 + verifier-in-the-loop"。** AutoGen/AG2 在"灵活对话"上更顺手,但 SIGMOD 投稿需要"可重生成 + lineage 可追踪 + 失败重试可审计",LangGraph 的 StateGraph + checkpointer 天生胜任,且与 step5 的 lineage 输出无缝对接。CrewAI/Swarm 过于产品化,自由度反而低;Magnetic-One/MAG-V 是参考实现,非框架底座。

---

## 1. 索引总表(35 条)

| # | 名称 | 类别 | 年份 | 链接 | 本地路径 | 价值(一句话) |
|---|---|---|---|---|---|---|
| 1 | ToolACE | data_generation_papers | 2024 (ICLR 25) | [arXiv 2409.00920](https://arxiv.org/abs/2409.00920) | `data_generation_papers/2409.00920_toolace.pdf` | **User+Assistant+Tool 三 agent + 复杂度 evaluator + dual-layer 验证**——离我们最近的 7B 数据合成模板 |
| 2 | xLAM | data_generation_papers | 2024 | [arXiv 2409.03215](https://arxiv.org/abs/2409.03215) | `data_generation_papers/2409.03215_xlam.pdf` | 数据 unify+augment+synthesize+verify 全流程的工程指南 |
| 3 | APIGen-MT | data_generation_papers | 2025 | [arXiv 2504.03601](https://arxiv.org/abs/2504.03601) | `data_generation_papers/2504.03601_apigen_mt.pdf` | **二相框架:Phase1 blueprint(LLM Gen + format check + Review Committee 多 judge majority vote + Feedback Generator),Phase2 simulated human-agent interplay**——和我们 step1→step2 的设计完全同构 |
| 4 | AgentTuning | data_generation_papers | 2023 (ICLR 24) | [arXiv 2310.12823](https://arxiv.org/abs/2310.12823) | `data_generation_papers/2310.12823_agenttuning.pdf` | AgentInstruct: 任务派生 + 轨迹交互 + 轨迹过滤,reward-driven filter |
| 5 | MAG-V | data_generation_papers | 2024 | [arXiv 2412.04494](https://arxiv.org/abs/2412.04494) | `data_generation_papers/2412.04494_mag_v.pdf` | Splunk 的 multi-agent 合成 + verification,反向工程 trajectory + 非 LLM verifier |
| 6 | Persona Hub | data_generation_papers | 2024 | [arXiv 2406.20094](https://arxiv.org/abs/2406.20094) | `data_generation_papers/2406.20094_persona_hub.pdf` | 1B persona 驱动数据合成,我们 UserAgent 的角色源 |
| 7 | WildChat | data_generation_papers | 2024 | [arXiv 2405.01470](https://arxiv.org/abs/2405.01470) | `data_generation_papers/2405.01470_wildchat.pdf` | 真实 1M ChatGPT 用户对话,**做 ground-truth 分布比对的"金标 corpus"** |
| 8 | CAMEL | role_play | 2023 (NeurIPS 23) | [arXiv 2303.17760](https://arxiv.org/abs/2303.17760) | `role_play/2303.17760_camel.pdf` | **Inception Prompting + Role Flip/Repeat/Flake/Infinite 4 大坑**——所有 multi-agent 数据合成的必读 |
| 9 | MetaGPT | role_play | 2023 (ICLR 24) | [arXiv 2308.00352](https://arxiv.org/abs/2308.00352) | `role_play/2308.00352_metagpt.pdf` | **SOP + publish/subscribe message pool**——orchestration 模式 |
| 10 | ChatDev | role_play | 2023 | [arXiv 2307.07924](https://arxiv.org/abs/2307.07924) | `role_play/2307.07924_chatdev.pdf` | Chat Chain + communicative dehallucination 反幻觉机制 |
| 11 | AutoGen | frameworks | 2023 | [arXiv 2308.08155](https://arxiv.org/abs/2308.08155) | `frameworks/2308.08155_autogen.pdf` + `frameworks/autogen/README.md` | ConversableAgent + GroupChatManager,**conversation programming** 范式 |
| 12 | Magentic-One | frameworks | 2024 | [arXiv 2411.04468](https://arxiv.org/abs/2411.04468) | `frameworks/2411.04468_magentic_one.pdf` + `frameworks/magentic_one/README.md` | **Orchestrator + Task Ledger + Progress Ledger** 模式,值得抄 |
| 13 | LangGraph | frameworks | live | [github langchain-ai/langgraph](https://github.com/langchain-ai/langgraph) | `frameworks/langgraph/README.md` + `supervisor_README.md` | StateGraph + checkpointer,**我们推荐的底座** |
| 14 | CrewAI | frameworks | live | [github crewAIInc/crewAI](https://github.com/crewAIInc/crewAI) | `frameworks/crewai/README.md` | Crews + Flows + Sequential/Hierarchical Process |
| 15 | AG2 | frameworks | live | [github ag2ai/ag2](https://github.com/ag2ai/ag2) | `frameworks/ag2/README.md` | AutoGen 社区分叉,目前更活跃 |
| 16 | OpenAI Swarm | frameworks | 2024 | [github openai/swarm](https://github.com/openai/swarm) | `frameworks/swarm/README.md` | 极轻量教学框架,**已被 OpenAI Agents SDK 替代** |
| 17 | OpenAI Agents SDK | frameworks | 2025 | [github openai-agents-python](https://github.com/openai/openai-agents-python) | `frameworks/openai_agents_sdk_README.md` | Swarm 生产版,Handoff + Guardrails + Tracing |
| 18 | Letta (MemGPT) | frameworks | live | [docs.letta.com](https://docs.letta.com) | `frameworks/letta/README.md` | LLM-OS 风格分层记忆,**可作 MemoryChecker 的实现底座** |
| 19 | Multi-Agent Debate | adversarial | 2023 (ICML 24) | [arXiv 2305.14325](https://arxiv.org/abs/2305.14325) | `adversarial/2305.14325_multiagent_debate.pdf` | 多实例分歧 → 收敛,**自然的 self-improvement 信号源** |
| 20 | PAIR | adversarial | 2023 | [arXiv 2310.08419](https://arxiv.org/abs/2310.08419) | `adversarial/2310.08419_pair.pdf` | Attacker-target 迭代式 jailbreak,我们 Adversary 的算法骨架 |
| 21 | TAP | adversarial | 2023 | [arXiv 2312.02119](https://arxiv.org/abs/2312.02119) | `adversarial/2312.02119_tap.pdf` | Tree-of-Attacks + Pruning,比 PAIR 更高效的搜索 |
| 22 | Self-Rewarding LM | verifier_in_loop | 2024 | [arXiv 2401.10020](https://arxiv.org/abs/2401.10020) | `verifier_in_loop/2401.10020_self_rewarding_lm.pdf` | LLM-as-a-Judge + iterative DPO,reward 自演化 |
| 23 | Meta-Rewarding | verifier_in_loop | 2024 | [arXiv 2407.19594](https://arxiv.org/abs/2407.19594) | `verifier_in_loop/2407.19594_meta_rewarding.pdf` | actor→judge→meta-judge,**MemChecker 防 reward hacking** |
| 24 | Constitutional AI | verifier_in_loop | 2022 | [arXiv 2212.08073](https://arxiv.org/abs/2212.08073) | `verifier_in_loop/2212.08073_constitutional_ai.pdf` | RLAIF + constitution,verifier 的设计哲学源头 |
| 25 | Generative Agents | simulation_corpus | 2023 (UIST 23) | [arXiv 2304.03442](https://arxiv.org/abs/2304.03442) | `simulation_corpus/2304.03442_generative_agents.pdf` | **Memory Stream + Reflection + Plan 三件套**——我们 evaluation 的 ground-truth 对照组 |
| 26 | AgentSims | simulation_corpus | 2023 | [arXiv 2308.04026](https://arxiv.org/abs/2308.04026) | `simulation_corpus/2308.04026_agentsims.pdf` | sandbox + GUI,可做 corpus 来源 |
| 27 | SOTOPIA | simulation_corpus | 2023 (ICLR 24) | [arXiv 2310.11667](https://arxiv.org/abs/2310.11667) | `simulation_corpus/2310.11667_sotopia.pdf` | 社会智能 90 场景 + GPT-4 judge,multi-dim eval 范本 |
| 28 | SOTOPIA-π | simulation_corpus | 2024 | [arXiv 2403.08715](https://arxiv.org/abs/2403.08715) | `simulation_corpus/2403.08715_sotopia_pi.pdf` | filtered behavior cloning + self-reinforcement |
| 29 | A2A Protocol | orchestration | 2025 | [github a2aproject/A2A](https://github.com/a2aproject/A2A) | `orchestration/a2a/README.md` + `python_README.md` | Google 牵头的 agent 间通信标准,JSON-RPC/HTTPS |
| 30 | MCP | orchestration | 2024 | [github modelcontextprotocol](https://github.com/modelcontextprotocol/python-sdk) | `orchestration/mcp/python_sdk_README.md` | Anthropic 的 tool/resource 标准 |
| 31 | LangGraph Supervisor | frameworks | live | [github langgraph-supervisor-py](https://github.com/langchain-ai/langgraph-supervisor-py) | `frameworks/langgraph/supervisor_README.md` | hierarchical 多 agent 官方实现,核心代码 70 行抄即可用 |
| 32 | CAMEL framework | role_play | live | [camel-ai.org](https://github.com/camel-ai/camel) | `role_play/camel_README.md` | CAMEL 的开源实现 |
| 33 | ChatDev framework | role_play | live | [OpenBMB/ChatDev](https://github.com/OpenBMB/ChatDev) | `role_play/chatdev_README.md` | (README only) |
| 34 | MetaGPT framework | role_play | live | [FoundationAgents/MetaGPT](https://github.com/FoundationAgents/MetaGPT) | `role_play/metagpt_README.md` | (README only) |
| 35 | xLAM framework | data_generation_papers | live | [SalesforceAIResearch/xLAM](https://github.com/SalesforceAIResearch/xLAM) | `data_generation_papers/xLAM_README.md` | xLAM 工程实现 |

---

## 2. 分类精读

### 2.A multi-agent 数据合成论文

#### 2.A.1 ToolACE — 离我们最近的模板

- **元信息**:Weiwen Liu et al., Huawei/SJTU/USTC,arXiv 2409.00920,ICLR 2025
- **多 agent 架构**:三角色 — **User Agent**(发请求 + self-guided complication)、**Assistant Agent**(决策调 API 还是要更多信息)、**Tool Agent**(模拟 API 执行)。外加一个**Complexity Evaluator**(用待 fine-tune 的 LLM 自身的 loss 作为难度信号)。
- **通信协议**:轮转式 turn-based dialog(user→assistant→[tool if needed]→user…),所有 agent 共享一个 dialog history。
- **收敛判定 / 失败重试**:无显式收敛,target_turn_length 到了就停;每个 assistant action 生成 **N 次取 majority**(rule + model 双层验证作为 retry trigger)。
- **关键代码片段**:
  ```
  loss = -1/n_y * Σ log p(t_i | x, t_<i)        # complexity = LLM loss
  if loss < low: instruct user_agent to "make harder"
  if loss > high: instruct user_agent to "make simpler"
  Dual-Layer Verification:
    Rule: API name match? params required? regex match? dialog correctness?
    Model: hallucination detect, consistency, tool resp check
  ```
- **对我们 step2 的具体用法**:User Agent → **UserAgent**;Tool Agent → **EnvironmentAgent**;Complexity Evaluator + Dual-Layer Verification → 合并成 **MemoryChecker**;新加 **AdversaryAgent**(ToolACE 没有,是差异化)。
- **限制 / 坑**:无 long-range memory dependency 设计——dialog 内部基本无 cross-turn 信息复用。我们要把"几个 session 后再回访"的 trigger 加进去。

#### 2.A.2 APIGen-MT — **架构同构,直接抄**

- **元信息**:Akshara Prabhakar et al., Salesforce, arXiv 2504.03601, 2025
- **多 agent 架构**:**两相**:
  - **Phase 1**:Context Sampler → **LLM-based Data Generator** → **Format & Execution Checker** → **Review Committee**(多 LLM judge,majority vote on Correctness/Completeness/Satisfaction/Creativity) → **Feedback Generator** → 喂回 Generator。失败重试 ≤ 3-5 次。
  - **Phase 2**:用 Phase1 验过的 blueprint,启动 **Simulated Human** + **Test Agent** + **Environment** 三角 interplay,best-of-N + self-critique 防 simulated human drift,trajectory 用 state-based + output-based reward 过滤。
- **关键代码片段**:
  ```
  for retry in range(max_retries):
      task_cfg = data_generator(context)
      if not format_check(task_cfg) or not execution_check(task_cfg):
          feedback = feedback_gen(failures); continue
      scores = [judge.evaluate(task_cfg) for judge in committee]
      if avg(scores) >= threshold: break
      feedback = feedback_gen(reviews)
  for trial in range(N=4):
      traj = simulate(human_lm, test_agent, env, task_cfg.intent)
      if state_match(traj, task_cfg.gt_actions) and output_match(traj, task_cfg.gt_outputs):
          collect(traj)
  ```
- **成功率**:Phase1 70% vs no agentic feedback 28%(2.5x 提升);Phase2 67%
- **对我们 step2 的具体用法**:**Phase1 就是我们 step1→step2 之间的桥**;**Phase2 就是 step2 主体**——把 Test Agent 替换成 Memory Agent under test、Simulated Human 替换成 UserAgent。
- **限制 / 坑**:Simulated Human drift 是真实问题,Best-of-4 + self-critique 成本 4x。

#### 2.A.3 ~ 2.A.6 其他论文(xLAM / AgentTuning / MAG-V / PersonaHub / WildChat)

简要要点:
- **xLAM**:5-stage pipeline + 5 类错误检测器(Undefined Function / Argument Hallucination / Low-Quality Reasoning / ...)→ memory-specific 改名为 **Stale Memory Recall / Hallucinated Past Event / Cross-Session Inconsistency / Reflection Quality**
- **AgentTuning**:Task Derivation → Trajectory Interaction → Trajectory Filter(reward=1.0 only)。step4 直接借用
- **MAG-V**:**非 LLM verifier**(semantic similarity + graph edit distance + argument overlap 训 k-NN/SVM/RF)→ MemoryChecker 可混合 LLM + 结构化 metric
- **Persona Hub + WildChat**:UserAgent 的角色源 + 分布比对金标

### 2.B 角色扮演 / 多 agent 对话

#### 2.B.1 CAMEL — **多 agent 数据合成的圣经,必读**

- **元信息**:Guohao Li et al., KAUST, NeurIPS 2023, arXiv 2303.17760
- **架构**:**AI User**(planner) + **AI Assistant**(executor) + 可选 **Task Specifier / Critic**
- **关键 idea**:**Inception Prompting** — 对话开始前**一次性**注入 task specifier + system prompts,之后纯 turn-based
- **5 大终止条件(必须抄)**:
  1. `User No Instruct (3 轮)`
  2. `Assistant Instruct (角色翻转)`
  3. `End of Task Token (<CAMEL_TASK_DONE>)`
  4. `Token Limit`
  5. `Max Messages (40)`
- **4 大坑(照单避雷)**:
  1. **Role Flipping** → "Never flip roles! Never instruct me!"
  2. **Assistant Repeats Instruction** → 复读 user
  3. **Flake Replies** ("I will…" 不行动) → "Always start with Solution:"
  4. **Infinite Loop** → `<CAMEL_TASK_DONE>` token
- **对我们用法**:完全照抄 Inception Prompting + 5 终止条件,4 agent 都用

#### 2.B.2 MetaGPT — SOP + message pool
- **架构**:5 role(PM/Architect/PM/Engineer/QA),SOP 硬编码工作流
- **关键 idea**:**publish/subscribe message pool** — blackboard architecture 的现代复活
- **对我们用法**:**publish/subscribe = 我们的 SHARED_STATE 设计原型**。LangGraph 的 `Annotated[list, add]` reducer 就是这个语义

#### 2.B.3 ChatDev — Communicative Dehallucination
- 对话本身作为 verifier,一个 agent 出错时另一个对话中纠正
- **对我们用法**:Adversary↔User 的对抗本身承担一部分 dehallucination

### 2.C 开源框架

#### 2.C.1 AutoGen / AG2
- **核心抽象**:`ConversableAgent`(基类)+ `GroupChat`(message pool)+ `GroupChatManager`(LLM 选 speaker)+ `register_reply()`(拦截)
- **优**:`generate_reply` hook + 自定义 speaker selection 灵活度顶级
- **缺**:状态散在 agent.chat_messages,**lineage 追踪要自己写**;multi-agent token 成本容易爆
- **复用方式**:对比基线骨架

#### 2.C.2 LangGraph — **推荐底座**
- **核心抽象**:`StateGraph[StateType]` + `TypedDict` 状态 + `Annotated[T, reducer]` 增量合并 + `add_conditional_edges` 动态路由 + `compile(checkpointer=...)` 自动 persist
- **优**:
  1. 显式状态,lineage 天然落到 checkpointer,step5 几乎免费
  2. 失败重试是 conditional edge 的自然产物
  3. DAG 心智模型与论文 Figure 极易对齐
  4. `thread_id` 直接做 episode_id
  5. human-in-the-loop / interrupt 现成
- **缺**:学习曲线比 AutoGen 陡(要懂 reducer);对话风格不如 AutoGen 自然
- **复用方式**:**主干** — 见 §4.2

#### 2.C.3 ~ 2.C.6 其他框架
- **CrewAI**:学习曲线低,但 process 选项太少,自定义 routing 弱 → 不用
- **Magentic-One**:**Orchestrator + Task Ledger + Progress Ledger** → **lineage 输出的好模板**,step5 借用
- **Swarm / Agents SDK**:Swarm 已弃;Agents SDK 与心智模型不契合 → 不用
- **Letta**:LLM-OS 风格分层记忆 → **MemoryChecker 的实现底座**(候选,生产中可在 Letta 上跑获得 cross-episode reflection 能力)

### 2.D 对抗 / 红队

#### 2.D.1 Multi-Agent Debate(Du et al, ICML 24)
- N 个 LLM 实例对同一 query 各自给答 → 每轮读其他答案 → 更新自己 → 重复 R 轮
- **关键发现**:**初始答案高度多样,辩论后高度收敛**,不确定 fact 自动消失
- **对我们用法**:**MemoryChecker 内部可以是 mini debate** — 多个 judge 各自打分,辩论后给 final verdict

#### 2.D.2 PAIR + TAP
- PAIR:Attacker LLM 迭代查 Target,< 20 queries 出 jailbreak。TAP:Tree-of-Attacks + Pruning,70%+ 成功率
- **对我们用法**:**Adversary 不做 jailbreak**,目标改为**"制造 memory 难度"**,算法借 TAP 风格树搜索 + pruning

### 2.E Verifier-in-the-loop

- **Self-Rewarding LM**(Meta 24):LLM 同时充当 actor + judge,iterative DPO → MemChecker 可自我演化
- **Meta-Rewarding**(24):actor→judge→meta-judge → **防 reward hacking**,SIGMOD 投稿写 "why our verifier is reliable" 时有论据
- **Constitutional AI**(Anthropic 22):SL self-critique + revise → MemChecker system prompt = **memory bench constitution**

### 2.F 仿真 corpus

#### 2.F.1 Generative Agents(UIST 23)
- **架构**:每 agent 有 **Memory Stream**(NL events + timestamp + last_access)→ **Retrieve**(score = recency·α₁ + importance·α₂ + relevance·α₃)→ **Reflect**(周期性 synthesize)→ **Plan**(daily plan 递归分解)→ **Act**
- **重要性评分**:LLM rate "1=mundane 10=poignant",作 retrieval 权重
- **对我们用法**:**evaluation 的 ground-truth 对照组**——完美 memory agent 应该长得像 Generative Agent;**EnvironmentAgent 需要部分 memory stream 能力**(记得自己发过哪些 env event)

#### 2.F.2 SOTOPIA / SOTOPIA-π
- 90 scenarios × 40 characters,GPT-4 judge multi-dim:goal completion / finance / relationship
- **对我们用法**:**step4 auto-judge 直接抄 SOTOPIA-EVAL multi-dim rubric**——不要单总分,拆 4-7 维度

### 2.G 通信协议

- **A2A Protocol**(Google + 50 partners, 2025):JSON-RPC 2.0 + SSE,核心 primitives:Agent Card / Task / Message / Artifact → 消息 schema 参考
- **MCP**(Anthropic 2024):JSON-RPC 2.0,3 primitives:Tools / Resources / Prompts → **MemoryChecker 暴露为 MCP server**(SIGMOD 工程亮点)

### 2.H 评估(核心要点,散在 2.E/2.F/这一节)
- **Coverage metric**:5 维抽象(persona/trigger/anti_mode/env/adversarial)的覆盖率
- **Diversity metric**:n-gram + topic + persona 三轴 distinct ratio;与 WildChat 真实分布 KL 散度
- **Agreement signal**:Multi-Agent Debate round-0 vs round-R 答案 diff——diff 越大信息量越高,作 difficulty 信号
- **Disagreement-as-Difficulty**:同 corpus 灌两个不同 memory agent,answer 不一致部分就是难样本

---

## 3. 总结洞察

### 3.1 multi-agent 数据合成的"4 大模式"

| 模式 | 代表工作 | 适用场景 | 我们 step2 用不用? |
|---|---|---|---|
| **辩论式** | Du et al, Self-Rewarding | reasoning/factuality 任务 self-improvement | ❌ 只在 MemChecker **内部**用 mini debate |
| **角色扮演式** | CAMEL, ToolACE, ChatDev | 对话数据 / 任务执行 trajectory | ✅ 主路径 |
| **Verifier-loop 式** | APIGen-MT, Self-Rewarding, Constitutional AI | 需要正确性保证的复杂任务 | ✅ MemoryChecker |
| **仿真式** | Generative Agents, SOTOPIA, AgentSims | 社会动力学 / 长程行为 | 🟡 部分——EnvironmentAgent 需 state-changing 能力 |

**结论:我们 step2 = 角色扮演式 + Verifier-loop 式合体**——APIGen-MT 已验证的 SOTA 路径。

### 3.2 推荐架构(给我们 step2 用)

#### 3.2.1 底座选型:**LangGraph + checkpointer**

- **不选 AutoGen**:状态散落 agent.chat_messages,lineage 要补胶水代码
- **不选 CrewAI**:process 模型太少,自定义 routing 弱
- **不选自建**:checkpointer + interrupt + Studio 三件套自己写要 3 周

#### 3.2.2 4 个 agent 职责定义

**UserAgent**:扮演 blueprint.persona,基于 blueprint.triggers 中下一个未触发的事件发声。**严格不出戏**(借 CAMEL Inception Prompting:Never flip roles / Never instruct env or adversary / Always speak as persona)。anti_mode='evasive' 时含糊/跳话题/改主意,anti_mode='contradictory' 时与之前自己发言矛盾(memory inconsistency 难点)。**禁止编造 blueprint 外事实**。输出 schema:`{actor:'user', content, trigger_id, intent}`。failure mode:flake reply / role flip / 重复 trigger → MemChecker 检测返回 retry。

**EnvironmentAgent**:扮演"世界"——Slack 通知、日历事件、第三方消息、时间流逝、地点变化。基于 blueprint.environment_seeds 抽样。**职责不是回答 user**(避免变 ChatGPT),而是**主动注入 long-range dependency**:在 turn N 发 event,要求 step4 时被回忆/重整合。**state-changing**:同一 session 内 env 状态自洽。输出 schema:`{actor:'env', content, effect, depends_on}`。

**AdversaryAgent**:**注意:目标是制造 memory 难度,不是 jailbreak**。借鉴 PAIR/TAP 迭代式 attacker idea,基于 blueprint.adversarial_mode 注入对抗:**矛盾事实**、**诱导遗忘**、**压力测试**、**interruption**。**强度自适应**:MemChecker 反馈 too_easy 升级、escalate 维持。输出 schema:`{actor:'adversary', content, attack_type, intensity}`。

**MemoryChecker**:verifier-in-the-loop,APIGen-MT Review Committee 精简版 + Self-Rewarding judge + Constitutional AI 思想。每 K 轮(K=4)介入,做 **4 项评估**:**(1) 一致性** / **(2) 难度** / **(3) 覆盖度** / **(4) 收敛**。**输出 verdict ∈ {pass, retry, escalate, too_easy, done}**,**meta-judge 防作弊**:MemChecker verdict 由独立轻量 judge 抽查(Meta-Rewarding paper)。

#### 3.2.3 通信协议建议

**Message schema(wire format)**:
```python
@dataclass
class IngestEvent:
    session_id: str
    actor: Literal["user","env","adversary"]
    turn: int
    content: str
    salience: float
    meta: dict
```

**共享 state(LangGraph TypedDict)**:
```python
class FactoryState(TypedDict):
    blueprint: dict
    events: Annotated[list[IngestEvent], add]
    mem_check_log: Annotated[list[dict], add]
    turn: int
    retry_count: int
    last_verdict: str
    converged: bool
```

**轮次上限**:`max_turns=20`、`max_retries=3`

**收敛判定**:`MemoryChecker.verdict == 'done'` OR `turn >= max_turns` OR `coverage >= 0.9 AND difficulty == 'just_right' AND consistency_passed`

#### 3.2.4 失败重试策略

| 失败类型 | 检测者 | 动作 |
|---|---|---|
| format 错误 | LangGraph node | 节点级 retry 1 次,失败丢弃 event |
| consistency 违反 | MemoryChecker | verdict=retry,**回 UserAgent 重生成**(带 feedback) |
| too_easy | MemoryChecker | verdict=escalate,**回 AdversaryAgent 升级强度** |
| 死循环(turn>max) | router | __end__,episode 标记 partial |
| coverage 不足 | MemoryChecker | verdict=retry,**注入 missing dim hint** |

### 3.3 对 v3 设计的 5 条改动建议

1. **step1→step2 之间加 APIGen-MT 风格 Review Committee** — step1 出的 blueprint 不应直接喂 step2,先过 3 个 LLM judge majority vote + Feedback Generator → 重生成。**预期提升:step2 trajectory 成功率 ~30% → 70%**。
2. **MemoryChecker 不要单 LLM,做 mini Multi-Agent Debate** — 3 个轻量 judge(7B/13B)各打分 + 一轮 debate + majority vote。
3. **EnvironmentAgent 加局部 memory stream**(Generative Agents 风格,只保 last 50 events)——不然 env event 自相矛盾。
4. **AdversaryAgent 用 TAP 风格 tree search**——而非单次生成。MemChecker 反馈 too_easy 时,Adversary **生成 N 个分支(N=4)→ MemChecker 评 difficulty proxy → 留 just_right 的分支继续展开**。
5. **lineage 直接走 LangGraph checkpointer**——别在 step5 重新设计 lineage 数据结构。`thread_id == episode_id`,每个 state snapshot 自带 timestamp + parent state,生成 lineage graph 一次 `graph.aget_state_history(config)` 搞定。

---

## 4. 工具包(可直接落地)

### 4.1 框架对比表

| 框架 | License | 活跃度 | 核心抽象 | 数据合成 native | 学习曲线 | 用不用 |
|---|---|---|---|---|---|---|
| **LangGraph** | MIT | 极活跃 | StateGraph + checkpointer + Annotated reducer | 优 | 中-高 | ✅ **主干** |
| AutoGen | MIT | 活跃 | ConversableAgent + GroupChat | 中 | 低-中 | 对比基线 |
| AG2 | Apache-2.0 | 极活跃 | (AutoGen API 兼容) | 中 | 低-中 | 不主用 |
| CrewAI | MIT | 活跃 | Agent/Task/Crew/Flow + Process | 低 | 低 | ❌ |
| Magnetic-One | MIT | 维护中 | Orchestrator + Task/Progress Ledger | 中 | 中 | Ledger 抽象作 lineage 参考 |
| OpenAI Swarm | MIT(legacy) | 已弃 | Agent + handoff | 弱 | 极低 | ❌ |
| OpenAI Agents SDK | MIT | 活跃 | Agent + handoff + guardrail + tracing | 中 | 低 | 不用 |
| Letta | Apache-2.0 | 活跃 | LLM-OS + core/archival memory | 弱 | 中 | MemChecker 实现底座(候选) |

### 4.2 LangGraph 主干代码骨架

见同目录 `skeleton_step2_langgraph.py`(75 行,主推) 和 `skeleton_step2_autogen.py`(对比基线)。核心结构:

```python
from typing import TypedDict, Literal, Annotated
from operator import add
from langgraph.graph import StateGraph, START, END

class FactoryState(TypedDict):
    blueprint: dict
    events: Annotated[list[dict], add]
    mem_check_log: Annotated[list[dict], add]
    turn: int
    retry_count: int
    last_verdict: str
    converged: bool

def user_node(s): ...
def env_node(s): ...
def adv_node(s): ...
def mem_check_node(s): ...

def router(s) -> Literal["user","env","adv","mem","__end__"]:
    if s["converged"] or s["turn"] >= 20: return "__end__"
    v = s.get("last_verdict","")
    if v == "retry": return "user" if s["retry_count"] < 3 else "__end__"
    if v in ("escalate","too_easy"): return "adv"
    return ["user","env","adv","mem"][s["turn"] % 4]

g = StateGraph(FactoryState)
# ... add nodes / edges
factory = g.compile()  # 加 checkpointer=SqliteSaver 即得 lineage
final = factory.invoke(init, config={"configurable": {"thread_id": "ep-001"}})
```

### 4.3 message schema 建议

```python
@dataclass
class IngestEvent:
    session_id: str
    turn: int
    actor: Literal["user", "env", "adversary"]
    content: str
    salience: float = 0.5
    timestamp: float = 0.0
    blueprint_ref: str = ""
    trigger_id: str = ""
    depends_on: list[str] = field(default_factory=list)
    coverage_dims: list[str] = field(default_factory=list)
    difficulty_score: float = 0.0
    verified: bool = False
    attack_type: str = ""
    intensity: Literal["low","medium","high"] = "medium"

@dataclass
class MemCheckVerdict:
    verdict: Literal["pass","retry","escalate","too_easy","done"]
    reason: str
    feedback_for: Literal["user","env","adversary","none"]
    coverage: dict[str, float]
    difficulty: Literal["too_easy","just_right","too_hard"]
    consistency_passed: bool
```

### 4.4 通信 / 收敛 checklist

- [ ] 所有 agent 输出**严格 JSON**(LangChain `with_structured_output` 或 Pydantic),非 JSON node 内 retry 1 次后丢弃
- [ ] **CAMEL 5 终止条件**全部接入:no_instruct_3rounds / role_flip_detected / done_token / token_limit / max_messages
- [ ] **APIGen-MT 风格 review committee** 在 MemChecker 内部启用 3-judge majority vote
- [ ] **Meta-judge 抽查 MemChecker** 输出(每 10 个 verdict 抽 1 个 meta-review)防 reward hack
- [ ] **EnvironmentAgent local memory** 至少保留 last 20 own events 防自我矛盾
- [ ] **AdversaryAgent** intensity 由 MemChecker 反馈驱动,初始 intensity=low
- [ ] **lineage**:LangGraph checkpointer = SqliteSaver,thread_id = episode_id
- [ ] **重生成测试**:同一 thread_id + `graph.aget_state_history()` 拿 turn=k state → resume → diff with original final state
- [ ] **Diversity 监控**:每 100 episodes 算一次 n-gram distinct ratio
- [ ] **WildChat 分布比对**:每 1000 events 算一次 vs WildChat 1M 的 KL 散度
- [ ] **MCP 暴露**:MemoryChecker 包装成 MCP server 便于未来横向打通

---

## 5. 下载统计

| 类别 | 目标 | 实际 | 备注 |
|---|---|---|---|
| data_generation_papers | ToolACE/xLAM/APIGen-MT/AgentTuning/MAG-V/PersonaHub/WildChat | 7 PDF + 1 README | ✅ |
| role_play | CAMEL/MetaGPT/ChatDev + 3 README | 3 PDF + 4 README | ✅ |
| frameworks | AutoGen/Magnetic-One/LangGraph/CrewAI/AG2/Swarm/Letta/Agents SDK | 2 PDF + 8 README | ✅ |
| adversarial | Multi-Agent Debate/PAIR/TAP + README | 3 PDF + 1 README | ✅ |
| verifier_in_loop | Self-Rewarding/Meta-Rewarding/Constitutional AI | 3 PDF | ✅ |
| simulation_corpus | Generative Agents/AgentSims/SOTOPIA/SOTOPIA-π | 4 PDF + 2 README | ✅ |
| orchestration | A2A/MCP | 3 README | ✅(无 PDF) |
| eval | 嵌入其他 | 见 §2.H | — |

**总计:22 PDF + 13 markdown = 35 个资源,覆盖 8 个类别。**

---

## 6. 复用清单(其他 step 复用本调研)

- **step3 出题打标**:借 MAG-V 反向工程 trajectory + Self-Rewarding judge-as-rewarder
- **step4 自动判分**:借 SOTOPIA-EVAL multi-dim rubric + Meta-Rewarding meta-judge 防作弊
- **step5 lineage**:LangGraph checkpointer + Magnetic-One Task/Progress Ledger
- **step6 端到端验证**:Generative Agents 作 ground-truth memory agent 对照

## 7. 下一步建议

基于 `skeleton_step2_langgraph.py` 起底实现 `memory_bench_factory/step2/`,优先 UserAgent + MemoryChecker,EnvironmentAgent / AdversaryAgent 初版可降级为 stub。先用 APIGen-MT 的 2.5x 成功率提升(Phase1 Review Committee)验证整个 pipeline 跑通。
