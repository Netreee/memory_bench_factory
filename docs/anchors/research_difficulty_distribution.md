# 难度的 定义 / 度量 / 控制·分布 —— 调研报告

> **动机**:当前工厂"难度"是隐式的(靠场景规模、靠 L2.7 中段查询碰运气)。本报告把"难度"补成可指定的生成旋钮——像 `target_tokens` / 题数那样,**指定难度档与比例**,并能验证。
> **方法**:① 读全 `docs/literature/` 的 memory benchmark + `redesign_factory_v2.2 §9/§L2.7` + `related_work.md`;② Web 深挖 14 篇借鉴管线的难度处理。
> **一句话**:业界把难度做成"**轴(怎么变难)× 度量(失败率 / IRT)× 分布目标(各档比例)**"三件套;我们**轴已最全、度量缺位、分布无目标**——补这两块即可把难度升为一等旋钮。

---

## 1. 各管线"难度 定义 / 度量 / 控制"速查表

| 管线 (arXiv) | **定义**(什么使其难) | **度量**(怎么知道难) | **控制 / 分布**(能否指定难度档比例) |
|---|---|---|---|
| **AutoBencher** (2407.08351, ICLR'25) | topic + 算子组合;难=privileged-info 题压低模型准确率 | **Difficulty = 1 − maxₘ acc**(最低错误率/headroom);Separability=acc 平均绝对差;Novelty=1−rankcorr | ★难度是**显式优化目标** `J = Novelty + β₁·Diff + β₂·Sep`;**agentic adaptive search**:拿 baseline 准确率反馈,迭代专挑压低准确率的 topic/算子。无"档比例"但有难度**最大化** |
| **WizardLM / Evol-Instruct** (2304.12244, ICLR'24) | 5 个 In-Depth 算子:+constraint / deepening / concretize / **+reasoning-steps** / complicate-input(+ In-Breadth 变广) | 无内生度量(靠下游 reward);"每次只`a bit harder`、限 +10~20 词"防过难塌缩 | **难度阶梯式进化**(逐轮拧),但 LLM 改 prompt = "祈祷变难"、gt 不可证伪。我们把它**结构化重做**(见 §3) |
| **DataMorgana** (2501.12789) | 正交类目组合;难度藏在 `linguistic-variation`(similar↔**distant-from-document** 逼语义跳转)、`phrasing`、`with-premise` | 无(只测多样性) | ★**每类目带 `probability` → 直接是分布目标**:配置文件里写各 category 占比,生成即按比例采样组合。**这就是"难度分布作为生成目标"的最干净范式** |
| **TimeQA** (2108.06314) | **easy**=时间"in YEAR"可直接匹配上下文;**hard**=before/after/first/last,**无直接匹配 → 逼时序推理** | 准确率 easy→hard 断崖 | **两档显式标注**(各 20K),按定义机械生成,非事后分级 |
| **LongMemEval** (2410.10813) | 证据**间接/不经意**渲染 + 证据散落 ≤6 session·位置打散 + **haystack 长度**(115k vs 1.5M) | 人工"rewrite 到 desired difficulty" | haystack 长度可配;难度档靠人工,**非程序分布** |
| **LoCoMo** (2402.17753) | single/**multi-hop**/temporal/open/adversarial(拒答);跨 session = 难 | 各类 F1 分桶 | 题型即难度代理,**无比例目标** |
| **ProDa** (2604.24819) | 三层知识 𝒦:L1 概念→L2 关系→**L3 推理链**;链**深度/步数**=难度 | 确定性精确匹配(非 LLM judge);失败回溯 concept-gap / reasoning-deficit | 难度=链长,可调;**无显式档比例**,训评故意同源(我们须反向拉开) |
| **Skill-it!** (2307.14430, NeurIPS'23) | ——(不直接管题难度) | loss 反馈 | ★**配比即分布**:在线乘法权重 `wᵢ∝exp(η·Σ Aᵢⱼ·(1−scoreⱼ))` 把产能倾向薄弱能力。**"指定能力/难度分布 + 动态逼近"的算法模板** |
| **PSN-IRT / Lost-in-Benchmarks** (2505.15055) | —— | ★**4 参数 item 模型**:difficulty *b* / discriminability *a* / guessing *c* / **feasibility *d*(天花板)**;LEH+Fisher 量化题质量 | 不生成,但给**度量金标准** + 选题:点名"难度天花板不足/饱和/极化"为 benchmark 通病 |
| **Easy2Hard-Bench** (2409.18433, NeurIPS'24) | 连续难度评级(IRT/Elo,来自历史作答) | 真实作答拟合的连续 rating | ★**等分位切 easy/medium/hard 三档**;实测难升→分降,验证分级有效 |
| **GRADE** (2508.16994) | 多跳 QA:**hop 数 × 语义距离 × 干扰 × 检索复杂度** | 矩阵化经验度量(对题-文档对算指标) | **难度矩阵**系统化变参,但不强制档比例 |
| **AgentFrontier** (2510.24695) | ZPD"可解但不易" | **LKP/MKO 双角色 + Best-of-N** 验区分度 | 难度闸(弱 baseline 秒杀=太易剔;全错=送审) |
| **辅助**:CodecLM(2404.05875)Self-Rubrics 前置约束 · Polyjuice(2101.00288)受控反事实 · STaR/ReST(2203.14465/2308.08998)失败回炉阈值渐严 ·

> 两条贯穿结论:**(A)** 难度做成生成目标的最成熟两条路 = **DataMorgana 式"每类目 probability → 组合分布"** + **AutoBencher 式"难度入目标函数 + adaptive search"**;**(B)** IRT 文献一致警示——**极化谱(全易或全难)= benchmark 头号病**,且**难度极端处 discriminability 必塌**,平衡分布才最大化区分度(2505.15055 / 2409.18433)。这把"为何要管分布"从偏好升为硬约束。

---

## 2. memory benchmark 专属难度轴(通用 QA 没有、记忆评测独有)

把上表的"定义"列收敛成可拧的记忆难度轴(我们的世界基质天然能精确控制每一根):

1. **时序跨度 / 证据时距**(TimeQA·LongMemEval):查询周距最近变更周越远越难 → **本报告核心可控量**(=§L2.7)。
2. **多跳 hop 数 + 级联**(LoCoMo/2Wiki/MuSiQue/GRADE):桥实体跳数;级联依赖(改一跳塌全链)。
3. **session / 文档距离**(LongMemEval·LoCoMo):证据跨几个 session、位置是否打散、是否"不经意"渲染。
4. **干扰密度 + 干扰类型**(HotpotQA 教训·DataMorgana):**同 schema 异时间点**的硬干扰 ≫ 随机 filler;干扰条数。
5. **知识更新深度 / 过时值陷阱**(KU·BEAM):被覆盖几次、旧值作为干扰的诱惑度。
6. **拒答 / unanswerable 邻近度**(ABS·MuSiQue):问"关系存在前的周"——离可答边界越近越难。
7. **语义距离**(DataMorgana distant-from-document):查询用词与证据用词的脱节程度(逼检索做语义跳转)。
8. **haystack 体量**(needle-in-haystack):语料噪声占比(注意 §11.6——纯放大 filler 只升噪声不升信号难度,要与 1–7 配合)。

---

## 3. ★落到我们:够不够、缺什么、怎么落

### 3.1 我们已有的难度做法(逐条体检)
| 已有 | 是难度轴几号 | 评价 |
|---|---|---|
| **L2.7 TimeQA 式中段查询**(变更边界=易 / 两次变更中段=难) | 轴1 | ★**正确且独占**:mention-on-change 天然制造,gt 代码重算可证伪。**但只是"会产生难题",没有"指定多少比例落中段"** |
| **L2 cross_week 反捷径 + 断连过滤闸**(屏蔽 hop1 / 单周 baseline 能答即淘汰) | 轴2/4 | 强:保证多跳**必要性**;干扰主动塞同类时序实体(对的) |
| **mention-on-change**(每事实只渲一次) | 轴3 | 强:逼跨期回忆 |
| **L3 Kendall-τ 排序** | 轴2 变体 | 有,但难度未参数化(几个事件、跨几周可调而未调) |
| **接地闸 §G** | —— | 治"可答性",非难度;但**与难度耦合**:难题更易不接地,分布目标须在接地存活后统计 |
| **硬判别器(§9.2 LKP/MKO)** | 度量 | ★这是我们**唯一的难度度量雏形**,但只做"太易剔/全错送审"二元闸,没产出连续难度分 |

**判决:轴(定义)我们最全且可证伪——这是护城河;但「度量」只有二元闸、「分布目标」完全缺位。** 这正是 IRT 文献警示的风险敞口:不管分布→大概率极化→区分度塌(而区分度恰是我们论文核心结果)。

### 3.2 "难度分布作为生成目标"怎么落(三步,全部可证伪)

**第一步 · 定义:把难度做成结构化离散档(抄 §9.3 WizardLM 但代码化)。**
为每条产线定一个 **difficulty score = 各结构轴的加权和**,纯由 gt 图/状态机算出、不靠 LLM 猜:
- L2 例:`d = w_h·(hop数−1) + w_t·is_midspan + w_c·cascade_depth + w_d·distractor数 + w_s·session距离`。
- 切 **3 档(easy/medium/hard)** 用 §1 Easy2Hard 的**等分位**或人定阈值。**每拧一档 gt 由代码重算**(这是我们对 Evol-Instruct"祈祷变难"的根本超越)。

**第二步 · 控制·分布:在白皮书加 `difficulty_distribution` 目标 + 点菜分层采样(抄 DataMorgana probability + Skill-it 逼近)。**
- 白皮书 `capability_targets` 增字段:`difficulty_distribution: {easy:0.3, medium:0.5, hard:0.2}`(可全局 / 可按产线)。这就是和 `target_tokens` / 题数并列的旋钮。
- `order_gen` 点菜改 **分层采样**:枚举候选题→代码算 `d`→分档→**按目标比例配额填充**(配额不足的档→拧难度算子合成,如 L2 加 hop / 落中段 / 加 distractor;复用 §9.3 五算子的代码版)。
- 达不成时用 **Skill-it 在线乘法权重**思路把产能挪向缺额档(动态逼近 > 硬课程)。

**第三步 · 验证:三重证据(抄 AutoBencher Difficulty + IRT)。**
- **(a) 结构分布**:出厂报告画 `难度档 × 产线 × 能力` 直方图——证明命中目标比例(像 §9.6 多样性那样落 `manifest.algo.difficulty`)。
- **(b) 经验难度**:用已有三系统(`eval/multi_system.py` no_context / 真系统 / oracle)算 **per-item Difficulty = 1 − max_acc**(AutoBencher 式),验"结构 hard 档真的 acc 更低"(== Easy2Hard 的 sanity check)。**单调升 = 分级有效**;不单调 = 难度公式或语料有问题(指哪修)。
- **(c·选做,论文加分)**:多系统作答跑 **IRT 拟合 item difficulty *b* + discriminability *a***(2505.15055 PSN-IRT),报"我们能按目标产出**平衡且高区分度**的难度谱",直接反击"benchmark 极化"通病——**这条把难度分布从工程旋钮升级成可发表贡献**。

---

## 4. 凝练"可借鉴清单"(该抄哪几招)

| 抄什么(机制) | 来源 | 接入点 |
|---|---|---|
| ★**每档 probability → 组合分布目标** | DataMorgana 2501.12789 | 白皮书 `difficulty_distribution` 字段(第二步) |
| ★**难度=结构轴加权 + 代码重算 gt**(取代"祈祷变难") | WizardLM 5 算子 2304.12244 | 产线 `difficulty_score()` + §9.3 算子代码版(第一步) |
| ★**Difficulty = 1 − max-acc** 作为度量 + 出题时优化目标 | AutoBencher 2407.08351 | `eval/multi_system.py` 现成三系统直接算(第三步 b);可选 adaptive search 补缺额档 |
| ★**等分位切 easy/med/hard + 难升分降 sanity** | Easy2Hard 2409.18433 | 分档阈值 + 验证 b |
| **IRT 4 参数(b/a/c/feasibility)+ "极化谱是病、平衡最大化区分度"** | PSN-IRT 2505.15055 | 论文级度量(第三步 c);警惕全易/全难 |
| **TimeQA 中段=难**(已有,升级为可指定比例) | TimeQA 2108.06314 / §L2.7 | `is_midspan` 进 difficulty_score |
| **同 schema 异时间点硬干扰**(干扰密度轴) | HotpotQA 教训 / §L2.6 | distractor 数进 difficulty_score |
| **在线配比逼近目标分布**(动态>硬课程) | Skill-it 2307.14430 | 缺额档产能再分配(第二步) |
| **unanswerable 邻近边界=难** | MuSiQue/TimeQA / §L2.8 | 拒答档作为最难桶 |
| **缺额档失败回炉 + 阈值渐严** | STaR/ReST 2203.14465 | 拧不出 hard 时回炉重合成 |

**一句话收尾**:难度轴我们已是全场最全且可证伪(护城河);只需补**「结构难度分 + 白皮书分布目标 + 三系统验证」**这条最短路径,就能把"难度"从隐式副产物升为与题量/语料规模并列的一等生成旋钮,且因 gt 可重算 + 三系统实测,**比所有借鉴管线都更可证伪**。

---

## 来源(arXiv / 链接)

AutoBencher 2407.08351 · WizardLM/Evol-Instruct 2304.12244 · DataMorgana 2501.12789 · TimeQA 2108.06314 · LongMemEval 2410.10813 · LoCoMo 2402.17753 · ProDa 2604.24819(本地 `refs/ProDa/`)· Skill-it 2307.14430 · PSN-IRT "Lost in Benchmarks" 2505.15055 · Easy2Hard-Bench 2409.18433 · GRADE 2508.16994 · AgentFrontier 2510.24695 · CodecLM 2404.05875 · Polyjuice 2101.00288 · STaR/ReST 2203.14465 / 2308.08998 · 2Wiki 2011.01060 · MuSiQue 2108.00573 · HotpotQA 1809.09600。本地素材见 `docs/literature/` + `docs/anchors/{related_work,redesign_factory_v2.2 §9/§L2.7}.md`。
