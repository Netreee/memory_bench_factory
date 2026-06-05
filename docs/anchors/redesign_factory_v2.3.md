# Redesign · Benchmark 工厂 v2.3 —— 元架构 / 中央办公室 / 多产线 / 接地闸 / 世界生成 multi-agent / 规模与难度目标 / ★验证三角

> **定位**:把项目从「一个会换皮的**时间线** benchmark 生成器」升级为「一个能为**任意场景**实例化出**正交记忆挑战组合**的**元工厂**」。
> **要解决的根问题**:**场景区分度**。当前所有场景都走"造实体→按实体写文档"同一条认知骨架,换域=换皮,不同场景考的其实是**同一种**记忆(时间线回忆)。本设计让"不同场景考不同记忆能力"从**假设**变成**被设计、可测量**的事实。
> **来源**:对话推演(科层官僚隐喻)+ benchmark_landscape 调研(MEME/Memora/LongMemEval/LoCoMo/DataMorgana/YourBench/AutoBencher)+ 毕设 𝓕𝓔𝓠×M1–M6 诊断框架。
> **写法**:沿用 redesign 规范,关键环节写清 输入/谁/逻辑/输出/示例/出处。本文是**蓝图**,不是 as-built;实现按 §10 分阶段推进,预期长期调代码+prompt。
>
> **v2.1 增补**(2026-06-04):盲审 `blind_review_v3.md`(4 人独立)揭出根病——**世界自洽 ≠ 语料可答**:gold 源自状态机世界、却从没验它在**渲染出的语料**里成立,导致约 1/3 硬错(悬空/串部门/别名)。新增 **§G 接地闸(★命门3)**:把 orders(gold)与 corpus(语料)这两条**并列、本不交汇**的分支**汇合**做交叉校验,不接地的 gold **不出厂**。配套微调:§1 数据流(画出汇合点)、§6 产线契约(+第 5 件套 `ground()`)、§7.4(渲染期弱闸降级为"尽力而为",§G 为权威判定)。
>
> **v2.3 增补**(2026-06-04):① **§S 规模与难度目标**(TargetSpec 多维目标 + `#questions` 一等 floor + 难度分布 + 白皮书=被执行的合同);② **§V 验证三角(★论文核心框架)**:好题 = (题面, gold, 语料) 三边一致,**(A)良定义 /(B)接地=§G /(C)可答性** 各需一道独立闸——run0604 实证 **(B) 替不了 (A)**。
> **★一组纲领**:§V(三边验证)+ §S(白皮书=被执行的合同)+ `audit_patches.md`(单一域真相源:域值只从白皮书/世界来、代码不私藏)= 同一个"让 benchmark 质量【可证伪】"的纲领的三面。

---

## §0 一句话理念

> **LLM 负责"好看"(真实、场景化的语料),代码负责"正确"(可证伪的答案);而"考什么"由一个固定纲领 + 场景化实例化共同决定。**

三层价值主张,逐层递进:
1. **生成器泛化**:喂「场景描述 + few-shot」就出 benchmark,人不碰 prompt(域无关化在此自然达成,旧 #42 被吸收)。
2. **正交覆盖**:一套场景**激活不同产线组合** → 覆盖正交的记忆挑战 → 真区分度。
3. **诊断仪**:每题带"考哪种记忆/该触发哪种失败"标签 → 跨系统测出"谁缺哪种能力"。

---

## §1 总架构:宪法 → 中央 → 产线 → 打包

> **★路径注**:§1–§9 正文出现的 `factory/…` 路径是早期命名,**真实代码全在 `pipeline/`**(`pipeline/central_office.py` / `pipeline/lines/L*.py` / `pipeline/world_state.py`;渲染内联在 `pipeline/run_factory_v2.py`,无独立 `render/` 目录)。§L2/§L3/§L5/§G 等 as-built 章已用真实路径。

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
        │ ★世界喂出【两条并列分支】——产线(gold)与 渲染器(语料)各读世界、彼此不照面(这正是 bug 根源,见 §G.2)
        ├──────────────────────────────────────────────┐
        ▼ 每产线读自己那片(命门2:各自带代码gt)         ▼ 媒介渲染器(只读世界、★不读订单)
   ┌────┬────┬────┬────┬────┐ 产线                 场景化语料(文档/对话/交易流 + 草堆)
   │时间│关系│过程│偏好│冲突│ …                            │
   └────┴────┴────┴────┴────┘                              │
        │ (题, 代码gt) = gold 分支                          │ = 语料 分支
        └──────────────────────┬─────────────────────────────┘
                              ▼  (★命门3 接地闸:每题 gold 必须在语料里【逐字+就近归属】可验,否则弃题)
                       接地题库(出厂即对语料成立)
                              │
                              ▼  (完整性批判 + 真系统×场景区分度测量)
                       交付:benchmark
```

---

## §2 纲领(元架构):记忆能力坐标系 —— 固定,人写死

这是"宪法",定义"一个完整的 memory benchmark 该考哪些维度"。每个坐标 = **一条产线** = 一类记忆挑战 + 一种**代码 gt 基质**。综合 LongMemEval / LoCoMo / Memora / 毕设:

| # | 产线 | 考什么记忆 | gt 基质(代码) | 现状 |
|---|---|---|---|---|
| L1 | **时间线** | 时点回忆/最新值/何时变/聚合(max/min)/时长/拒答 | 状态机 + state-diff 切片 | ✅ as-built(自检 28/28) |
| L2 | **关系/多跳** | A管B、B依赖C,跨实体 N 跳推理与聚合 | 软外键 over 状态机 + 时序图遍历 | ✅ as-built(`L2_relational`,自检 11/11) |
| L3 | **过程/事件序列** | 动作序列、因果链、"先后" | 状态机变更事件流 + `gt_event_order` | ✅ as-built(`L3_process`,自检 11/11) |
| L4 | **偏好/隐式** | 从散落行为推断稳定偏好/画像(从不明说) | 潜偏好变量 + 行为发射 | ❌ 缺(未建) |
| L5 | **冲突/可信度** | 矛盾检测、按新近/来源可靠度裁决、标注不确定 | 跨来源矛盾侧信道 + 裁决规则 | ✅ as-built(`L5_conflict` v0=source_reliability,自检 20/20) |
| L6 | **边界/拒答** | 知道记忆的边界,不存在就拒答 | absent 集 + 越界探针 | ⚠️ ABS 探针已由 **L1** 产出(`capability="ABS"`);独立拒答线未建(PLANNED) |
| L7 | **巩固/摘要** | 长跨度要点/趋势回忆(非逐字) | 区间聚合/趋势函数 | ❌ 缺(未建) |

> 与毕设对齐:每条产线的题再叠加 **𝓕𝓔𝓠 算子 × M1–M6 失败模式**标签(§8 诊断层),L1–L7 是"考什么"、M1–M6 是"会怎么错"。两者正交,共同构成诊断坐标。

**纲领的不变量(写死)**:
- 每条产线**必须**提供 `(orders, code_gt, gates)` 三件套(§6 模板);没有代码 gt 的产线不准入厂。
- 所有产线**共用同一个世界基质**(§5),不得各造各的(防自相矛盾)。

---

## §3 中央办公室(Agentic Planner)—— 场景 → 白皮书

- **输入**:场景描述(自然语言)+ few-shot 文档(该领域真实样例,**不含 QA**)
- **谁(as-built)**:**6 视角并行议会**(观测/怀疑/映射/媒介/文风/陷阱,`config.pmap` 并发)→ **综合 = 代码确定性装配** `_assemble_whitepaper`(把 6 份结构化视角拼成白皮书,治"大 prompt 吐空")→ **单轮 LLM 批判润色**(失败则回落代码草案,白皮书永远有效)。**★不是"多轮提案→批判→修订"回炉**(那是早期设计,已被并行议会取代;`SYNTH_SYS` 综合 prompt 已删)。
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
>
> **★as-built 注**:`_assemble_whitepaper` 当前只产 `shared_world_spec` 的 **`entities / timeline / relations`** 三块。上面 schema 里的 `event_streams`(L3 从状态机变更 op **免费导出**)、`contradictions`(L5 由 `inject_conflicts` 在 world 阶段注入 `ws.conflicts` **侧信道**)、`latent_preferences`(L4 未建)**都不走白皮书**——schema 是蓝图全集,实产是其子集。

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
> ⚠ **两个开放问题**(见 §11.6 / §11.7):① 世界体量是否随目标 token 伸缩(现状:不随)② 世界生成应否 multi-agent 化(用户:提上日程,类比中央办公室白皮书闭环)。

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

> **★命门2 必要但不充分 → 命门3 接地**:`gt()` 只保证 gold 对**世界(状态机)**成立。盲审证明 gold 还**必须对渲染出的语料成立**,否则在语料里悬空/串部门/别名(详见 §G.1)。故产线契约新增**第 5 件套** `ground(order, evidence_docs)`(基类给默认的"单值就近归属"判定,各线按自己的 gold 语义覆写),由 §G 的独立接地 stage 统一编排。**没有 `ground()` 通过的题不出厂。**

---

## §7 场景化媒介渲染器(把"一个世界"渲染成场景媒介)

- **输入**:SharedWorld + 白皮书 `medium` + 全部产线的 orders(知道哪些事实要被"埋进"语料且可答)
- **谁**:LLM 渲染 + 代码闸(grounding/防泄漏)
- **逻辑**:
  1. **按媒介渲染**:文档→周报/邮件;对话→多轮会话(像 LoCoMo);交易流→结构化记录+评论。**同一个世界,不同媒介外壳**。
  2. **mention-on-change 纪律推广**:每个事实只在其"发生"的那个时间点/那条消息里出现一次,之后不复述 → 逼跨期/跨消息回忆。
  3. **草堆**:领域匹配 filler 把信号淹没(全 LLM,每篇不同,见理念:频繁调用但每次有意义)。
  4. **gt 存活校验(渲染期·尽力而为)**:渲染当下查本信号块的值是否出现,缺则带 hint 重渲——**这是降低漂移的尽力而为闸,不是权威保证**。盲审证明它远不够:只查"值在不在",不查"归属对不对 / 别名 / 跨题",且排序题、聚合题的 gold 根本没进这道查。**权威接地判定 = §G 独立的 `grounding` stage**(渲染完成后、按每道题的 gold 逐字 + 就近归属重验,不过即弃)。
- **输出**:场景化语料(媒介对应形态)+ 题库
- **示例**:电商 → `交易流(SKU调价/补货记录)+ 买家评论(发射偏好)+ 运营周报(数值)+ 大促公告(草堆)`,而非千篇一律的"周报"。
- **出处**:`factory/render/*.py`(媒介各一个 renderer,共用防泄漏闸)

---

## §8 验证 + 诊断层(区分度从"假设"变"测量")

- **四闸(★as-built:v2 流水线尚未接入)**:设计上每产线复用 grounding(代码)/ 题答自洽(LLM)/ 硬判别器(无语料·单证据 baseline)/ judge_wf;但**当前 `run_factory_v2.STAGES` 无任何验证 stage**——只有渲染期一道**弱 gt 存活闸**(§7.4,已被盲审证伪);**grounding 闸 = §G,蓝图未落地**;题答自洽/硬判别器/judge_wf 是 v10 旧物、**未迁入 v2**;`ProductionLine` 也**还没有** `ground()` 方法。
- **完整性批判 Agent**:对照纲领,审"白皮书有没有漏掉该场景该考的坐标、产线配比有没有制造区分度"(as-built:在中央议会的**单轮 critic** 里,作用于**白皮书层**、非产线题层)。
- **诊断标签**:每题打 `line_tag(L1–L7) × failmode(M1–M6 𝓕𝓔𝓠)`。
- **★区分度测量(判决性实验)**:多个**结构不同**的记忆系统,看**排名是否翻转**:
  - **as-built**:`eval/multi_system.py` 已实现 A/B/C 三系统(SingleShot / FullContext / Iterative RAG)+ ranking-flip 判定,但**只做了单场景内 L1 vs L2 能力层翻转**;§8 设想的 **system×scenario 跨场景**判决实验**尚未实现**(且工具名是 `eval/multi_system.py`,非下行的 `discrimination_eval.py`)。
  - 翻转 → 场景真有区分度(**铁证**,论文核心结果);不翻转 → 产线配比没拉开,回炉调白皮书。
- **出处**:`pipeline/run_factory_v2.py`(验证 stage 待建)+ `eval/multi_system.py`(区分度,as-built)

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
- **★as-built 勘误**:`tools/dependency_ablation.py`(消融图)+ eq.4 在线配比**仍零实现**(纯路线图)。但 `pipeline/lines/__init__.dependency_graph()` / `feasible_lines()` 已落地一个**目标不同的弱化版**——"产线→世界基质需求"图(L1 无依赖=根;L2 需≥2 person 字段;L3 需多事件时间线;L5 需文本字段),服务**激活可行性判断 + 论文结构图**,**不是** Skill-it 的训练混合消融图,勿混淆。
- **出处**:Skill-it!(NeurIPS 2023 Spotlight, **arxiv 2307.14430**;**本地 `refs/Skill-it/`**)。

### 9.5 判分 → **FAMA 遗忘指标 + online 流式协议**(Memora + LifeDialBench)
- **FAMA**:FORGET/L4 判分 = `max(0, MPA − λ·(1−FAA))`,MPA=该召回的召回率,FAA=该排除的过时值排净率(拆双分,比单准确率更暴露"误用过时值")。**online 协议**:评测时**冻结记忆态 M_t、按时间因果流式答题**(治"未来上下文污染")。
- **怎么干**:产线判分函数加 FAMA;`tools/v10_eval_memsys.py` / Stage G 加流式冻结 M_t。
- **出处**:Memora/From Recall to Forgetting(**arxiv 2604.20006**;**本地 `refs/Memora/`**)+ LifeDialBench(**2604.11182**,github `RayNeo-AI-2025/LifeDialBench`)。

### 9.6 有效性 → **多样性量化(回应"多样性"最在意)**(DataMorgana + InfoSynth)
- **改**:语料发布前算 **NDG(n-gram 多样性)/4-gram 自重复率/gzip 压缩比/句向量平均余弦 HS**(DataMorgana)+ **KL 散度新颖度 / 微分熵多样性**(InfoSynth),带 bootstrap p 值,**用数据证明语料不同质 + 不同场景产线区分度**(把"多样性"从印象变带 p 值的硬指标)。
- **★已落地(as-built)**:`tools/diversity_metrics.py` 已实现 **NDG / IDO(跨文档重叠)/ gzip 压缩比 + bootstrap CI**(+ 跨场景 JS 散度),自检 5/5;并**已接进生成主回路** `pipeline/run_factory_v2.stage_corpus`(渲染完调 `diversity_report`,结果写进 `manifest.algo.diversity`,每 run 自动出指标)。office_v3 实测 NDG=1.84 / IDO=0.13 / CR=2.34。**待补**:语义层 HS(需句向量 `embed_fn`,主回路暂未传)。(文档原写"Stage G"是 v10 旧命名,v2 接入点是 `stage_corpus`。)
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

> **as-built 进度(2026-06):P0–P2 ✅、P3/P4/P5 部分**(逐行标注)。

- **P0 ✅**:L1 时间线产线 + 代码 gt + 渲染。**降级归位**为产线之一。
- **P1 ✅**:纲领(L1–L7 坐标)+ 白皮书 schema + 中央办公室(**并行议会**,见 §3)。← 域无关化已达成。
- **P2 ✅(超额)**:不止 L2——**L1/L2/L3/L5 四条线都已建成**(各自自检绿);"多产线共享一个世界、一起渲染、零矛盾"端到端已通(medical run 激活 6 线)。← 架构已证成立。
- **P3 半**:文档媒介 ✅ + 渲染期弱存活闸 ✅;**对话/交易流媒介未做;权威 gt 存活校验 = §G 接地闸,蓝图未落地**。
- **P4 半**:L3 过程 ✅ / L5 冲突 ✅(v0);**L4 偏好 / L7 摘要 未建**。
- **P5 半**:多样性指标已接(§9.6);**区分度判决实验只做了单场景 L1/L2 翻转**(`eval/multi_system.py`),**跨场景未做**。← 论文核心结果待补。

> 每条新产线的"准入清单":① 世界子结构 schema;② 代码 gt 函数(可单测);③ 四闸适配;④ 至少一个媒介能承载其 gt。四样齐了才算一条产线。

---

## §11 硬骨头与风险(诚实)

1. **软挑战 gt(L4/L5)最难**:靠"潜变量+发射",但要保证**证据足以唯一反推**潜变量(发射太弱→不可答,太强→泄漏)。需专门调"发射强度"旋钮 + 可答性闸。
2. **共享世界一致性**:产线越多越难保证零矛盾,需强 `world.validate()`(代码)。
3. **成本**:agentic 多轮 + 多产线 + 多 Agent,贵。已定调"为质量不怕频繁调 LLM,但每次调用要有意义、不重复"。
4. **验证规模化**:四闸 × 多产线 × 大题量 = 大量 LLM;需采样 + 完整性批判兜底。
5. **媒介↔gt 耦合**:某些 gt 在某些媒介里难自然承载(如"时长"在对话里);白皮书选媒介时要校验承载力。
6. **★世界体量不随目标伸缩(开放,待算法 session 决)**:`target_tokens` 目前**只进 `render_corpus` 放大 filler**;世界规模——`n_entities = 2×关系+6`、`n_sessions` **硬编码 10**(注:§4 白皮书 schema 示例写 104,与 as-built 不符)——由议会按场景定,**与目标体量无关**。
   - **后果**:要 100M = "~10 实体 × 10 周的小世界 + 99.97% 噪声 filler"(信号被 `实体×周` 钉死在 ~0.1M),既退化又跑不动(~2 万次 filler 调用)。
   - **一致性不受影响**:单状态机 + 代码 gt + 本周快照渲染,filler 被 blocklist 挡在真相源外 → 崩的是**信号密度**,不是一致性。
   - **待决方向**:① 大 haystack 小 needle(现状)/ ② 大 world(`target` → 推 `n_sessions`=一年 / `n_entities` / `change_density`)/ ③ 按比例一起涨。
   - **工程落点**:`build_world` 前加"按目标体量推世界规模"的函数 + 让 `target_tokens` 流进 whitepaper/world。
7. **★世界生成应引入 multi-agent(用户判断,提上日程)**:当前世界生成是 **one-shot 零迭代**(独立批次生实体、按名去重;`assemble_world` 算出的缺陷只 log 就扔;白皮书 `traps/change_density/style` 被 build_world 无视)。对比中央办公室有"并行议会→装配→批判"闭环,**世界这步没有**——不对称。
   - **定调**:世界生成也要引入更多 multi-agent 设计理念(类似中央办公室的白皮书闭环)。
   - **认识**:gt 由代码兜底 → 不为对错迭代,要迭代的是**质量/丰富度/coherence/陷阱落地**。
   - **三档(供算法 session 选)**:L0 现状;**L1(最划算)** code-critique 修复轮(确定性缺陷定点重生成 + 真正消费白皮书 traps/density);**L2(大世界才值)** architect 搭骨架→populate→critic 迭代。耦合 §11.6:小世界 L1 够,大世界 L2 近必需。
   - 多智能体范式调研 → `docs/anchors/research_multiagent_paradigms.md`;**★改造详设见 §W**。
8. **★规模/目标多维化 + 难度分布 + 白皮书要被执行(详设见 §S)**:唯一旋钮 `target_tokens` 只管 haystack;**#questions 是供给受限的副产物、加 token≠加题**;且议会算的 weight/规模/难度**执行端不读**(白皮书 = 没被执行的愿望清单)。判断:target 应升为**多维 spec**(#questions 一等 floor + 配额 + 难度分布),白皮书目标**必须被执行**。难度调研:`docs/anchors/research_difficulty_distribution.md`。

---

## §保留(V10/V11 已对的,直接进 v2)

状态机 + state-diff 机械 gt(L1 基质)、mention-on-change 防剧透(推广到所有媒介)、**真系统评测**(EmbedMemory dense 检索,已在 `eval/multi_system.py`)、非单调 ShapeSpec、ORDER/Kendall-τ(**已升级为 `L3_process` 独立线**)、PREEXPIRE、json_repair 兜底、断点续跑 + 防呆闸 + 显式分段超时(XL 工程经验)。

> **★勘误**:此前把"四闸 / FAMA / 点二列区分度 / Stage G"列为"V11 已对、直接进 v2"——实际**均未迁入 v2**(属 v10 旧设计,见 §8 as-built 说明)。v2 现存的验证只有渲染期弱存活闸 + `eval/multi_system.py` 的三系统区分度。

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

---

# §G 接地闸详设 —— ★命门3:gold 对语料成立(为什么·是什么·怎么判)

> **状态**:v2.1 新增的**核心设计**(蓝图,未落地)。来源 = `blind_review_v3.md`(4 独立盲审收敛)+ 第一手语料核验 + 多系统评测二次坐实。
> **一句话**:命门2 保证 gt 对**世界**成立;命门3 保证 gt 对**渲染出的语料**成立。前者必要,但**远不充分**——本章补上缺的那一半。
> **定位(见 §V 验证三角)**:本章 = 验证三角的 **(B) gold↔语料【接地】边**;另两边 (A) 题面↔gold【良定义】、(C) 题面↔语料【可答性】见 §V。

## G.1 病灶:世界自洽 ≠ 语料可答(根发现)

旧验证用 `gt_multihop` 把 gold 与**状态机世界**比 → 8/8 一致(命门2 自洽)。盲审把 gold 与**渲染出的语料**比 → 约 1/3 硬错。**病根 = 世界生了两个独立的孩子**:`orders`(gold)与 `corpus`(语料)都从同一个 world 各长各的、**全程不照面**,LLM 渲染一跑偏,源自世界的 gold 就在语料里失真。三类系统性错(office_v3 第一手已核):

| 类 | 性质 | office_v3 实证(已核) |
|---|---|---|
| ① **悬空/幽灵** | gold 值**压根没渲进语料** = 渲染漏写 | Q25/26 排序题 gold 引用"尤娜/沈婷",全 108k 字符语料精确检索 **0 次** |
| ② **串部门/错归属** | 值渲了但**挂错实体**,gold 取了邻居的值 | Q19「基础架构部 P0 max」gold=0.14,语料里它最高 0.13;0.14 是数据安全部的值 |
| ③ **别名/表示不匹配** | gold 用世界本体(头衔),语料用渲染别名(人名),**渲染其实忠实** | 钟明语料里只 →刘总/CPO、**从不→CEO**;gold=CEO 的题,读对语料的系统反被判错 |

> **二次独立坐实**:多系统评测里 IterativeRAG **把桥实体全抽对**(钟明/韩涛/…)、答出语料真名,却因 gold 要的是没渲染的头衔 → L2 判 0%。两套方法(人读盲审 + 机器判分)收敛同一结论:**语料是真金、gold 没对上语料**。

## G.2 定位:为什么必须是独立 stage(而非塞进现有 stage)

看 §1 数据流:`orders(gold)` 与 `corpus(语料)` 是 world 的两条**并列分支、全程不交汇**——bug 正生于此。接地闸的本质 = **把这两支汇合做交叉校验**,它必须同时拿到 gold 和语料 → **只能跑在两支之后**。而当前 DAG 里"两支之后"是**空的**(corpus、questions 都是叶子)。所以它在拓扑上就该是一个**新节点 `grounding`,依赖 `[questions, corpus]`**——是**补 DAG 的洞**,不是加补丁。

反证"塞进现有 stage":塞 corpus / questions 都得引入反向跨依赖(两支不再并列、DAG 变脏),且"在渲染里查值"正是 §7.4 那道被盲审证伪的弱闸;塞进 orders 在**时序上不可能**(语料还没渲出来)。→ 独立 stage 是唯一架构正确解。

> **工厂 vs 旁挂脚本**:可以写事后脚本查(像评测 harness),但那样"接地"只是**碰巧检查了**。做成 stage,工厂的**正式产物天生接地**。对一个"产出可信 benchmark"的元工厂,这道闸**必须在工厂里、不能在工厂旁**——这是 thesis 成不成立的分界。

## G.3 输入 / 谁 / 输出

- **输入**:`04_questions.json`(短语化题 + 代码 gt + `evidence_sessions` + `aux`)+ `05_corpus.json`。
  - **★编号以当前 `run_factory_v2.ART` 为准**:`03_orders / 04_questions / 05_corpus / 06_grounded_questions`。§G.1 引用的 `output/factory_v2_office_v3/` 是**过时产物**(老编号:06=questions、无 04_questions、corpus 无 conflict 文档),只作**病灶实证**、不是本闸输入样例。
  - **★corpus 真实结构外层包了一层** `["corpus"]`:`{"corpus":{"sessions":[{session_id,date,docs:[{doc_id,content,is_filler,fact_refs,(is_conflict)}]}]}, "done_weeks":[…]}`。读法 = `read("05_corpus.json")["corpus"]["sessions"]`(直接 `["sessions"]` 会 KeyError)。
- **谁**:**纯代码**——零 LLM、零网络、确定性、可复跑。stage 编排 + 每条线 `ground()` 判定(同 `enumerate/gt` 的"stage 编排、线实现"模式)。
- **输出**:`06_grounded_questions.json`(**接地通过的子集** = 出厂题库)+ `manifest.algo.grounding`(存活率统计)+ `grounding_report`(逐题弃因,人读)。
- **DAG**:`Stage("grounding", needs=["questions","corpus"], …)`;幂等(同输入同输出,`--only grounding --force` 可单独重跑)。**下游(评测)改读 `06_grounded`,不再读原始题库。**

## G.4 判定口径:接地 = gold 在证据文档里【逐字 + 就近归属】可验

定义三件事,消歧到底:

1. **证据文档**:一道题的证据文档 = 它 `evidence_sessions` 里的**信号文档**(`doc_id` 含 `_sig_`、非 `is_filler`)。L5 额外含该 session 的**矛盾文档**(`is_conflict`)。**草堆 filler 不算证据**(草堆本就不该承载被考事实)。
2. **逐字**:gold 值经 `_norm`(去空格 / 全角→半角 / 去尾 %,与判分同口径)后,作为子串出现在证据文档正文。
3. **就近归属**(治②串部门):gold 值与**锚名**必须在**同一句**(按 `。!?\n` 切)**或 ±50 字符窗口**内共现——只"值出现在某篇"不够(多部门周报里 0.14 和"基础架构部"会同篇但不同句,光同篇会放过串部门)。

→ 共用判定原语:`attributed(value, anchor, docs, window=50) -> bool`(value 与 anchor 在某证据文档同句 / 邻窗共现)。各线 `ground()` 用它拼自己的判定。**anchor 默认 = 题面实体名;L2 用桥实体;L5 用实体 + 来源。**

> **命中语义 = any**:候选证据文档里**任一篇**满足 `attributed` 即判 grounded——mention-on-change 下一个值只在其变更周出现一次,**不要求每篇都命中**(写成 all 会把 IE/KU 几乎全 drop)。

> **★v0 判定有【两个】误差方向(此处纠正初稿"只会漏放不会误杀"的错误论断)**:就近子串匹配只是"接地"的**必要非充分**条件,两头都会错——
> - **(a) 不命中 → drop**:可能**误杀**真接地的题(值与实体同篇却隔得 > 窗口,如实体在表头、值在远处表行);
> - **(b) 命中 → 保留**:可能**误放**没真接地的题(巧合共现)——尤其**纯数值**(`_norm("8")` 是 `_norm("8人")` 的子串)和**头衔**(实测语料里 CPO/CFO/CTO 各出现 25–28 次、极密,某些 L2 题"头衔落在桥实体 ±50 内"会假命中)。
>
> v0 取"从松"(命中即留,优先别误杀好题),**代价:存活率是【上界/偏乐观】、非精确值**——尤其 L2/聚合这类高密度值能力会虚高。所以读数法:**低存活率 = 铁定有问题(可信);高存活率 = 仍需 v1 复核**。**v1 收紧**:高密度值(数值/头衔)要求**同时近字段名 + 落在该实体的同句/表项**,把"巧合共现"挤掉,让存活率从上界收敛到真值。

## G.5 每条线的 `ground()` 契约(线自己懂自己的 gold 怎么接地)

`ProductionLine` 加**第 5 件套**:`ground(self, order, evidence_docs) -> (status, detail)`,`status ∈ {grounded, drop}`(v0)。基类给**默认实现** = `attributed(gold, entity, docs)`;各线按自己的 gold 语义覆写:

| 线 / 能力 | `ground()` 判定(全用 `attributed`) | 抓哪类错 |
|---|---|---|
| L1 IE/KU/PREEXPIRE | gold 值就近归属于**实体**,在证据文档内 | ①③ |
| L1 MR(聚合) | 极值就近归属于实体,**且锚到 gt 标注的那一 session 的文档** | ②(串部门) |
| L1 TR(何时变) | 变更后的**新值**就近归属于实体,在**变更 session** 文档内 | ②③ |
| L1 ABS / L6 拒答 | **反向判**:实体 + 该字段在全语料**从不就近共现** = 缺失为真 → grounded;一旦共现 = 这题不成立 → drop | gold=INSUFFICIENT 的真伪 |
| L2 多跳 | 末值(答案)就近归属于 **`aux.bridge`(桥实体)** | ①③(别名悬空) |
| L3 过程序列 | **gt 里每个事件**的值都须就近归属于实体、在**各自 session** 文档内;**有一个不接地 → 整题 drop** | ①(幽灵事件) |
| L5 冲突可信 | 权威值就近归属实体(信号文档)**且** 小道值现于矛盾文档 —— **两边都接地**才算真有冲突 | 渲染漏写任一边 |

> **结构判定类**(L1 FORGET「已停统」、L1.CONFLICT「前后是否一致」)gold 是个判断而非语料里的一个值:接地 = 验其**触发条件的语料痕迹**在(如 FORGET 验"自本期起停止…"这类停用声明就近于实体)。本表先覆盖有明确"值"的能力;结构判定类按同一原则逐条补,不留模糊。

## G.6 处置:v0 = 弃题(reconcile / 重渲 = v1,边界写死)

- **v0**:`drop` 的题**从 06 移除**,弃因进 `grounding_report`。得到**更小、但出厂即可信**的题库。盲审已判语料是 A-、坏的是 gold → 我们主要要**剪枝**,不是重造。
- **明确不做(留 v1,写在此防 review 追问)**:
  - **不重渲**:重渲是反馈环(grounding→corpus→grounding),会再漂、未必收敛、烧 LLM;v1 再做"把弃题的证据文档带 hint 重渲以挽回"。
  - **不语义 reconcile**:别名类(CEO↔刘总)的真 reconcile 需要"头衔↔人名"映射表,世界里没有 → v0 直接 drop。**别名类治本在上游(见 G.8),不在闸。**
  - **不跨文档拼接**:v0 只在**单篇证据文档内**判就近共现;"值在 A 文、实体在 B 文"不算接地(那种弱关联本就该弃)。

## G.7 产物与【存活率】指标:质量闸 + 论文证据 + 指哪修

接地闸的报告 = **gold 存活率**(总 / 按线 / 按能力):`存活率 = grounded 数 / 该组总数`。一物三用:
1. **出厂质量闸**:存活率 = 这批 benchmark 有多少题可信。可设阈值(如某线 < 60% → 该 run 标"gold 质量不达标,勿直接打分")。
2. **论文硬证据**:正是盲审说"缺了的那个证明"——量化证明"gold 与语料一致"。审稿人一句"你怎么知道 gold 对得上语料",被它直接挡掉。
3. **指向上游修哪**:某能力存活率异常低 = 上游(世界 / 渲染 / 出题)有系统 bug。如 office_v3 跑这闸,预期 L2 存活率很低 → 指向"汇报对象用头衔不用人名"这个上游根因。

## G.8 闸是探测器 + 底线保证,治本在上游(office_v3 三类的去向)

接地闸**保证**不接地的 gold 绝不出厂(剪掉),但**不替上游治病**。三类错的治本去向:

| 类 | 闸的动作 | 治本(上游) |
|---|---|---|
| ③ 别名(汇报对象=头衔) | drop | **世界生成**:`汇报对象` 等关系字段的值建模成**真实人员实体名**(非 CEO/CPO 头衔)→ 渲染自然写人名、gold 也是人名 → **别名问题从源头消失**(本也更符合"汇报对象是个人")。修完 L2 存活率应大涨。 |
| ② 串部门(MR/末值) | drop | **出题/渲染**:查 `order_gen` 极值/末值是否从错实体取值;渲染按实体分块、防跨部门串味。 |
| ① 幽灵(L3 事件) | drop | **渲染/出题**:L3 出题前预筛"被选中事件的值必须能渲进对应 session"。 |

> **闸 + 上游修 = 组合拳**:闸给**底线**(绝不发坏题)+ **健康信号**(存活率);上游修让存活率从"剪掉一大半"回到"绝大多数留得住"。**v0 先上闸**(立刻止血、不再发坏题),上游修按存活率报告逐个跟进。

## G.9 v0 边界一览(读到这里不该再有问号)

- **纯代码**:零 LLM、零网络、确定性、可复跑、秒级。
- **判定**:单篇证据文档内、`_norm` 后逐字子串 + 就近(同句 / ±50 字符)归属。
- **处置**:只 `drop`,不 reconcile、不重渲、不跨文档拼接。
- **锚**:默认实体名;L2 桥实体;L5 实体 + 来源;聚合题加 session 锚。
- **产物**:`06_grounded_questions`(出厂题库)+ 存活率(进 manifest)+ 弃因报告。
- **不负责**:上游 bug 的修复(它只探测 + 剪枝 + 报数);别名 reconcile / 重渲挽回(v1)。

## G.10 复用 vs 新建

- **复用**:`_norm`(判分同口径)、corpus 的 doc 结构(`doc_id/is_filler/is_conflict`)、`evidence_sessions`、Run/Stage 基建(幂等/留痕/`--only`)、各线已有的 `entity/field/aux/bridge`。
- **新建**:① `Stage("grounding", needs=[questions,corpus])` + stage 函数;② `attributed(value, anchor, docs, window)` 判定原语;③ `ProductionLine.ground()`(基类默认 + L1各cap/L2/L3/L5/L6 覆写);④ 存活率统计 + `grounding_report`;⑤ 下游评测改读 `06_grounded`。

## G.11 出处

`docs/anchors/blind_review_v3.md`(4 独立盲审 + 第一手 `reviewed_corpus/qa`)= 病灶来源。判定 / 处置思路借鉴:**KGQA 别名集**(gold = 实体 + 全部表面形式,启发 v1 reconcile)+ **验证类合成 pipeline 的可答性闸**(用 reader / 串匹配验答案可答,不过即弃)+ **span-grounded benchmark**(SQuAD/HotpotQA:gold = 语料 span、构造上即接地)。我们因"用状态机世界当真值源 + 又单独渲染真实文本"而独有 world≠corpus gap;接地闸正是**付这个代价、同时保住"真实文本 × 代码可验复杂 gold × 接地"三者**的机制。

## G.12 实现细则:钉死盲读暴露的歧义(读到这条之前的每个"问号"在此闭合)

> 一名从零接手者盲读 §G + 核对真实代码/产物后提的疑问,逐条钉死。**动手写 `ground()` 前先读完本节。**

**G.12.1 gt → 待验标量(★最关键:gold 多是 dict、不是一个值)。** `ground()` 第一步永远是"从 gt 抽出要拿去语料里找的那个标量"。映射表(按 capability,均已对 `06_questions.json` 核形态):

| capability | gt 形态 | 待验标量 = | 锚 session |
|---|---|---|---|
| KU / PREEXPIRE / L2_multihop / L5(权威值) | 标量 | gt 本身 | evidence_sessions |
| IE | `{"value","at_week"}` | `gt["value"]` | evidence_sessions |
| MR | `{"value","session","date","agg"}` | `gt["value"]` | **`gt["session"]`** |
| TR | `{"session","date","from","to",…}` | `gt["to"]`(变更后新值) | **`gt["session"]`** |
| L3_order | `list[event]`(`event={field,value,session,…}`) | **逐个** `event["value"]`,**全部接地才留** | 各 `event["session"]` |
| ABS | 哨兵 `"INSUFFICIENT_EVIDENCE"` | 不验值 → 见 G.12.3 | 全语料 |
| FORGET / CONFLICT | dict(`forgotten`/`conflict`) | 见 G.12.4 | evidence_sessions |

**G.12.2 证据文档集怎么取(消 A1+A6 的口径冲突)。** 三步:① corpus 读 `["corpus"]["sessions"]`(外层那一层);② 默认证据文档 = `evidence_sessions` 里的**信号文档**(`doc_id` 含 `_sig_`),L5 再加 `is_conflict` 文档——**★`is_filler` 是三态**:信号文档该键 `=None`、草堆 `=True`,筛信号必须用 `not doc.get("is_filler")` 或靠 `_sig_` 命名,**切勿写 `== False`(会筛出 0 篇 → 全题误判无证据、静默全 drop)**;③ **聚合/变更/排序类再收窄**:MR/TR 收窄到 `gt["session"]`、L3 每个 event 收窄到 `event["session"]` 的那周文档。口径关系=`evidence_sessions` 是"该读齐的周",`gt.session` 是"答案落点周";**先按前者取候选、再按后者收窄**,不冲突。

**G.12.3 ABS/拒答的接地(消 A3:ABS 订单 `entity=""`、`evidence_sessions=[]`)。** ABS 不验"实体+字段共现"(它本就没实体),改验**字段全局缺失**:扫**全语料**信号文档——`field`(如"季度营收")**从不出现** → 缺失为真 → grounded;一旦出现 → 该字段其实存在 → 这道拒答题不成立 → **drop**。无需实体、无需就近。**★拒答能力是 `line="L1_timeline", capability="ABS"`,不是独立 L6**(L6 在 PLANNED 未落地);§G 凡写"L6 拒答"均指此。

**G.12.4 结构判定类 + 未定义能力的 v0 默认 = fail-closed 弃题(消 B3+B8)。** 不变量"没 ground() 通过的题不出厂"靠**失败即弃**兜底:
- **任何 capability 未显式实现 ground() 判定 → v0 默认 `drop`**(宁可少发、不发不可信)。这保证不变量对**未来线**也成立。
- FORGET(其 order 只有 `evidence_sessions`、**无显式 expire 周字段**):grounded iff 实体 + **停用标记**在**任一证据文档**就近共现(停用标记 = 固定集 `{停, 停止, 不再, 下架, 出院, 结案}` ∪ 白皮书 `stopped_phrase`)。
- L1.CONFLICT(`gt["values"]` 实为 **`[[session, value], …]` 的「(周, 值)对」列表**,非一维值列表):grounded iff 各 `pair[1]`(值)都就近归属实体、且其 `pair[0]`(周)**分落 ≥2 周**(矛盾两端都真渲了)。
- 不满足 → drop。

**G.12.5 非逐字 gold 的线(L4 偏好 / L7 摘要)必须覆写,否则被默认闸全杀(消 B8)。** L4 的 gold 是潜偏好("高性价比")、L7 是趋势/区间聚合——**本就不该在语料逐字出现**,基类默认的"逐字子串"会把它们**全 drop**。故契约:**gold 非逐字型的线,必须把 `ground()` 覆写成对应的结构判定**(L4 验"足以反推该偏好的那些发射行为是否都渲了";L7 验"构成趋势的那串区间值是否都在")。未覆写 = fail-closed drop(G.12.4)。§G.5 表只列 L1/L2/L3/L5,正因 L4/L7 未落地;接入时"覆写 ground()"是其准入清单一项。

**G.12.6 为何不信现成的 `fact_refs`(消 B7)。** 每篇信号文档带 `fact_refs`(声明"我渲了哪些 实体.字段")。**绝不能拿它当接地依据**:`fact_refs` 是渲染器的**自我声称**,而病灶②③正是"声称渲了、实际串部门/漏值/换别名"——信它等于给 bug 盖章。接地闸**只认正文文本**(`attributed` 跑在 `content` 上);`fact_refs` 至多当"先定位候选文档"的提速 hint,**判决永远落在文本**。

**G.12.7 验证现实:office_v3 只能干跑 L1/L2,L3/L5/ABS 要新 run(消 A7)。** office_v3 早于 L3/L5 拆分(排序题仍以 `capability="ORDER"` 挂在 `L1_timeline`,corpus 无 `is_conflict` 文档)。所以:**L1/L2 分支可立刻对 office_v3 干跑**(预期 L1 标量能力 KU/PREEXPIRE 多 grounded;L2 因头衔别名多 drop——但记 G.4 的**误放上界**,L2 存活率可能虚高);**L3/L5/ABS 分支须等一次激活 L3/L5 的新 run**(corpus 真有 conflict 文档、orders 真有 `L3_order`/`L5_conflict`)才能验。落地顺序:实现 → office_v3 干跑 L1/L2 → 新 run 全量验。

**G.12.8 落地连带(消 B4/B5/B6,按图拼装即可)。** ① `ART` 加 `"grounding":"06_grounded_questions.json"`,否则 `drive()` 依赖校验找不到产物名;`manifest.algo.grounding` 自行 `run.set_algo` 写。② 下游评测 `eval/multi_system.py` 的 `DEFAULT_BENCH/DEFAULT_CORPUS` 是**写死常量**(还指向老 office_v3 编号)、`SKIP_CAPABILITIES={"ORDER"}` 用旧 cap 名——改读 `06_grounded` 时这几处同步改(L3 拆出后排序题叫 `L3_order`)。③ L2 将来若出 comparison 题型(L2.4)则**无 `aux.bridge`**,其 `ground()` 锚改用"被比的两个实体";取 bridge 前先判存在(防 KeyError)。

---

# §W 世界生成详设 —— multi-agent 化(把 §5 的"填表"升级为"议会造世界")

> 把 §11.7 的判断 + `research_multiagent_paradigms.md` 的调研落成设计。
> **核心**:世界生成**照搬中央办公室已验证的骨架**(并行发散 → 单一收敛 → 有界批判),**不引自由对话框架**。

## W.0 病灶(为什么改)
- 现状 **one-shot 零迭代**:独立批次各从固定 prompt 生实体、按名去重;`assemble_world` 算出的缺陷(单调轨迹/伪 evolving/absent 矛盾)**只 log 就扔**;白皮书 `traps / change_density / style` 被 `build_world` **全程无视**。
- **不对称**:中央办公室有"并行议会→装配→批判"闭环,世界这步没有。
- **关键认识**:gt 由代码兜底 → **不为对错迭代**;要迭代的是**质量 / 丰富度 / coherence / 陷阱落地**。

## W.1 设计原则(从调研收敛,五条)
1. **发散用并行、收敛用单一权威**:提案是 read-like(可并行);最终世界是必须自洽的 write(单 architect 拍板,★**绝不让多 agent 投票**)。
2. **异质性 > agent 数量**:3–5 个**异质角色**(同质 agent ≈ 多采样);角色异质才是杠杆。
3. **别投票、别等共识**:裁决交**代码校验 + 单 architect**(LLM judge 会被自信的胡说带偏;共识常是趋同伪装)。
4. **多 agent 不保证多样,主动护**:verbalized sampling + 相似度惩罚剔冗余(防表征塌缩,调研 2604.03809)。
5. **准入门槛**:能用"单 architect + best-of-N 采样"廉价拿到的,**不上多智能体**。

## W.2 两档(L1 先行,L2 大世界才值)
| | **L1 修复轮** | **L2 hybrid 造世界** |
|---|---|---|
| 何时 | **默认,先做** | 走"大 world"体量方向(§11.6 ②)时 |
| 形态 | 现状并行生成 + CRITIC 精修轮 | architect → populate → critic |
| 成本 | 低(+1~3 轮、只修坏的) | 高 |
| ROI | ★**最高**(治"算了却丢弃") | 大世界才回本 |

## W.3 L1 详设:CRITIC 式修复轮(最高 ROI,先做)
**数据流**:`并行批次生成(现状)→ world.validate()【代码,产缺陷清单】→ critic-repair【LLM,只重生成坏字段/实体】→ 迭代 1–3 轮,直到无缺陷或到顶`。
- **`world.validate()` 产结构化缺陷信号**(已有 + 补):单调轨迹(MR 退化)、标 evolving 却单值、absent 矛盾、**change_density 不足**(白皮书要 4–8 次变更却没到)、**trap 未落地**(白皮书要"近重名"却没造)、跨实体撞名 / 字段值雷同(防塌缩)。
- **让 `build_world` 真正消费白皮书** `traps / change_density / style`(现在忽略)。
- **复用骨架**:Tracer / `config.pmap` / 全局信号量;批次生成 = 并行(发散),修复 = 有界单轮(收敛),与中央办公室**同构**。
- **依据**:Self-Refine / Reflexion / **CRITIC(用外部校验器而非自省)** —— 4 路调研一致点名为最高 ROI。
- **改动落点**:`build_world`(`pipeline/run_factory_v2.py`)+ 把 `assemble_world` 的 issues / `_shape_issues` 升级成 `world_state.validate()` 的结构化缺陷清单。

## W.4 L2 详设:StoryBox 式 hybrid(大世界才值)
**最接近的范本 = StoryBox(调研 2510.11618):top-down architect + bottom-up 按周 sim + retrieval 连贯 + critic;其"7 天 sim 甜点"正好对应我们按周演化。**
- **architect agent**:出**可改**的世界骨架——实体清单 + 关系图 + 每字段弧线形状 + trap/矛盾布局 + 时间跨度(由体量推,接 §11.6)。★骨架可修订,不是冻结 spec(纯 top-down 会压平)。
- **populate**:**异质专家角色**(按场景:地理/经济/人事/技术…)各自**独立、并行**填充实体轨迹(防趋同)。
- **coherence**:retrieval + 周摘要维持跨周连贯;agent 间**传结构化 world 产物(MetaGPT 哲学)非对话**。
- **critic**:验弧线 / 落陷阱 / 消矛盾(**程序化优先**,LLM 兜底)。
- **护多样性**:verbalized sampling(让 architect 显式给 N 候选分布)+ 相似度惩罚。

## W.5 与现有架构的接口(不破坏什么)
- **Stage 序列不变**:还是 `world` stage,只是**内部**从"批次循环"换成"议会子图"。
- **产物不变**:仍输出**一个 `WorldState`**(`02_world.json`)→ 下游产线 / 代码 gt / 渲染 / 接地闸 **零改动**。
- **基建全复用**:全局信号量 / Tracer / manifest / 监控面板照常。
- **L1 默认开,L2 按需**;两者都活在 world stage 内部,**对外透明**。

## W.6 准入门槛(别为多而多)
先实现"单 architect + best-of-N 采样"做对照;**只有多样性确为瓶颈、且 best-of-N 不够,才升 L2**。能多采样解决的不上多智能体。

## W.7 出处 / 依据
- **调研**:`docs/anchors/research_multiagent_paradigms.md`(4 路并行 subAgent,~30+ 篇引用)。
- **落地**:`build_world` + `world_state.validate()`;复用中央办公室"并行→装配→批判"骨架。
- **开放轴(算法 session 定)**:体量伸缩(§11.6)决定走不走 L2。

---

# §S 规模与目标 详设 —— 多维 target spec + 难度分布 + 白皮书必须被执行

> 把这一轮"规模如何影响 / 该有哪些旋钮 / 难度怎么控"的讨论落成设计。难度调研依据:`docs/anchors/research_difficulty_distribution.md`。
> **★可实现级算法详设(伪代码 + 反推率模型 + 双环 + stage 落点)见 `docs/anchors/closed_loop_targetspec_design.md`**(本节是蓝图、那份是算法)。
> **两条主线**:① **target 从"单一 corpus 体量"升为"多维目标 spec",`#questions` 是一等 floor**;② **白皮书 = 被执行的目标 spec**(议会定的 weight/规模/难度,执行端必须真落实——现状"算了就丢")。

## S.0 病灶
- **唯一旋钮 `target_tokens` 只管 haystack(filler),不管信号/题目**(§11.6 同根)。
- **`#questions` 是供给受限的副产物**:`世界结构 → 各线 enumerate(L1 用写死 _PLAN、L2/L3/L5 世界受限)→ 出题 drop 空 → 接地闸 drop 未接地`。**既不能指定、也不能预测;加 token ≠ 加题**。
- **白皮书的目标没被执行(审计)**:

| 维度 | 在白皮书? | 执行了吗? |
|---|---|---|
| corpus tokens | 在 config(非白皮书) | ✅ 灌 filler |
| **#questions** | 仅 `total_q`(写死 200) | ❌ 不绑定 |
| 实体数 | ✅ `entities.count` | ✅ build_world 读了,**但值=`2×关系+6`公式** |
| 时间跨度 | ✅ `n_sessions` | ✅ build_world 读了,**但值=`10`写死** |
| 每线 weight | ✅ 议会算 | ❌ **一行没读** |
| 每能力配额 | ❌(L1 `_PLAN`写死) | (写死) |
| traps / change_density | ✅ 议会算 | ⚠️ **W.3 刚开始读** |
| **难度分布** | ❌ | ❌ |

## S.1 目标多维化:TargetSpec(与 `target_tokens` 并列)
```
TargetSpec {
  min_questions: N              # ★一等 floor —— benchmark 的本体就是题
  per_line_min: {L2:.., L5:..}  # 平衡(别全是 KU);接白皮书 active_lines.weight
  corpus: tokens 或 needle:haystack 比   # haystack 轴,独立
  time_span: 周数 / "一年"       # 接 §11.6
  difficulty_distribution: {easy:.3, med:.5, hard:.2}   # ★见 S.3
}
```
两条**独立轴**:**信号轴**(world→orders→questions,由 `min_questions` + 配额 + 难度驱动)× **haystack 轴**(corpus tokens)。世界规模从信号轴**派生**,不再议会拍脑袋。
> ★ `#questions` 与 corpus **不正交**:都踩世界,`needle:haystack` 比把二者拴住 —— 一起设计,别各调各的。

## S.2 `#questions` 一等 floor:open-loop → closed-loop
因 `#questions = f(world)`,"保证 ≥N 题"必须:
1. **反推世界规模**(N → 需要多少 实体×周×激活线,按各线产出率估);
2. **过量供给**(按接地存活率 over-provision:要 N 个最终题,先点 ~N/survival 个 order);
3. **闭环兜底**(生成→数最终接地题→不够就长世界/多点菜→重试;复用 world 批次 top-up 的循环模式,抬到 benchmark 层)。

现状 **open-loop**(给 tokens、题数随缘)→ 目标 **closed-loop**(给题数 floor、管够为止)。

## S.3 难度分布:轴 × 度量 × 分布目标(详见 `research_difficulty_distribution.md`)
**业界三件套;我们轴最全且可证伪(护城河),但度量只有二元闸(LKP/MKO)、分布目标缺位。**
- ★**硬约束(非锦上添花)**:IRT 文献(PSN-IRT 2505.15055 / Easy2Hard 2409.18433)证 —— **极化谱(全易/全难)是 benchmark 头号病、难度极端处区分度必塌、平衡分布最大化区分度**。**区分度是我们论文核心结果 → 不管分布 = 直接风险敞口**。
- **三步落地(全部可证伪)**:
  1. **定义**:每线 `difficulty_score` = 各结构轴(hop数 / 是否中段 / 级联深度 / 干扰数 / session距离)加权和,**纯 gt 代码算**(超越 Evol-Instruct"祈祷变难");等分位切 easy/med/hard。
  2. **控制**:白皮书加 `difficulty_distribution` → `order_gen` **分层配额采样**(抄 DataMorgana 每档 `probability` + Skill-it 在线逼近缺额档);缺档用 §9.3 五算子代码版合成。
  3. **验证**:三系统(`eval/multi_system.py`)算 `Difficulty = 1 − max_acc`(AutoBencher 式),验"hard 档真的 acc 更低"(难升分降);可选 IRT 拟合 `b/a`(论文加分,反击"benchmark 极化"通病)。
- **与接地耦合**:难题更易掉接地 → 分布须在**接地存活后**统计(§G)。

## S.4 元主线:白皮书 = 被执行的目标 spec
S.0 审计暴露的统一病:**议会算了一堆目标(weight/规模/难度/traps),执行端不读**。修复 = 让执行端**真消费**白皮书:
- `build_world` 按 spec 规模(由 TargetSpec 反推)+ 消费 `traps/change_density`(W.3 已起步)。
- `run_lines`/`order_gen` 消费 `active_lines.weight` + `per_line_min` + `difficulty_distribution`(分层配额采样,取代 L1 写死的 `_PLAN`)。
- `#questions`/规模由 TargetSpec **反向驱动 + 闭环**(S.2)。
→ 白皮书从"愿望清单"变"**被执行的合同**"。

## S.5 与现有架构接口 / 落点
- **不破坏 Stage 序列 / 产物 / 接地闸**;改动在:`build_world`(规模反推)、`order_gen`+`run_lines`(配额+难度分层采样)、+ 一个 `TargetSpec` 入口(CLI/config → 白皮书 §4 schema 加 `min_questions / per_line_min / difficulty_distribution`)。
- **`difficulty_score()` 挂在产线**(每线自报,像 `gt`/`intent`);**分层采样在 `order_gen`**;**验证复用 `eval/multi_system.py` + `manifest.algo.difficulty`**(像 §9.6 多样性那样落盘)。
- 与 **§W 协同**:闭环反推世界规模 = architect 按 TargetSpec 搭骨架(L2 大世界);难度结构轴正是 architect 要布的料。

## S.6 出处 / 依据
- **难度调研**:`docs/anchors/research_difficulty_distribution.md`(§3.2 落地路径 / §4 可抄清单)。
- **接** §11.6(体量)/ §11.7·§W(世界 multi-agent)/ §G(接地,难度在存活后统计)/ §9.3·§9.4·§9.6(WizardLM 算子 / Skill-it 配比 / 多样性度量)。
- **落点**:`build_world` / `run_lines` / `order_gen` / `eval/multi_system.py` / 白皮书 §4 schema 加字段。

---

# §V 验证三角 —— 一道"好题"的三条边(★论文核心框架)

> **来源**:两轮独立盲审(`blind_review_v3.md` + `blind_review_run0604.md`)收敛出的统一视角——把"benchmark 质量"从一堆零散 bug 收成一个**可证伪的结构**。
> **一句话**:一道可发表的题 = `(题面 question, 标准答案 gold, 语料 corpus)` 三者两两一致;三条边各需一道【尽量代码可验】的闸。**少一条边题就坏,且坏法互不相同、互不替代。**

## V.1 三角与三条边

```
              题面 question
             /            \
       (A) 良定义        (C) 可答性
       well-posed        answerable
           /                  \
        gold ──(B) 接地 grounded── 语料 corpus
```

| 边 | 名称 | 保证什么 | 缺这条边的反例 | 状态 |
|---|---|---|---|---|
| **(A)** 题面↔gold | **良定义** well-posedness | gold 是题面所问的【唯一】解——题面点明 gold 赖以确定的**全部坐标**(时点 / 聚合 / 哪个具体实体…) | L2"X部负责人向谁汇报"没说第几周 → 逐周多个值都对 → gold 不唯一 → 不公平 | ❌ 待建(V.3) |
| **(B)** gold↔语料 | **接地** grounding | gold 真出现在语料、且归属到对的实体 | v3 幽灵实体(尤娜查无此人)/ 跨部门串值 | ✅ 已建(§G,命门3) |
| **(C)** 题面↔语料 | **可答性** answerable | 读语料能**唯一**推出 gold,且无语料基线推不出(非平凡) | gold/语料都没错,但问法让读者推不到、或太好猜 | ◐ 雏形(V.4) |

**与命门2 的关系**:命门2 保证 gt 对【世界】成立(gt = 代码从状态机算)。但"世界"不是交付物——交付物是 `(题面, gold, 语料)`。**命门2 让 gt【诞生得正确】;验证三角让这个 gt 作为一道【题】成立。** 串联 = 完整护城河。

## V.2 三条边正交,不能互相替代(核心论点)

每条边都管不了另两条——run0604 给了铁证:
- **(B)接地 ≠ (A)良定义**:那轮 L2 的 10 道题 **100% 接地**(CPO/CFO 都在语料里),却仍 **ill-posed**(题面无周锚)。**接地闸放行了"接地但题面没锁死 gold"的题**——它只查值在不在,管不了题面问清楚没。
- **(A)良定义 ≠ (B)接地**:一道周锚齐全的题,gold 仍可能没渲进语料(渲染漂移)→ (A) 过 (B) 挂。
- **(C)可答性**最强也最贵:即便 (A)(B) 都过,语料里的**干扰**仍可能让"唯一可推"不成立,或反过来太好蒙。

> 一句论文话:**"加了接地闸,盲审还能挑出 L2 那族 bug"——不是接地闸没用,而是那是【另一条边】的事。** 三道闸正交,缺一不可。

## V.3 (A)良定义闸 —— 纯代码、确定性(待建,头号)

**核心:`gold = f(entity, field, 时点? 聚合? 路径?…)`;题面必须把这些坐标【全部】点明,否则 gold 不唯一。**
- `intent()` 契约升级:除 `(题面, 须隐藏)`,再返回**它向题面承诺了哪些坐标** `anchors`(如 `{at_week}` / `{agg:max}` / `{temporal:latest}`)。
- **良定义闸**:只用 `(entity, field, anchors)` 回世界重算 → 若得到**不止一个**值 ⇒ 题面没锁死 gold ⇒ **ill-posed,不出厂**。
  - L2 无周锚 + 汇报对象逐周变 → 多值 → 毙 → **逼 intent 必须吐 at_week**(不是手工补,是闸强制);
  - Q07"几个"但 gold 是人名 → 承诺坐标推不出计数 → 毙。
- **它是接地闸的对偶**(同一哲学:代码兜底、坏题出不了厂):让"题面完整"成为**可验证的不变量**,而非靠出题人记性。L1 的 IE/KU 早就带周锚——良定义闸把这条标准对全线**强制化**。

## V.4 (C)可答性闸 —— reader 式(未来)

完整的"读语料唯一推出 gold"需 reader:valid ⟺ 强记忆系统读语料 = gold 且无语料基线 ≠ gold。天然非确定、要 LLM,留 v1。**注:多系统评测 `eval/multi_system.py` 本就是 (C) 的雏形——"评测"与"出厂可答性验证"是同一动作的两面**(与 §9.2 硬判别器同源)。(A)(B) 两道纯代码闸已能根治目前盲审暴露的绝大多数。

## V.5 与 §S / audit 的关系(★同一组纲领的三面)

三者各管一种"质量可证伪",合起来才完整:

| 纲领 | 一句话 | 治什么 |
|---|---|---|
| **§V 验证三角** | (题面, gold, 语料) 三边一致 | 单题的**良定义 / 接地 / 可答性** |
| **§S 白皮书=被执行的合同** | 议会算的目标(weight / 规模 / 难度 / 配额)执行端必须真消费 | 该执行的**没执行** |
| **audit 单一域真相源** | 域值只从白皮书/世界来,代码/prompt 不私藏 | 该派生的**写死了** |

> 统一精神:**所有"真相"(域值 / 目标 / gold)只有一个源(白皮书 / 世界),且每一步都被【代码可证伪地】校验。** §V 验的是"题成不成立",§S 管的是"目标落没落地",audit 堵的是"域知识有没有私藏"——三面合一 = 一个能写进论文、且可证伪的质量纲领。

## V.6 出处
`blind_review_v3.md`(暴露 (B) 边:幽灵 / 串值)+ `blind_review_run0604.md`(暴露 (A) 边:L2 时序无周锚——接地却 ill-posed)。验证三角 = 这两轮独立盲审的统一抽象。
