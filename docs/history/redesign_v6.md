# V6 设计 spec — 螺旋重构:弃白名单、弃命门,回归 motivation

> **触发**:V5 dry-run 暴露两个根本错误 —(1)"复现毕设 simpleMem×CR R3=10.1%"是自加的虚命门;(2)白名单机制不科学(不可泛化 + 不可证伪 + 循环自证 + 只为虚命门服务)。
> **用户判断**:"这是螺旋而非前进" — V6 回到 V3.1 的自然合成基础,把 cell-driven 思想螺旋上去,砍掉 V4/V5 的白名单弯路。
> **方案**:A 为主(自然合成 + W2 让数据说话),B 记账(evaluator-in-loop 后续遇瓶颈再上)。

---

## 0. 一句话

**输入(场景描述 + few-shot 文档)→ cell-driven 配额规划 → 自然合成 corpus → 按配额出题 → W2 真实 baseline 评测**,用 baseline ranking 的稳定性 + 与真实 benchmark 的相关性证明生成器有效,而不是复现任何单一失败数字。

---

## 1. Motivation 重申 + 科学评价标准(取代"复现 10.1%")

用户初心:**"输入场景+few-shot,自动生成 memory 评测 benchmark"**。一个 benchmark 生成器值不值得发表,科学标准是这 4 条(全部 ≠ 复现 M3 触发率):

| 标准 | 含义 | 怎么验证 |
|---|---|---|
| **A. 有评测价值** | 不同 memory 系统跑出来分数能拉开差距 | W2 跑 ≥3 baseline,看 cell-level 方差 |
| **B. ranking 稳定** | 同 scenario 同 spec 多次 seed 重生成,baseline 排名一致 | 多 seed 重生成,Spearman(rank_seed1, rank_seed2) |
| **C. ranking 与真实 benchmark 相关** | 在 LongMemEval/MAB/OfficeMem 上强的系统,在我们 benchmark 上也强 | Spearman(our_rank, real_bench_rank) |
| **D. 跨场景泛化** | 同一 pipeline 能造 office/客服/医疗 都可用的 benchmark | W4 扩 ≥3 场景 |

> **★ 期望校准(用户 2026-05-30)**:**不要求合成 benchmark 质量超越人工精心配制的**(不现实)。卖点是【自动化 + 可扩展 + 可控配额 + 跨场景泛化】,质量"够用 + 排名相关性高"即可。
> 因此 W2 成功标准【不是】"baseline 表现和人工 benchmark 一模一样",【而是】:① baseline 排名 Spearman 够高(标准 C)② cell 间有区分度(标准 A)③ failmode 预测命中率够用(不要求完美复现)。
> 一句话:**我们卖的是"流水线",不是"米其林单品"**。

---

## 2. 三个范式转变(V5 → V6 的本质)

### 转变 1:失败模式从「强造」→「预测 + 验证」

- **V5(错)**:预设 (KU, M3) 标签 → 用白名单倒推 corpus(Apple→Pyongyang)。这是把研究者先验当 ground truth 硬塞。
- **V6(对)**:自然合成 corpus → 出题时**预测**它"倾向触发哪个失败模式" → W2 跑 baseline **实测**验证。
- **这跟毕设方法论一致**:毕设 M1-M5 是从真实跑出来的失败案例**事后归纳**,不是预设标签倒推。

### 转变 2:M3 的科学重定义 — 反常识 → corpus 内信号竞争 ★ 核心突破

V5 误以为 M3 (conflict defaulting) 的本质是「反常识」(Apple HQ 不该是 Pyongyang)。**错**。

毕设 M3 的真正机制是「**冲突信息中,系统倾向于某个"默认"**」。这个"默认"可以来自:
- 训练分布常识(Apple→Cupertino)← V5 只抓这个,导致要造反常识,陷入 hack
- **corpus 内出现频率更高/位置更显著的值** ← V6 用这个,自然、可泛化、可解释

**V6 的 M3 机制**:corpus 自然演化(负责人 张三→李四),但**旧值"张三"在多个 period 出现 5 次,新值"李四"只在最后 1 个 period 出现 1 次** → 问"最新负责人是谁",系统容易答信号更强的旧值"张三"。

- 这是真实的 memory 失败(LoCoMo / LongMemEval 的 knowledge-update 题就这么测)
- **不需要反常识,不需要白名单,完全可泛化**(任何场景的字段更新都有这种"信号竞争")
- M3 强度旋钮 = **旧值/新值的信号频率比**(corpus 合成时可控),而不是"反常识程度"

### 转变 3:验证目标从「单一命门数字」→「ranking 稳定性 + 相关性」

- V5:死磕"M3 触发率 ≥ 60%"
- V6:看标准 A/B/C/D(baseline 拉开差距 + ranking 稳定 + 与真实 benchmark 相关 + 跨场景)

---

## 3. V6 数据流

```
RawScenarioInput (description + few-shot docs)
  ↓
Stage 0 Clarifier            [保留 V5,可选]
  ↓ RefinedScenarioSpec
Stage A 维度推断              [保留 V5]
  ↓ ScenarioDimensions (I,S,V,T,flavor)
Stage B 配额计划              [保留 V5 三轴 cell_quota,删 field_whitelist]
  ↓ OpsProfileV3 (cell_quota 仅作配额,不带白名单)
Stage A' Seed 多样化          [改造:按 cell 缺口自然合成 seed,不强造反常识]
  ↓ ExpandedSeedPool
Stage C V6 自然合成 corpus    [重写:自然字段演化 + 信号竞争旋钮,删白名单约束]
  ↓ list[Chain](字段演化是真实合理的)
Stage D V6 出题 + failmode 预测 [重写:基于 corpus 真实信号出题,failmode 是预测标签]
  ↓ list[Chain] qas(failmode 是"待 W2 验证的预测")
Stage E 双 wrapper 输出       [保留 V5]
  ↓ Benchmark JSON
══════════════ W2 新增(让数据说话)══════════════
MemoryInterface 适配器        [新写]
  ↓
BenchmarkRunner(ingest → retrieve → answer) [新写]
  ↓ × {simpleMem} × {R1/R2/R3}
LLM-as-Judge 多口径评分        [新写]
  ↓
结果聚合(cell-level baseline 表现) [新写]
  → 验证标准 A(拉开差距)+ failmode 预测命中率
```

---

## 4. 删除清单(螺旋砍掉的 V4/V5 弯路)

| 文件 / 机制 | 处理 | 原因 |
|---|---|---|
| `cell_requirements_table.py` 的 `FIELD_WHITELIST_REGISTRY` | **删** | 白名单不科学(§理由见开头) |
| `CELL_TO_REQUIREMENTS` 的 `field_whitelist_keys` | **删**,保留 `required_field_types` 作题型 hint | 同上 |
| `schema.py` `CellRequirement.field_whitelist` | **删** | 同上 |
| `stage_a_seed_curator_v5.py` `synthesize_seed_for_cell` 的 whitelist 强反常识分支 | **删**,改自然补全 | 自然合成 |
| `stage_c_corpus_synthesis_v5.py` skeleton "强制从 whitelist 挑 conflict" | **删**,改"自然演化 + 信号竞争" | 转变 2 |
| `stage_d_v4_question_synthesis.py` LLM-as-Curator 自检 | **暂留不用**(方案 B 备用) | reject=0 自我应付不可信 |
| "复现毕设 10.1%"作为验证目标 | **删** | 虚命门 |
| `redesign_v5.md` 的白名单章节 + Tier 分级讨论 | **作废**(本 anchor 取代) | 同上 |

**保留(V5 真正的 novelty 锚)**:
- cell_quota 三轴配额(能力 × 失败模式 × 算子)
- reverse-driven 思想(cell_quota 反向影响 Stage C/D,但靠"配额引导"不靠"白名单")
- schema 主体、Stage A/B/E、双 wrapper、config retry

---

## 5. 新增清单

### 5.1 Pipeline 改造

| 文件 | 内容 |
|---|---|
| `stage_c_corpus_synthesis_v6.py` | 自然合成 corpus:从 few-shot 学场景字段分布 → 合成同分布文档 → **字段演化真实合理**(P0 缺陷率自然下降、负责人自然交接)→ **信号竞争旋钮**(KU 题字段:旧值出现频率 > 新值,制造 M3 材料) |
| `stage_d_question_synthesis_v6.py` | 按 cell_quota 出题;failmode 是**预测标签**(基于 corpus 信号特征预测可能触发哪个失败模式);不强造、不预设 ground truth |
| `run_pipeline_v6.py` | 串 Stage A→B→A'→C V6→D V6→E |
| `schema.py` 改 | 删 field_whitelist;`Question.failmode` 语义改为"predicted_failmode";加 `failmode_evidence`(corpus 内信号竞争证据,如旧值频率/新值频率) |

### 5.2 W2 评测基础设施(★ 真正让数据说话)

| 文件 | 内容 |
|---|---|
| `eval/memory_interface.py` | MemoryInterface ABC(毕设 3.5 节签名:ingest/retrieve/update/reset)+ SimpleMemAdapter(wrap `/Users/ryanleory/proj/memory-systems-eval/simpleMem_src/`)|
| `eval/benchmark_runner.py` | 按 chains[i].periods[j] 顺序 ingest_docs → 逐 qa retrieve + LLM generate → 记 prediction;支持 R1/R2/R3(复用 `memory-systems-eval/adaptors.py`)|
| `eval/judge.py` | 多口径评分:EM / sub_match / fuzzy / llm_judge;ABS 题 sentinel 判定;**failmode 预测命中检测**(M3 题:系统是否答了"信号更强的旧值") |
| `eval/aggregate.py` | cell-level 聚合:(capability × failmode × R) 的 accuracy 矩阵;failmode 预测命中率;baseline ranking |
| `eval/run_eval.py` | 端到端:V6 benchmark JSON × simpleMem × {R1,R2,R3} → 结果表 |

---

## 6. 方案 B 钩子(记账,后续遇瓶颈再上)

如果 W2 评测发现「生成的题太简单 / 太难 / 无区分度」,启用 evaluator-in-loop:
- Stage D 出题后立即跑 1 个轻量 baseline 测试
- 题太简单(所有 R 都对)→ Evol 升级
- 题太难(所有 R 都错)→ 抛弃
- 题刚好(R1 对 / R3 错,或 baseline 间有差异)→ 保留

**预留接口**:`stage_d_question_synthesis_v6.py` 的出题函数加可选 `validator_fn` 参数(默认 None = 不验证;后续传 baseline runner 即开启 in-loop)。

---

## 7. 论文卖点 framing(取代"复现 M3")

> **A declarative, cell-driven generator for memory-agent benchmarks.** 输入一段场景描述 + 少量示例文档,沿 (能力 × 失败模式 × 算子) 三轴声明式配额,反向驱动合成可控、可重生成的评测集。我们证明:(i) 生成的 benchmark 在多个 memory 系统上产生显著且可解释的表现差异(标准 A);(ii) 同配额多次重生成的 baseline ranking 稳定(标准 B);(iii) 该 ranking 与人工 benchmark(LongMemEval/MAB/OfficeMem)的 ranking 显著相关(标准 C);(iv) 同一生成器跨多个场景域均产出可用 benchmark(标准 D)。

**关键差异化(vs AutoBencher / DataMorgana)**:它们 forward 生成 QA;我们把**失败模式分类**(借毕设 M1-M5)作为 declarative 配额轴反向驱动 corpus 合成,且失败模式靠 corpus 内**自然信号竞争**实现(不靠反常识注入),可泛化到任意场景。

---

## 8. V6 task 拆分

| Task | 内容 | 工作量 |
|---|---|---|
| **T1** | redesign_v6.md(本文档)+ schema 改(删 whitelist,failmode→predicted,加 failmode_evidence) | 0.5 天 |
| **T2** | Stage C V6:自然合成 corpus + 信号竞争旋钮(M3 = 旧值频率 > 新值) | 2 天 |
| **T3** | Stage D V6:按 cell_quota 出题 + failmode 预测(基于信号特征,不强造) | 2 天 |
| **T4** | run_pipeline_v6 端到端 + smoke test(office 场景,看 corpus 自然度 + failmode 预测合理性) | 1 天 |
| **T5** | ★ W2 评测基础设施:memory_interface + benchmark_runner + judge + aggregate | 3 天 |
| **T6** | ★ W2 跑 simpleMem × R1/R2/R3 on V6 benchmark,看 cell-level 差异 + failmode 预测命中率(标准 A 验证) | 1 天 |

**总 ~9.5 天**。T5(W2 评测基础设施)是真正的新增重头戏 —— 之前 V1-V5 全在"生成端"打转,V6 第一次建"评测端",这才是"让数据说话"的物质基础。

---

## 9. 与历史版本的关系(螺旋图)

```
V1 贴标签 ──────────────────────────────── ✗ 推翻
V2 LLM+全量 ────────────────────────────── ✗ 推翻
V3 cell_quota+signature ─┐
V3.1 三轴真消费 ──────────┤ ← V6 螺旋回到这里的"自然合成"基础
V4 LLM-Curator ───────────┤   (V4/V5 的白名单+命门弯路砍掉)
V5 白名单+reverse-driven ─┘   (reverse-driven 思想螺旋保留)
                              ↓
V6 = V3.1 自然合成 + V5 cell-driven 思想 + 全新 W2 评测端
```

V6 不是 V5+1,是螺旋上升:**砍掉两条弯路(白名单、命门),保留一个思想(cell-driven reverse),长出一个新器官(W2 评测端)**。

---

## 10. 引用

- `survey/记忆体评测.pdf` 第 5.4 节(M1-M5 是事后归纳,V6 方法论对齐)
- `docs/anchors/operator_taxonomy.md`(𝓕-𝓔-𝓠 三轴保留)
- `docs/anchors/failmode_taxonomy.md`(M1-M5 作预测标签,不作强造目标)
- `docs/anchors/scenario_dimensions.md`(5 维场景空间,卖点 D)
- `/Users/ryanleory/proj/memory-systems-eval/`(simpleMem + adaptors,W2 接入点)
- V5 dry-run 结论(`tools/dry_run_ku_m3.py` 输出):Pyongyang 反噬 + Elvis 元识别 = 白名单方向错的实证

redesign_v3.md / v5.md 保留作版本史,但**以本 V6 为准**。
