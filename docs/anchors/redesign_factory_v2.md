# Redesign · Benchmark 工厂 v2 —— 元架构 / 中央办公室 / 多产线

> **定位**:把项目从「一个会换皮的**时间线** benchmark 生成器」升级为「一个能为**任意场景**实例化出**正交记忆挑战组合**的**元工厂**」。
> **要解决的根问题**:**场景区分度**。当前所有场景都走"造实体→按实体写文档"同一条认知骨架,换域=换皮,不同场景考的其实是**同一种**记忆(时间线回忆)。本设计让"不同场景考不同记忆能力"从**假设**变成**被设计、可测量**的事实。
> **来源**:对话推演(科层官僚隐喻)+ benchmark_landscape 调研(MEME/Memora/LongMemEval/LoCoMo/DataMorgana/YourBench/AutoBencher)+ 毕设 𝓕𝓔𝓠×M1–M6 诊断框架。
> **写法**:沿用 redesign 规范,关键环节写清 输入/谁/逻辑/输出/示例/出处。本文是**蓝图**,不是 as-built;实现按 §10 分阶段推进,预期长期调代码+prompt。

---

## §0 一句话理念

> **LLM 负责"好看"(真实、场景化的语料),代码负责"正确"(可证伪的答案);而"考什么"由一个固定纲领 + 场景化实例化共同决定。**

三层价值主张,逐层递进:
1. **生成器泛化**:喂「场景描述 + few-shot」就出 benchmark,人不碰 prompt(域无关化在此自然达成,旧 #42 被吸收)。
2. **正交覆盖**:一套场景**激活不同产线组合** → 覆盖正交的记忆挑战 → 真区分度。
3. **诊断仪**:每题带"考哪种记忆/该触发哪种失败"标签 → 跨系统测出"谁缺哪种能力"。

---

## §1 总架构:宪法 → 中央 → 产线 → 打包

科层官僚隐喻 ↔ 技术对应:

| 隐喻 | 技术组件 | 谁干 | 产物 |
|---|---|---|---|
| 纲领/宪法(唯物史观) | **元架构** = 记忆能力 taxonomy + 编排骨架 | 人写死 | 固定坐标系 |
| 中央决策办公室(反复开会) | **Agentic Planner** | 多轮 LLM(提案→批判→修订) | **白皮书** |
| 政策白皮书 | **实例化架构** spec | Planner 产出 | 见 §4 schema |
| 各部门官员(领会中央精神) | **产线 generator-agent**(每产线一个) | 并行 Agent + 代码 | (题, 代码gt) |
| 前终端打包 | **媒介渲染器** | LLM + 代码闸 | 场景化语料 + 题库 |

数据流(text 流程图):
```
场景描述 + few-shot
        │
        ▼  (中央办公室:多轮 agentic)
   ┌─────────────┐
   │  白 皮 书    │  激活哪些产线 / 各产线参数 / 共享世界schema / 输出媒介
   └─────────────┘
        │
        ▼  (代码:实例化"共享世界基质" —— 命门1)
   ┌──────────────────────────────────────────────┐
   │ 共享世界 = 实体·字段时间线 + 关系图 + 事件流 +  │
   │            潜偏好 + 注入矛盾 + absent集          │
   └──────────────────────────────────────────────┘
        │ 每产线读自己那片(命门2:各自带代码gt)
   ┌────────┬────────┬────────┬────────┬────────┐
   │时间线  │关系多跳│过程序列│偏好隐式│冲突可信│ … 产线
   └────────┴────────┴────────┴────────┴────────┘
        │ (题, 代码gt) 汇总
        ▼  (媒介渲染器:把"同一个世界"渲染成场景媒介)
   场景化文档集合 / 对话历史 / 交易流  +  题库(每题带产线/失败模式标签)
        │
        ▼  (四闸 + 完整性批判 + 真系统×多场景区分度测量)
   交付:benchmark
```

---

## §2 纲领(元架构):记忆能力坐标系 —— 固定,人写死

这是"宪法",定义"一个完整的 memory benchmark 该考哪些维度"。每个坐标 = **一条产线** = 一类记忆挑战 + 一种**代码 gt 基质**。综合 LongMemEval / LoCoMo / Memora / 毕设:

| # | 产线 | 考什么记忆 | gt 基质(代码) | 现状 |
|---|---|---|---|---|
| L1 | **时间线** | 时点回忆/最新值/何时变/聚合(max/min)/时长 | 状态机 + state-diff 切片 | ✅ V11 已有 |
| L2 | **关系/多跳** | A管B、B依赖C,跨实体 N 跳推理与聚合 | 关系图 + 图遍历 | ❌ 区分度最大 |
| L3 | **过程/事件序列** | 动作序列、因果链、"先后" | 事件日志 + 偏序 | ⚠️ ORDER 起步 |
| L4 | **偏好/隐式** | 从散落行为推断稳定偏好/画像(从不明说) | 潜偏好变量 + 行为发射 | ❌ 缺 |
| L5 | **冲突/可信度** | 矛盾检测、按新近/来源可靠度裁决、标注不确定 | 矛盾注入 + 裁决规则 | ⚠️ CONFLICT 很弱 |
| L6 | **边界/拒答** | 知道记忆的边界,不存在就拒答 | absent 集 + 越界探针 | ✅ ABS |
| L7 | **巩固/摘要** | 长跨度要点/趋势回忆(非逐字) | 区间聚合/趋势函数 | ❌ 缺 |

> 与毕设对齐:每条产线的题再叠加 **𝓕𝓔𝓠 算子 × M1–M6 失败模式**标签(§8 诊断层),L1–L7 是"考什么"、M1–M6 是"会怎么错"。两者正交,共同构成诊断坐标。

**纲领的不变量(写死)**:
- 每条产线**必须**提供 `(orders, code_gt, gates)` 三件套(§6 模板);没有代码 gt 的产线不准入厂。
- 所有产线**共用同一个世界基质**(§5),不得各造各的(防自相矛盾)。

---

## §3 中央办公室(Agentic Planner)—— 场景 → 白皮书

- **输入**:场景描述(自然语言)+ few-shot 文档(该领域真实样例,**不含 QA**)
- **谁**:多轮 agentic LLM(提案 Agent → 批判 Agent → 修订),收敛出白皮书
- **逻辑**:
  1. **领域解析**:从描述+few-shot 推断 `entity_noun`(实体叫什么:SKU/患者/案件)、字段 schema(哪些字段、各自类型:数值/类别/人名/状态)、**文档体裁**(few-shot 长什么样:周报/病历/对话)、**停用措辞**(下架/出院/结案)。← 这一步即旧 #42"域无关化",现内生于此。
  2. **挑战配置**:按场景**天然结构**决定**激活哪些产线、各配多重**。关系密的场景(组织/供应链)重配 L2;助手类重配 L4 偏好;新闻/多源类重配 L5 冲突。← **区分度在此被设计**。
  3. **媒介决策**:选输出媒介(§7)+ 其子体裁与节奏。
  4. **共享世界 schema**:产出实例化共享世界所需的全部 schema(实体数、字段、关系类型与密度、事件流、潜偏好、矛盾注入点)。
  5. **多轮自审**:批判 Agent 检查"白皮书是否覆盖了该场景该考的能力(对照纲领)、产线配比是否制造了足够区分度、媒介是否承载得了所有 gt",不过则回炉。
- **输出**:**白皮书**(§4 schema)
- **示例(电商场景,节选)**:`{medium:"交易流+评论+运营周报", active_lines:[{L1,w:0.3},{L4偏好,w:0.3},{L5冲突,w:0.2},{L2关系,w:0.2}], world:{entities:SKU×N, relations:[SKU-属于-类目, 类目-负责人], preferences:[买家画像], ...}}`
- **出处**:`factory/central_office.py`(新)
- **偷自**:AutoBencher(agentic 迭代搜配置)+ DataMorgana(可配置问题类目/persona)+ YourBench(few-shot 驱动)。

---

## §4 白皮书(实例化架构)schema —— 中央产物,驱动一切

```jsonc
{
  "scenario_id": "ecom_ops",
  "domain_profile": {                  // §3.1 域无关化的产物
    "entity_noun": "SKU",
    "field_schema": [ {"name":"售价","kind":"numeric"}, {"name":"运营负责人","kind":"person"},
                      {"name":"上架状态","kind":"status"} ],
    "doc_genres": ["运营周报","商品快照","调价通知","买家评论"],
    "stopped_phrase": "下架停售"
  },
  "medium": { "type":"mixed", "channels":["交易流","评论","周报"], "cadence":"weekly" },
  "shared_world_spec": {               // §5 共享世界,所有产线的唯一真值
    "entities": {"type":"SKU","count":120,"field_schema":"↑"},
    "timeline": {"n_sessions":104,"change_density":"每字段4-8次,铺满全程"},
    "relations": [ {"type":"属于","from":"SKU","to":"类目"},
                   {"type":"负责","from":"运营","to":"类目"} ],   // 解锁 L2 多跳
    "event_streams": [ {"entity":"SKU","events":["调价","补货","参加大促"]} ], // L3
    "latent_preferences": [ {"holder":"买家画像A","pref":"偏好高性价比","emit_via":["复购","评论倾向"]} ], // L4
    "contradictions": [ {"field":"库存","conflict":"两份报表口径不一","resolve_by":"recency"} ]  // L5
  },
  "active_lines": [                     // §2 激活哪些产线 + 配比(★区分度旋钮)
    {"line":"L1_timeline","weight":0.3,"params":{...}},
    {"line":"L4_preference","weight":0.3,"params":{...}},
    {"line":"L5_conflict","weight":0.2,"params":{...}},
    {"line":"L2_relational","weight":0.2,"params":{...}}
  ],
  "capability_targets": {"total_q":300, "per_line":{...}, "difficulty_curve":"easy→hard"}
}
```

> 白皮书 = "可执行的实例化架构"。换场景 = 换白皮书 = 换激活产线组合 = 换记忆挑战 → **区分度内生**。

---

## §5 共享世界基质(★命门1:防自相矛盾)

- **输入**:白皮书 `shared_world_spec`
- **谁**:代码为主(可编程 gt 的根)+ LLM 填创意素材(实体名/数值/关系的具体取值)
- **逻辑**:实例化**一个**统一世界对象,含多种子结构,各产线读自己那片但共享同一真值:
  - `entities[].fields[].timeline` —— 状态机(L1/L7 用)
  - `graph(entities, typed_edges)` —— 关系图(L2 用)
  - `event_log[]` —— 带 session 的事件流(L3 用)
  - `latent[]` —— 潜偏好变量 + 发射规则(L4 用)
  - `contradictions[]` —— 矛盾注入 + 裁决规则(L5 用)
  - `absent[]` —— 不存在字段(L6 用)
- **不变量**:一个事实只有一处真值;关系/事件/偏好都**派生自或一致于**实体时间线(代码做一致性校验 `world.validate()`,冲突即回炉)。
- **输出**:`SharedWorld`(可序列化,带 `value_at`/`neighbors`/`events_of`/`latent_of` 等代码查询)
- **示例**:`SKU#42 售价时间线 + 属于"女装"边 + "参加618大促"事件@s24 + 买家A对它复购3次(发射"高性价比偏好")` —— 同一个 SKU,五条产线五种视角,**零矛盾**。
- **出处**:`factory/shared_world.py`(扩 V11 `world_state.py`)

---

## §6 产线模板(★命门2:每产线自带代码 gt)

**铁律**:每条产线 = `读世界子结构 → 点菜(orders)→ 代码算 gt → LLM 出题面 → 四闸`。**没有代码 gt 的产线不准入厂**(否则沦为"LLM写问答",丢护城河)。

| 产线 | 读哪片世界 | 点菜→代码gt | 题面示例 |
|---|---|---|---|
| L1 时间线 | timeline | `value_at_session` / `gt_mr` / `gt_event_order` … | "第N周X多少" |
| L2 关系多跳 | graph | 图遍历:`X的类目的负责人是谁`(2跳) | "卖X的那个类目,谁在管?" |
| L3 过程序列 | event_log | 偏序:事件真序,打乱呈现,Kendall-τ 判 | "把这几件事按先后排" |
| L4 偏好隐式 | latent + 发射 | gt = **被定义的潜偏好**(行为可反推) | "据他历次行为,他更看重价格还是品质?" |
| L5 冲突可信 | contradictions | gt = 裁决规则裁出的真值/"标注矛盾" | "两份报表打架,该信哪份/能确定吗?" |
| L6 拒答 | absent | gt = INSUFFICIENT | "X的[不存在字段]是多少?" |
| L7 巩固摘要 | timeline 区间 | 区间聚合/趋势函数 | "这季度整体趋势是涨是跌?" |

**软挑战 gt 的关键招式(L4/L5)**:**"先在代码里定义潜变量,再发射成证据"**。如 L4:代码定 `买家A.偏好=高性价比`,发射成"专挑打折复购、评论提价格"等行为;完美记忆能从证据反推出该潜变量,**gt 就是那个潜变量**——可证伪。L5 同理:代码注入矛盾 + 写死裁决规则,gt = 规则输出。

- **输出**:每产线一批 `(question, code_gt, line_tag, params)`
- **出处**:`factory/lines/L*.py`,共用基类 `ProductionLine`(强制 `gt()` 抽象方法)

---

## §7 场景化媒介渲染器(把"一个世界"渲染成场景媒介)

- **输入**:SharedWorld + 白皮书 `medium` + 全部产线的 orders(知道哪些事实要被"埋进"语料且可答)
- **谁**:LLM 渲染 + 代码闸(grounding/防泄漏)
- **逻辑**:
  1. **按媒介渲染**:文档→周报/邮件;对话→多轮会话(像 LoCoMo);交易流→结构化记录+评论。**同一个世界,不同媒介外壳**。
  2. **mention-on-change 纪律推广**:每个事实只在其"发生"的那个时间点/那条消息里出现一次,之后不复述 → 逼跨期/跨消息回忆。
  3. **草堆**:领域匹配 filler 把信号淹没(全 LLM,每篇不同,见理念:频繁调用但每次有意义)。
  4. **gt 存活校验(代码)**:渲染后查每个 gt 承载值是否真出现在语料里,不在就重渲(命门2 的延伸:渲染不能把答案弄丢)。
- **输出**:场景化语料(媒介对应形态)+ 题库
- **示例**:电商 → `交易流(SKU调价/补货记录)+ 买家评论(发射偏好)+ 运营周报(数值)+ 大促公告(草堆)`,而非千篇一律的"周报"。
- **出处**:`factory/render/*.py`(媒介各一个 renderer,共用防泄漏闸)

---

## §8 验证 + 诊断层(区分度从"假设"变"测量")

- **四闸**(每产线复用 V11):grounding(代码)/ 题答自洽(LLM)/ 硬判别器(无语料·单证据 baseline)/ judge_wf。
- **完整性批判 Agent**:对照纲领,审"白皮书有没有漏掉该场景该考的坐标、产线配比有没有制造区分度"。
- **诊断标签**:每题打 `line_tag(L1–L7) × failmode(M1–M6 𝓕𝓔𝓠)`。
- **★区分度测量(判决性实验)**:多个**结构不同**的记忆系统(纯检索 / recency窗口 / 长上下文 / Mem0类)× 多场景,看**排名是否翻转**(system×scenario 交互):
  - 翻转 → 场景真有区分度(**铁证**,论文核心结果);
  - 不翻转 → 产线配比没拉开,回炉调白皮书。
- **出处**:`factory/validate.py` + `tools/discrimination_eval.py`(新)

---

## §9 技术借鉴(可落地)—— 从 14 篇精读吸收

> 详细对标/威胁评级见 `docs/anchors/related_work.md`。本节只挑【能直接改进设计 + 写得清"具体怎么干"】的,按**接入点**组织。每条附 **arxiv + 本地素材**(`refs/X/` = 已 clone 可读源码,否则注明)。
> **早期已吸收(不再展开)**:AutoBencher(中央办公室 agentic 迭代)、YourBench(few-shot 驱动 + 新鲜虚构实体防污染)、LongMemEval(能力 taxonomy → §2)、LoCoMo(对话媒介 → §7)、MEME(多实体世界,已在 V11)。

### 9.1 中央办公室 → 加 **Self-Rubrics 前置质量标准**(CodecLM)
- **现状**:白皮书 → 产线直接出题/渲染。**改**:白皮书每条 `active_line` 增 `rubric` 字段 = 该产线题**必须满足的、可代码机判**的约束清单(LLM 生成 rubric,但每条须落到 gt 可计算项,如"答案随 state-diff 唯一""证据 ≥2 周")。
- **怎么干**:`pipeline/central_office.py` 出白皮书时为每产线产 rubric;`pipeline/constitution.py` 的 `LineSpec` 加 `rubric_tmpl`;`run_factory_v2.py` 四闸新增 `rubric_gate`(校验 rubric 项命中)。把"先定标准再合成"前置化。
- **出处**:CodecLM(NAACL 2024 Findings, **arxiv 2404.05875**;无开源 repo,纯 prompt 工程可照抄)。

### 9.2 硬判别器 → **LKP/MKO 双角色 + 对比过滤**(AgentFrontier + CodecLM)
- **现状**:硬判别器 = 无语料/单证据 baseline 答中 → 丢。**改**:三档——弱 baseline(纯 recency / 无记忆)秒杀 = 太易,丢;`gt 可判 ∧ 弱 baseline 答不中 ∧ 强弱 gap 大` = 保;连 oracle/强系统都错 = 送审(疑似题坏)。**gap 只用来排"训练/区分价值",对错永远由代码 gt 判**(替掉它们的 LLM-judge)。
- **怎么干**:`eval/memory_interface.py` 加 LKP(recency窗口)/MKO(强检索) baseline 矩阵;`run_factory_v2.py` Stage F 接入。
- **出处**:AgentFrontier(**arxiv 2510.24695**,LKP/MKO/IsSolvableBy)+ CodecLM(2404.05875,Contrastive Filtering θ 阈值)。

### 9.3 难度旋钮 → **WizardLM 5 算子结构化、可证伪**(Evol-Instruct/WizardLM)
- **现状**:难度靠场景规模(实体/周/草堆)。**改**:把 Evol 的 5 个"变难"算子从 prompt 降级品重做成**确定性变换**——`+hop`(L2 跳数)/`+cascade`(级联依赖)/`+distractor`(同 schema 干扰实体)/`+constraint`(改变 gt 的硬约束)/`+evidence-span`(证据跨更多周/文档)。每拧一档 **gt 由代码重算**,难度=被测量而非"祈祷 LLM 改对"。
- **怎么干**:白皮书 `capability_targets.difficulty_curve`;产线 `params`;`run_factory_v2.build_world` / `order_gen` 的生成参数。
- **出处**:WizardLM(ICLR 2024, **arxiv 2304.12244**, github `nlpxucan/WizardLM`,算子 prompt 在 `Evol_Instruct/depth.py`;本地未 clone)。

### 9.4 产线配比 → **loss 反馈在线配比 + 能力依赖图实测**(Skill-it!)
- **现状**:中央办公室**静态**配比 `active_lines.weight`。**改**:① 先用【单训 vs 合训消融】实测一张 **7×7 能力依赖图**(对每对 Lᵢ,Lⱼ:造只含 Lᵢ 原料 vs Lᵢ+Lⱼ,看真系统在 Lⱼ 题的分是否被拉动)——**别预设 L1–L7 有层级**(Skill-it 硬教训:语义像层级 ≠ 训练有偏序);② 配比升级在线乘法权重 `wᵢ ∝ exp(η·Σⱼ Aᵢⱼ·(1−真系统在Lⱼ得分))`,把产能倾向"先修薄弱 + 目标未达标"的能力;动态配比 > 硬课程。
- **怎么干**:新建 `tools/dependency_ablation.py`(产依赖图);中央办公室配比层接 eq.4(代码现成,见本地 `refs/Skill-it/trainer/mw_trainer.py`)。
- **出处**:Skill-it!(NeurIPS 2023 Spotlight, **arxiv 2307.14430**;**本地 `refs/Skill-it/`**)。

### 9.5 判分 → **FAMA 遗忘指标 + online 流式协议**(Memora + LifeDialBench)
- **FAMA**:FORGET/L4 判分 = `max(0, MPA − λ·(1−FAA))`,MPA=该召回的召回率,FAA=该排除的过时值排净率(拆双分,比单准确率更暴露"误用过时值")。**online 协议**:评测时**冻结记忆态 M_t、按时间因果流式答题**(治"未来上下文污染")。
- **怎么干**:产线判分函数加 FAMA;`tools/v10_eval_memsys.py` / Stage G 加流式冻结 M_t。
- **出处**:Memora/From Recall to Forgetting(**arxiv 2604.20006**;**本地 `refs/Memora/`**)+ LifeDialBench(**2604.11182**,github `RayNeo-AI-2025/LifeDialBench`)。

### 9.6 有效性 → **多样性量化(回应"多样性"最在意)**(DataMorgana + InfoSynth)
- **改**:语料发布前算 **NDG(n-gram 多样性)/4-gram 自重复率/gzip 压缩比/句向量平均余弦 HS**(DataMorgana)+ **KL 散度新颖度 / 微分熵多样性**(InfoSynth),带 bootstrap p 值,**用数据证明语料不同质 + 不同场景产线区分度**(把"多样性"从印象变带 p 值的硬指标)。
- **怎么干**:新建 `tools/diversity_metrics.py`;Stage G 出多样性报告(已对 office 跑过 char-bigram Jaccard,升级成这套)。
- **出处**:DataMorgana(**arxiv 2501.12789**)+ InfoSynth(**2601.00575**);均无本地,见 related_work §1。

### 9.7 防泄漏/污染 → **ProDa 结构正交论证 + BETR 反相似度自检闸**(ProDa + BETR)
- **ProDa**:形式化"训练原料(L1/L2)与评测题(L3)**实例级不可达**"——我们对应"信号事实只在变更周渲染一次、答题须跨周组合",把 mention-on-change 防剧透**写成形式化论证**(补强论文理论性)。**BETR 反用**:产出题 embedding vs 公开 benchmark 语料,**过近(sim>θ)则丢**,做污染自检闸(它对齐 benchmark=污染原型,我们反着用)。
- **怎么干**:`run_factory_v2.py` 四闸加 `contamination_check`(借 BETR 的 embed + max-sim 聚合);防剧透论证写进 §7 + 论文。
- **出处**:ProDa(**arxiv 2604.24819**;**本地 `refs/ProDa/`**,正交论证见 `proda/benchmark_generator.py`)+ BETR/Apple(**2507.12466**,无 repo)。

### 9.8 对照/硬干扰 → **能力因素层反事实**(Polyjuice + DICT/RACE)
- **现状**:草堆=无关 filler。**改**:造"**差一点就对**"的硬干扰/对照题——沿**一个能力因素**(改一个时间戳让答案翻面 / 删一条桥接证据让多跳断链 / 换一个消歧实体)做受控反事实,gt 翻面,**最小对照 = 强干扰**。把 Polyjuice 的 `control-code + blank + 回填` 范式从 **token 层抬到能力因素层**(它们停在 token = 我们的机会)。
- **怎么干**:产线出 `counterfactual` 对照题(L1 改时间戳 / L2 删桥 / L5 换实体);blank 选位用我们的 gt 图(比它的句法启发更准)。
- **出处**:Polyjuice(ACL 2021, **arxiv 2101.00288**;**本地 `refs/Polyjuice/`**,control-code 在 `polyjuice/generations/special_tokens.py`)+ DICT(ACL 2025)/RACE(EMNLP 2023, **2310.14508**, github `AAAndy-Zhu/RACE`)。

### 9.9 迭代闭环 → **STaR/ReST 自举**(STaR + ReST)
- **改**:质量闸失败的题**不丢**——把 gt 当 hint 回喂重出/重渲(STaR rationalization,失败样本注信号回炉);质量闸阈值**逐轮抬高 + 语料复用**(ReST grow-improve,摊薄生成成本)。
- **怎么干**:产线 `loop-until` + 中央办公室多轮议会接入"失败回炉 + 阈值渐严"。
- **出处**:STaR(NeurIPS 2022, **arxiv 2203.14465**, github `ezelikman/STaR`)+ ReST(**2308.08998**,DeepMind,无官方 repo)。

### 引用 × 本地素材 速查表
| 工作 | arxiv | 本地素材(点开即看) |
|---|---|---|
| **ProDa**(最形似,威胁中) | 2604.24819 | **`refs/ProDa/`**(可跑,163★) |
| **Skill-it!** | 2307.14430 | **`refs/Skill-it/`** |
| **MASS** | 2503.14917 | **`refs/MASS/`** |
| **Polyjuice** | 2101.00288 | **`refs/Polyjuice/`** |
| **Memora** | 2604.20006 | **`refs/Memora/`** |
| CodecLM | 2404.05875 | 仅 arxiv |
| WizardLM/Evol-Instruct | 2304.12244 | github `nlpxucan/WizardLM` |
| STaR / ReST | 2203.14465 / 2308.08998 | STaR github `ezelikman/STaR` |
| DICT / RACE | (ACL25) / 2310.14508 | RACE github `AAAndy-Zhu/RACE` |
| BETR | 2507.12466 | 无(Apple 未开源) |
| AgentFrontier | 2510.24695 | 仅 arxiv |
| DataMorgana / InfoSynth / LifeDialBench | 2501.12789 / 2601.00575 / 2604.11182 | 见 related_work,LifeDialBench 有 github |

> **一条贯穿洞察(进论文 intro)**:上述 14 篇表示"能力"非此即彼——人工写死标签(粗)或 LLM 自由文本(不可证伪),**无一有"代码可证伪的能力表示"**;而同学调研苦寻的"Capability Factors 中间层",正是我们 **𝓕𝓔𝓠 × M1–M6 × L1–L7** 已给出之物。**我们的诊断层既是护城河,也是这条线公开未解问题的答案。**

---

## §10 分阶段落地(P0–P5)

- **P0(已有)**:L1 时间线产线 + 代码 gt + 四闸 + 渲染。**降级归位**为产线之一。
- **P1**:写死**纲领(L1–L7 接口)+ 白皮书 schema**;搭**中央办公室**(先单轮:场景+few-shot→白皮书;后多轮 agentic)。← 域无关化在此达成。
- **P2**:做**第二条产线 L2 关系/多跳**(区分度最大、代码 gt 最干净=图遍历);打通"**两产线共享一个世界、一起渲染、零矛盾**"的端到端。← 里程碑:证明架构成立。
- **P3**:**媒介灵活渲染器**(先文档,再对话);gt 存活校验。
- **P4**:逐条加 L3 过程 / L4 偏好 / L5 冲突 / L7 摘要,每条自带代码 gt(L4/L5 用"潜变量+发射")。
- **P5**:**诊断层 + 区分度判决实验**(多系统×多场景排名翻转)。← 论文核心结果。

> 每条新产线的"准入清单":① 世界子结构 schema;② 代码 gt 函数(可单测);③ 四闸适配;④ 至少一个媒介能承载其 gt。四样齐了才算一条产线。

---

## §11 硬骨头与风险(诚实)

1. **软挑战 gt(L4/L5)最难**:靠"潜变量+发射",但要保证**证据足以唯一反推**潜变量(发射太弱→不可答,太强→泄漏)。需专门调"发射强度"旋钮 + 可答性闸。
2. **共享世界一致性**:产线越多越难保证零矛盾,需强 `world.validate()`(代码)。
3. **成本**:agentic 多轮 + 多产线 + 多 Agent,贵。已定调"为质量不怕频繁调 LLM,但每次调用要有意义、不重复"。
4. **验证规模化**:四闸 × 多产线 × 大题量 = 大量 LLM;需采样 + 完整性批判兜底。
5. **媒介↔gt 耦合**:某些 gt 在某些媒介里难自然承载(如"时长"在对话里);白皮书选媒介时要校验承载力。

---

## §保留(V10/V11 已对的,直接进 v2)

状态机 + state-diff 机械 gt(L1 基质)、mention-on-change 防剧透(推广到所有媒介)、四闸、Stage G 有效性(真系统 EmbedMemory + FAMA + 点二列区分度)、非单调 ShapeSpec、ORDER/Kendall-τ(L3 雏形)、PREEXPIRE、json_repair 兜底、断点续跑 + 防呆闸 + 显式分段超时(XL 工程经验)。

---

## 附:与"区分度"的闭环(为什么这套真能解决根问题)

1. 不同场景 → 中央办公室产出**不同白皮书** → **激活不同产线组合 + 不同配比**(§3.2)。
2. 不同产线 = **不同记忆挑战**(§2),不是换皮 → 认知骨架真的不同。
3. 共享世界(命门1)保证语料连贯;代码 gt(命门2)保证答案可证伪、护城河不丢。
4. 诊断层 + 多系统×多场景(§8)**测出排名翻转** → 区分度从"假设"变"实测铁证"。

**这就是把"假设不同场景有区分度"工程化、可证伪化的完整路径。**

---

# §L2 产线详设 —— 关系多跳(informed by HotpotQA / 2Wiki / MuSiQue / 时序KGQA)

> **原则**:产线 ≤7 条 → 每条**特事特办、像 L1 那样分版迭代**。本章是 L2 的 v0 设计 + 迭代路线。
> **调研背书**:四家多跳构造法已扒(见 §9 扩充)。结论:**L2 走 2Wiki 的"KG路径→代码gt"路线最稳,且我们更干净**(合成世界=零对齐成本、无预训练污染、gt 代码可重算),并天然多一个**时序**维度。

## L2.1 考什么
给【起点实体 + 路径结构】、**不给中间桥实体、不给答案**,逼模型沿关系链**跨文档/跨周**回忆+遍历。例:"负责 X 部门的那个人,**他向谁汇报**?"(给 X + "负责人→汇报"结构,答终点 Q)。与 L1(单实体时点)正交。

## L2.2 世界基质 = 软外键 over 状态机(命门1 的具体化)
- **不造新图结构**。人员/项目也是普通 entity(各有时间线);**某些字段的值 = 另一实体的名 = 一条边**(软外键)。`部门X.负责人=P` → 去查名为 P 的人员实体的 `汇报对象`。
- 边的来源 = 白皮书 `shared_world_spec.relations`(中央议会·怀疑视角产出,如 部门→负责人→汇报对象)。
- **★引用完整性**(L2 特有、L1 没有):中间跳字段值必须是**真实存在的实体名**。解法:**先造实体池(人员/项目),字段值从池里选**,代码校验无悬空外键。合成世界让这件事天然可控(对比 2Wiki 一半工作量在对齐脏 Wikidata)。

## L2.3 gt = 时间感知图遍历(命门2,代码、可重算)
`gt_multihop(ws, start, field_path, at_week)`:沿 field_path 逐跳取**状态机切片值**;非末跳的值=下一跳实体(解外键),末跳值=答案;返回 `{answer, path_evidence:[{entity,field,value,session}], at_week}`。中间断链/取不到 → `INSUFFICIENT`。
- 搬 2Wiki Algorithm 2(1跳→2跳→n跳链扩)+ TempQA-WD"gt 即可执行查询、边变可重算"。
- **题、答、证据、分解同源于同一次遍历**(2Wiki Alg 1/2 的精髓)。

## L2.4 点菜(组合 + 唯一性)
- **组合**(MuSiQue):枚举 `hop1.答案 = hop2.起点实体` 的路径。
- **唯一性**(2Wiki 硬约束):路径在 `at_week` 必须**唯一可解**(一对多边丢弃)。
- **两种题型**:
  - **bridge** 链式遍历(跨周追实体字段变更);
  - **comparison** 同类两实体比同字段(**免边、零成本逼双实体**,如"A、B 两部门谁的 P0 峰值更高")。

## L2.5 出题 = 两段式(治 2Wiki 的模板 artifact)
代码用图遍历**锁死骨架(题意/gt/证据/路径/at_week)**;LLM **只润色措辞**(自然度);**gt 与证据路径绝不让模型重写**。「结构靠代码、措辞靠模型」。**★桥实体不进题面**(MuSiQue/2Wiki 反捷径):题干只给 hop1 入口,不出现 hop1 答案那个中间实体。

## L2.6 ★反捷径 = 断连过滤闸(把 L1 的 memory-necessity 闸升级)
对标 MuSiQue/DiRe:每道 L2 题跑两探针——
- (a) **单文档/单周** baseline 能否答对?
- (b) **屏蔽 hop1 证据**后能否仍答对?

**任一探针答得出 → 淘汰**(说明可单跳抄近路)。干扰文档主动塞**同类时序实体**(相同 schema、不同时间点),而非随机噪声(HotpotQA distractor 教训)。

## L2.7 ★时序难度旋钮(TimeQA 金点子,完美配 mention-on-change)
问的"那一周"落在:
- **变更边界周** = 易(当周文档有直证);
- **两次变更的中段** = 难(无当周直证 → 必须回溯上次变更周 + 沿时变边多跳)。
mention-on-change 天然制造这个:中段问题逼跨周拼接。

## L2.8 判分 + unanswerable
- 终点 EM / Hits@1;路径/证据可选 Joint-F1(2Wiki)。
- **unanswerable 对照**(MuSiQue/TimeQA):问"关系存在前的周"或"图上无此路径的周" → gt=INSUFFICIENT,复用 L6 拒答。代码程序化生成对照对。

## L2.9 迭代路线(像 L1 分版磨)
- **v0**:2 跳 bridge+comparison,查询落**变更边界**(易);打通"软外键世界 + `gt_multihop` + 断连过滤闸 + 两段式出题"。**先把代码 gt + 枚举 + 自检跑绿(无需 LLM)**。
- **v1**:查询落**中段**(时序隐式多跳=签名难度)+ 时变边。
- **v2**:3 跳 + unanswerable 对照 + Joint-F1。

## L2.10 复用 vs 新建
- **复用**:`assemble_world` 状态机(人员/项目也是实体)、`order_gen` 框架、四闸、mention-on-change 渲染、Kendall 之外的 EM 判分。
- **新建**:① 多实体类型 + relation_schema 的世界生成(带引用完整性);② `gt_multihop`(world_state);③ L2 路径枚举(order_gen);④ 断连过滤闸;⑤ L2 两段式出题句式。

## L2.11 出处
HotpotQA(arXiv 1809.09600)/ 2WikiMultiHopQA(arXiv 2011.01060, KG路径→模板+evidence)/ MuSiQue(arXiv 2108.00573, 组合+DiRe断连过滤)/ 时序KGQA:CronQuestions(2106.01515)、TimeQA(2108.06314)、TempQA-WD(2201.05793, SPARQL即gt)/ LoCoMo(2402.17753, 跨session多跳+evidence-turn)。

---

# §L3 产线详设 —— 过程序列(as-built:`pipeline/lines/L3_process.py`,自检 11/11)

> **状态**:已落地、单能力线 `L3_order`。这条线把原来临时挂在 L1 上的 ORDER **接手、独立成线**,并补上 L1 给不了的那道校验闸。
> **一句话**:L1 问"某时点的值",L3 问"这些散落各文档的变更,谁先谁后"——时序重建,与 L1 正交。

## L3.1 考什么
给【一组跨字段变更事件】(打乱呈现、不给日期/周号),逼模型把它们按**真实发生先后**排序。例:某部门「P0 占比」「Oncall 人数」「负责人」各变过一次,问这三件事的先后。考的是**跨字段事件的时序重建**,而非 L1 的"第 N 周该字段是多少"。

## L3.2 世界基质 = 变更事件流本身(免费)
- **不造新基质**。状态机里每个 `UPDATE/EXPIRE/DELETE` op 天然就是一条事件日志;`gt_event_order(ws, entity)`(world_state)已把该实体所有变更按 `(date, session)` 排成真值时序(初始 `SET` 不算"变更",不计)。
- 所以 L3 的 `prepare` = **no-op**(默认):直接复用共享世界,零额外注入、对别的产线零副作用。
- 与 L2 的"软外键侧信道"不同:L3 一行基质代码都不写,纯粹换一个**视角**去读同一个状态机。

## L3.3 gt = gt_event_order 重排(★比 L1 的 ORDER 强:能进校验闸)
- `gt()`:**不信** order 里 `aux` 烘焙的日期,而是用 `gt_event_order` 从世界**重新**算出真值时序,再过滤到本题选中的那几张卡片(按 `(field, value, session)` 匹配),结果就是有序 list。
- **★这是 L3 相对 L1 ORDER 的关键升级**:L1 当年把 ORDER 排除在校验闸外,因为"选哪几张卡片"这步没法只从 entity 重算;L3 把"选中集合"显式存进订单,于是 `gt() 重算 == enumerate 烘焙` 这道**校验闸**得以成立(自检里有该断言)。可重算 = 护城河没漏。

## L3.4 点菜(每实体取 ≥3 个跨字段·跨周事件)
- `enumerate_l3_orders`:对每个实体两趟选——
  - **pass1**:字段、周**都唯一**(信息量最大,不退化成"同一字段的值流水");
  - 不足 3 个再 **pass2**:放宽到仅跨周;取前 `max_events`(默认 4)个。
- 凑够 ≥3 才出题,按 `(date, session)` 排好烘焙进 `gt`,并记 `evidence_sessions`(≥3 周 → 强制跨文档回忆)、`aux.n_fields`(跨字段数)、`aux.scorer="kendall_tau"`。

## L3.5 出题 = 按字段名(非时序)呈现(防泄漏)
- `intent`:把选中事件**按字段名排序**后呈现(`「字段」变为 值` / `「字段」停止统计`),**绝不**按真时序摆——否则正确顺序就直接喂给模型了。
- `hide` 列出所有事件日期:日期是最强时序线索,必须从题面隐藏;题面只给事件本身,不给任何周号/先后提示。
- 自检用一个"字段名序 ≠ 真时序"的错位世界专门验过:呈现序确实与真序错位 = 不泄漏。

## L3.6 判分 = Kendall-τ(序相关,非 EM)
排序题不能用精确匹配判对错,用 **Kendall-τ** 衡量预测序与真序的相关度。scorer 名写进 `aux.scorer`,由评测侧取用(产线只负责把它标出来)。

## L3.7 复用 vs 新建
- **复用**:`gt_event_order`(world_state 现成)、`ProductionLine` 四件套、mention-on-change 渲染(事件只在其发生周出现一次 → 逼跨周拼接)。
- **新建**:① `enumerate_l3_orders` 的两趟跨字段·跨周点菜;② 把"选中集合"显式入订单以打通校验闸;③ 字段名序的防泄漏出题句式;④ Kendall-τ 标注。

## L3.8 出处
ORDER/Kendall-τ 雏形(V10/V11 已有,见 §保留)→ 本线接手独立成线;偏序/事件序列能力对标 §2 的 L3 坐标。

---

# §L5 产线详设 —— 冲突可信(as-built:`pipeline/lines/L5_conflict.py`,自检 20/20)

> **状态**:已落地、单能力线 `L5_conflict`,v0 裁决规则 = `source_reliability`。
> **一句话**:不是"历史出现过不同值"(那是合法演化),而是**同一时刻、不同来源各执一词**,按裁决规则定夺该信谁。

## L5.1 考什么(与 L1.CONFLICT 划清界限)
- **L1.CONFLICT**(`gt_conflict`,world_state):只检测"同字段跨 session 出现过 ≠ 值"——那是**合法的时间演化**(负责人换了人),不是矛盾。
- **L5**:**同一时刻**、两个来源对**同一个事实**给出互斥说法(一份官方通报、一份小道传闻),问到底认定哪个。这才是"冲突可信度",与 L1 正交。

## L5.2 世界基质 = 跨来源矛盾【侧信道】ws.conflicts(★命门1:不污染 canonical)
- **新增侧信道** `WorldState.conflicts`(world_state 字段)。`prepare` 调 `inject_conflicts`:挑**文本类**(person/status/category,或值全非数值)、**且已有权威值**的字段,在那一周追加一条**低可信"小道"值**。
  - 小道错误值 = **同字段全局值池里 ≠ 权威值的另一个真实取值**("张冠李戴",看似合理而非瞎编)。
  - 取**中段**权威值(避开端点,稳)作 `authoritative_value`,源 = `官方通报`;小道源从 `内部群聊转述/未经核实的外部传闻/…` 里选。
- **★命门1:canonical 时间线一动不动** —— 小道值只进 `ws.conflicts` 侧信道,不写回状态机,因此 **L1/L2/L3 的 gt 不被污染**。自检里专门断言"注入后负责人 latest 仍是真值"。
- **小道值如何真出现在语料**:渲染器 `_render_conflict_docs`(`pipeline/run_factory_v2.py`)读 `ws.conflicts`,用 prompts 的 `conflict.system`/`conflict.user` 另吐一篇**低可信来源文档**,与权威信号文档**同周并存** → 语料里真的出现矛盾(已接进 corpus 渲染主循环;非 L5 场景 `ws.conflicts` 为空 = 零副作用)。
- `prepare` **幂等**(已注入则不叠加,`--force` 重跑安全)。

## L5.3 gt = 裁决规则重算(v0 = source_reliability,代码可算)
- `gt()`:从 `ws.conflicts` 按 `(entity, field, session)` **重新查到**本题矛盾,按 `rule` 裁决——`source_reliability` → **信权威源** → `gt = authoritative_value`。
- **校验闸**:`gt() 从 ws.conflicts 重算 == enumerate 烘焙`(自检断言),护城河成立。
- **★序列化存活**:`conflicts` 随 `to_dict/from_dict` 往返(world_state)——必须如此,因为 corpus 阶段是从盘把 ws 读回来才能渲小道文档;自检验过"过序列化往返后 gt 仍对"。

## L5.4 出题 = 两值都不剧透(逼检索两边再裁决)
- `intent`:题面只说"有两份说法对不上,一份出自【权威源】、一份出自【小道源】,按**来源可靠度**裁决(正式记录 > 小道消息),只回最终认定值"。
- `hide` = 权威值 + 小道值**都**列入:两值都埋在语料里、题面一个都不给 → 逼记忆系统**真去检索两边**,再按来源定夺。

## L5.5 ★区分度 = M3 信号竞争的可证伪化
- 朴素 top-k 检索只取**更高频/更相似**的那条——往往正中**小道值**(因为传闻文档可能写得更"扎眼") → **答错**。
- 这正是把毕设 **M3(信号竞争)失败模式**做成**可证伪**的题:带**显式裁决规则 + code-gt**,只有会"检索两边、再按来源可靠度裁决"的系统才答得对。区分度由设计保证,不靠祈祷。

## L5.6 复用 vs 新建
- **复用**:`ProductionLine` 四件套、`_to_num/_norm`、防泄漏 `hide` 机制、`to_dict/from_dict` 框架(加一个字段)。
- **新建**:① `WorldState.conflicts` 侧信道 + 序列化;② `inject_conflicts`(挑字段 + 张冠李戴小道值 + 中段权威值);③ `gt()` 按 `source_reliability` 裁决 + 校验闸;④ `_render_conflict_docs` + `conflict.*` prompts(低可信文档,同周并存);⑤ 按来源可靠度的出题句式。

## L5.7 v1 路线(明确未落地,记此防重复造)
- **"标不确定"**:对**本无权威记录**之事(absent 字段)注入两条**互斥小道** → `gt = 无法确定`,考"知道自己不知道"(epistemic 边界);
- **"新近裁决"**:两份都权威、只是先后不同 → 取新——但这 **≈ L1.KU 已覆盖**,不在本线重复造。

## L5.8 出处
M3 信号竞争(毕设 𝓕𝓔𝓠×M1–M6 诊断框架)→ 可证伪化;裁决/可信度能力对标 §2 的 L5 坐标。
