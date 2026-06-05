# Related Work · 对标与差异化(论文 related-work 底稿)

> **用途**:论文 related work 的底稿 + novelty 防守。最近邻竞品逐一对标,核心论证"**没人占住『记忆 benchmark 元工厂 × 可证伪代码 gt × L1–L7 正交 × 场景自适应』四交集**"。
> **更新**:2026-06-03 加入 5 篇深读(DataMorgana / InfoSynth / AgentFrontier / LifeDialBench / Memora)。早期 17-benchmark 对标见 `benchmark_landscape.md`。

---

## §1 最近邻竞品对标表

| 工作 | arxiv | 是什么 | gt 怎么来 | 威胁 | 最该偷 |
|---|---|---|---|---|---|
| **DataMorgana** | 2501.12789 | RAG QA **生成器** | LLM 写答 + LLM 过滤(软标) | 低 | 多样性量化指标(NDG/自重复率/压缩比/句向量+bootstrap) |
| **InfoSynth** | 2601.00575 | 代码 bench **生成器** | LLM 写解+测试 → **代码执行过滤** | 低 | 信息论新颖度/多样性当**适应度函数**(KL散度/微分熵) |
| **AgentFrontier** | 2510.24695 | **训练**数据合成(eval 副产品) | agent 自洽 + LLM judge(IsSolvableBy) | 低–中 | **LKP–MKO 双角色难度闸**、Best-of-N 验区分度、"计算化"升级算子 |
| **LifeDialBench** | 2604.11182 | lifelog 对话 **benchmark 本体** | LLM 合成 + 人工抽检 + LLM judge | 中 | **online 流式评测**(冻结记忆态防未来污染)、时间定位题型 |
| **Memora**(Recall→Forgetting) | 2604.20006 | 个人助理对话 **benchmark 本体** | 模拟器 trace + 代码派生准则(trace 含 LLM、LLM 评委) | **中(最近)** | **FAMA 遗忘指标**、非单调偏好三态、难度旋钮 |

> 另:`benchmark_landscape.md` 已对标 MEME / Memora / LongMemEval / LoCoMo / BEAM / MemoryAgentBench / YourBench / AutoBencher 等 17 个。

---

## §2 gt 机制谱系(我们最硬的护城河)

把"标准答案怎么来"从**最依赖 LLM**到**最可证伪**排开,我们独占最右端:

```
LLM写答+LLM判          LLM合成+人工/judge        agent自洽+judge       模拟器trace+代码准则     代码执行过滤        【我们】状态机机械切片
(DataMorgana)          (LifeDialBench)          (AgentFrontier)       (Memora,trace含LLM)    (InfoSynth,纯代码域)  预先算定·可证伪·LLM只写文档
  弱 ───────────────────────────────────────────────────────────────────────────────────────────────────────► 强
```

**关键差异**:即便最接近的两个——InfoSynth(代码执行)只在**纯代码自包含题**域成立、且是"生成后过滤"而非"预先算定";Memora(模拟器 trace)的 trace 仍是 **LLM 模拟产物 + LLM 评委判分**。(注:ProDa 的评分虽是确定性精确匹配、不靠 LLM judge,但答案本身仍是 teacher 自洽产物、无外部锚定——见 §6,落在"代码执行过滤"右侧但仍弱于"预先算定"。)**只有我们做到"从一个独立状态机机械切片、预先算定 gt,LLM 全程不碰答案"**——这是论文最该反复强调的单点。

**形式化:支撑集级训评正交(构造级、可证的不可达性)。** 这是 ProDa 缺、我们有的理论贡献——ProDa 仅有隐式的"共享 L2 ⇒ 可达"直觉(且实为故意训评同源,见 §6),我们把它**反用并升级**为可证保证:设变更周 w 的信号事实集 F_w 在语料中**只渲染一次**;训练样本 t 的支撑 supp(t) 落在单个 F_w 内;评测题 q 的答案 ans(q)=g(F_{w₁},…,F_{wₖ}),k≥2 且诸 wᵢ 互异,故 supp(q) **跨多个变更周**。于是 **∀t,q: supp(t) ⊉ supp(q)**——训练样本对任何评测题在支撑集覆盖意义下**实例级不可达**,模型不能靠记忆单条事实答题,必须跨周多跳组合。这把"防泄漏"从 ProDa 那种事后启发式隔离(可破)提升为**构造级、可证的正交保证**。

---

## §3 差异化定位话术(可直接进 related work)

- **Memora(最近、心智同源)**:Memora 的状态机真值世界 + add/update/delete 轨迹 + 显式遗忘 + 非单调偏好,与我们高度重叠,但它是**单场景(个人助理对话)、单媒介、LLM 评委、一次性数据集**,且明确把社交关系/多用户列为 future work。**定位:Memora ≈ 我们元工厂在『个人助理』这一个场景上的手工弱化实例;我们自动生成、跨场景泛化、代码可证伪、L1–L7 正交。** 借其 FAMA。
- **DataMorgana**:开创"可配置正交类目→组合多样性",但**类目人手写、单文档、无时间/无世界、gt 是 LLM 软标+过滤**,且自承不保证答案正确/类目一致。**定位:我们承其多样性度量,以可证伪 gt + 状态机世界 + 场景实例化补其全部空白。**
- **InfoSynth**:坐实"代码执行=真值 + 信息论控新颖度",但**纯代码题、无记忆/世界/场景**。**定位:我们把『代码即真值』从代码域推广到任意记忆场景。**
- **AgentFrontier**:**训练侧** ZPD 数据飞轮,真值靠 agent 自洽。**定位:评测侧 + 记忆建模 + 可证逆 gt 是它的空白;借其 LKP–MKO 难度闸即可。** 注意区分"相对模型动态难度(它)vs 结构化绝对难度(我们)"。
- **LifeDialBench**:lifelog 对话 benchmark 本体,卖点 **online 流式评测**(治未来污染),自称"scenario-extensible"但那是**人工流水线可扩展、非自动元架构**。**定位:借其 online 协议 + 时间定位题型;我们以元工厂 + 代码 gt 区别。**

---

## §4 必偷清单(已确认机制 → 接入我们哪条线)

| 偷什么 | 来源 | 接入点 |
|---|---|---|
| **FAMA** = max(0, 该含召回率 − λ·该排残留率),拆 MPA/FAA 双分 | Memora | L1 FORGET 判分 + L4 偏好,比单准确率更暴露"误用过时值" |
| **online 流式评测**:冻结记忆态 M_t + 时间因果流式答题 | LifeDialBench | 诊断层 / Stage G,治未来上下文污染(杀手指标) |
| **多样性量化**:NDG/4-gram自重复率/gzip压缩比/句向量HS + bootstrap;KL新颖度/微分熵 | DataMorgana + InfoSynth | 验证语料多样性 + "不同场景激活不同产线=区分度"(把多样性从印象变带 p 值数据) |
| **LKP–MKO 难度闸**:弱 baseline 秒杀的题剔除、全军覆没送审 | AgentFrontier | 难度分档 / 质检闸 |
| **非单调偏好三态**(强化/弱化/反转) | Memora | L4 偏好产线题型 |
| **难度旋钮**:consolidation 深度 + mutation 次数 | Memora | 区分度量化轴 |

---

## §5 一句话结论

5 篇里**没有一篇抢占我们的四交集**;威胁最高的 Memora 也只是"个人助理"单场景的手工弱化版。**novelty 站得住**,但 related work 必须显式 cite 并用 §3 话术差异化,同时把 §4 必偷项排进路线图。

**来源**:arxiv 2501.12789 / 2601.00575 / 2510.24695 / 2604.11182 / 2604.20006;本地 refs:`refs/Memora/`、`refs/MEME/`、`refs/survey/{LongMemEval,BEAM,MemoryAgentBench,yourbench}/`。

---

## §6 数据合成侧近邻(同学《基于 benchmark 的训练数据生成》调研 → 二次精读 9 篇,2026-06-03)

> 这批是"benchmark→训练数据 / 能力驱动数据合成",与我们"场景→记忆 benchmark"是**镜像/邻接**。每篇拉论文 + 代码精读。
> **总判决:除 ProDa(中)外全部低威胁,但偷点极多;且 9 篇无一例外用"人定标签 or LLM 自由文本"表示能力 → 普遍反衬我们"写死 L1–L7 宪法 + 代码可证伪"的护城河。**

### ★ ProDa(2604.24819,2026-04-27)—— 最形似的 prior art,威胁【中】,必须正面对标
代码已克隆 `refs/ProDa/`(真实可跑,163★,OpenRaiser)。同一套三层知识结构 𝒦(L1 原子概念 / L2 三元关系 / L3 推理链)→ L1/L2 出训练数据、L3 出 benchmark;benchmark = 自动生成的选择题(单选/多选/判断,`extract_mcq_prompt_multi.txt`),**非单元测试/可执行规范**;失败分 concept-gap / reasoning-deficit 回溯知识节点打 patch。
- **三条差异化(都站得住,写进 related work)**:① **gt**——*纠正旧说法:ProDa 评分并非 LLM judge*,它走确定性精确匹配(`evaluator.py:159-160` OpenCompass `AccEvaluator` + `parse_multi_choice_answer`,逐项字母 set 比对;`validate_mcq` 把答案归一为大写字母集),LLM 只在出题与事后误因诊断介入、不判对错。真正软肋在**上游**:gt-bearing 的 MCQ 与 L3 链**无任何 source_quote / 锚定校验**(`validate_mcq` 仅校 question/options/answer,`benchmark_generator.py:83`;`L3_Reasoning_chain.txt` 只有 chain_id/steps,无 quote 字段——锚定仅存在于 L2 抽取层 `L2_Statement.txt:20`,不传到链与题),故答案对错纯靠 teacher 自洽,**无可证伪外部基准**。vs 我们状态机代码机械切片、答案预先算定(**可证伪**),仍是范式级差异;② 它单一线性 pipeline vs 我们**多产线共享世界 + 场景化媒介 + 白皮书激活 = 区分度**;③ 它扁平三层 + 2 类诊断 vs 我们 L1–L7 + 𝓕𝓔𝓠×M1–M6(细一个量级)。
- **可偷(但须破除两个旧误读)**:① *ProDa 根本没有"训评正交/防泄漏"论证*——全仓检索无 orthogonal/leak/contamination/disjoint,README 反而明示同一套 𝒦 既出训练(L1/L2)又出评测(L3),即**故意训评同源**;唯一隔离是事后、可选、可破的启发式 `diagnosis_supplement.py:471-475` `exclude_same_l2`(仅当共享 L2 id 才剔,且补题不足时 `fallback_random_if_insufficient` 从原池**随机回填**、隔离失效)。所以这条非但偷不到,反而是我们必须正面拉开的差距(见 §2 末形式化)。② metadata 回溯链 μ=(chain,L2,L1) 名存实亡——出题只挂 `chain_id`(`benchmark_generator.py:188-190`),诊断去读 `bench_info["l2_ids"]/["l1_ids"]` 永远取空(`diagnosis.py:305-306`)→ 实际只回溯到 chain_id 一层;我们的多层因素回溯不能照搬其声称、得自己建。
- **更正旧笔记**:非"11.7 万教材 + GPT-5 Teacher + 纯科学推理"——实为 16 学科、输入语料 10:1 过滤、抽取模型 provider-agnostic(无 GPT-5 Teacher 设定)。

### 其余 8 篇:低威胁,按"接入我们哪条线"组织偷点
| 偷什么(机制) | 来源 | 接入我们哪条线 |
|---|---|---|
| encode→metadata→decode + **Self-Rubrics(先定 rubric 后合成)** + Contrastive Filtering | CodecLM(NAACL24,无 repo) | 中央办公室/白皮书(同构"压缩→展开")+ 四闸(出题前置质量闸 + 难度/价值闸,judge 换成代码 gt) |
| **图聚合打分 S·A**(目标数据不提 skill,靠相似 + 图传播) | MASS(2503.14917,已 clone) | 产线**覆盖度/配比量化**;但其图是**共现**非依赖 → 我们若建图须是**能力依赖 DAG** |
| **在线 MW 配比 eq.4**(loss 反馈给薄弱能力加配额)+ OOD 跨集依赖图 + **loss 轨迹聚类校验能力边界(61%>语义 39%)** | Skill-it!(NeurIPS23,已 clone) | 中央办公室**动态配比**(非静态)+ ★**实测 L1–L7 依赖序**(单训 vs 合训消融 → 7×7 依赖图,别预设层级);动态配比 > 硬课程 |
| **5 个 in-depth 变难算子**(加约束/加深/具体化/+推理步/复杂化输入) | WizardLM/Evol-Instruct(ICLR24,9.5k★) | 难度旋钮——把"prompt 模糊变难"重做成**结构化可控可证伪**(状态机真加跳/级联/干扰,gt 代码重算);它是"LLM 猜难度"的反面对照 |
| **受控反事实**(8 control codes + 依存树**机械**选位 + 困惑度有效性闸) | Polyjuice(ACL21,已 clone) | 把扰动从 token 层抬到**能力因素层**造"差一点就对"对照题/硬干扰;困惑度闸 → 硬判别器 |
| **自举闭环**:STaR rationalization(失败样本 + gt 提示回炉)+ ReST 阈值渐严 + 数据复用 | STaR(NeurIPS22,有 repo)/ReST(DeepMind23) | 质量闸 → 回炉重生成 + AdaptiveSearch 反馈环 |
| span/rationale 定位 + DPO 偏好对 + FR/ACCδ 翻转验证 | 反事实 CDA 族:DICT(ACL25)/RACE(EMNLP23,有 repo) | 把"token 级反事实"抬到**能力因素层**做对照 + 防泄漏闸(它们停在 token = 我们的机会) |
| 嵌入 + 相似度排序选"像 benchmark"的语料(= 污染原型)+ n-gram 去污 | **BETR**(Apple 2507.12466,无 repo) | ★**反面教材**:反用其相似度做**反泄漏自检闸**(产出题离公开 benchmark 太近就丢);论证我们"造新鲜虚构世界 + 训评正交"的防污染立场 |

### 一条贯穿 9 篇的元洞察(可直接喂论文 intro)
9 篇表示"能力/技能/因素"非此即彼:**人工写死标签**(粗、不可扩展)或 **LLM 自由文本**(模糊、不可证伪、无统一本体)——**没有一个有"代码可证伪的能力表示"**。而我同学调研结尾苦寻的"比 Skill 更具体、能关联失败、支持反事实验证的中间层(Capability Factors)",**正是我们 𝓕𝓔𝓠 算子 × M1–M6 × L1–L7 提供的东西**。所以我们的诊断层不只是护城河,也是这一整条数据合成线**公开未解问题的答案**。

**新增本地 refs**:`refs/{ProDa,MASS,Skill-it,Polyjuice}/`(已 clone);**来源 arxiv**:2604.24819 / 2404.05875 / 2503.14917 / 2307.14430 / 2304.12244 / 2101.00288 / 2203.14465 / 2308.08998 / 2505.x(DICT ACL25)/ 2310.14508 / 2507.12466。
