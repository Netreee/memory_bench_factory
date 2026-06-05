# 00 · 文档总索引(调研 / 框架 / 设计 / 素材)

> **这是全仓文档的单一导航入口**。报告散在 `docs/anchors/`、`survey/`、根目录三处,本表按【价值 + 类别】收齐,标注新鲜度。
> **没物理搬动**——各 redesign 的"出处"引用还指着原路径,搬了会断;此表即"放到一起"的逻辑入口。
> **想快速上手就读**:本表 → `related_work.md`(竞品)→ `redesign_factory_v2.md`(当前设计)。

---

## A · 对标 / 竞品(novelty 防守,论文 related work)

| 文档 | 价值 | 新鲜度 |
|---|---|---|
| **`related_work.md`** | ★5 篇最近邻深读(DataMorgana/InfoSynth/AgentFrontier/LifeDialBench/Memora):对标表 + gt 机制谱系 + 差异化话术 + 必偷清单 | **最新 2026-06** |
| **`benchmark_landscape.md`** | 17 个主流 memory benchmark 对标(题型/数值-vs-离散/出题法/真实 instance)+ 我们的空位 + 该偷的机制 | V10/V11 期 |

## B · 理论框架 / taxonomy(毕设 + survey 凝练,工厂"宪法"的来源)

| 文档 | 价值 |
|---|---|
| **`operator_taxonomy.md`** | 𝓕-𝓔-𝓠 三元算子:记忆系统操作分类(毕设核心) |
| **`failmode_taxonomy.md`** | M1–M6 失败模式分类(诊断层的纵轴) |
| **`scenario_dimensions.md`** | 场景的 5 维正交空间(场景抽象) |
| **`scenario_spec.md`** / **`benchmark_output_schema.md`** | Pipeline 输入 / 输出 Schema 设计原则 |

> 这四份 + L1–L7 坐标(落在代码 `pipeline/constitution.py`)= 工厂"纲领"的全部理论来源。

## C · 深度调研报告(四轮,SIGMOD related work 素材)

| 报告 | 内容 | 备注 |
|---|---|---|
| **`survey/verifier_metrics/REPORT.md`** | Verifier 设计 + 蓝海"可机器判分"指标(26 条精读 + 工具包) | 第二轮·deep |
| **`survey/multiagent_synthesis/REPORT.md`** | Multi-Agent 数据合成架构 + 开源实现(35 条) | 第二轮·deep |
| **`survey/ingest_corpora/REPORT.md`** | 非对话 Ingest 真实形态 + 公开语料(33 条,含 schema/统计) | 第二轮·deep |
| `survey/全景报告.md` | 10 路调研全景 + 危险竞品对比 + v3 蓝图 | 第一轮·**早期,结论部分已被 A 类取代** |
| `调研沉淀.md`(根目录) | 7 路调研沉淀 + v3 设计 + 文献总表 | 第一轮·**与全景报告重叠** |
| `survey/spec_refinement/blog_articles/*.md` | 5 家 deep-research 的 clarify/plan-first 范式(给中央办公室"反问澄清") | 单点素材 |

> **重叠提示**:第一轮两份(全景报告/调研沉淀)结论已被 `benchmark_landscape.md` + `related_work.md` 更新,留作历史;**当前以 A 类为准**。

## D · 设计演进史(redesign specs,螺旋迭代)

| 阶段 | 文档 | 一句话 |
|---|---|---|
| **当前** | **`redesign_factory_v2.md`** | ★元工厂:宪法→中央办公室→多产线→场景化媒介(论文骨架) |
| V11 | `redesign_v11.md` + `v10_pipeline_asbuilt.md` | Tier0(非单调/ORDER/PREEXPIRE)+ V10 as-built 确切实现 |
| V10 | `redesign_v10.md` | 把 gt 从 LLM 手里拿走 → 状态机 state-diff(护城河起点) |
| 历史 | `redesign_v9/v8/v7/v6/v5/v3/v2.md` | 螺旋迭代史(结构裁判→拥抱 LLM→reverse-driven→…),**仅作演进参考** |

## E · 原始素材池(PDF + 克隆仓库,精读底料)

| 位置 | 内容 |
|---|---|
| `survey/记忆体评测.pdf` | ★毕设(𝓕𝓔𝓠 × M1–M6 的原始出处) |
| `survey/memory_taxonomy/papers/` | 记忆评测 20 篇 PDF(LongMemEval/LoCoMo/MemGPT/MemOS/A-MEM…) |
| `docs/literature/` | 36 篇 PDF(MEME/Memora/LongMemEvalV2/MemBench/LoCoMo+/WebDancer…) |
| `refs/` | 克隆仓库:`MEME/` `Memora/` `PSN-IRT/` `survey/{LongMemEval,BEAM,MemoryAgentBench,yourbench}/`(代码 + README + 数据) |
| `survey/{verifier_metrics,multiagent_synthesis}/` 子目录 | 各报告引用的论文/框架 PDF 与 README |

---

## 导航建议

- **看竞品/写 related work** → A 类(`related_work.md` 先,`benchmark_landscape.md` 补全景)。
- **理解我们的理论根** → B 类(𝓕𝓔𝓠 + M1–M6 + L1–L7)。
- **复用调研工具/方法** → C 类三份 deep REPORT 的"工具包"节。
- **理解当前设计/接着干** → `redesign_factory_v2.md`(+ `pipeline/constitution.py`)。
- 第一轮全景报告/调研沉淀:**只当历史**,新结论以 A 类为准。
