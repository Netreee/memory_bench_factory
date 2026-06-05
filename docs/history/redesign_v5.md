# V5 设计 spec — Reverse-Driven Data Synthesis

> **背景**:V4 工程层全胜(配额 100% / qid 唯一 / signature 100% 填),但 SIGMOD 命门(KU+M3 反常识强度)在 Stage C 源头就塌陷 — V4 在下游修上游的产物,是治标。
> **根因**:Stage A' (Seed) 和 Stage B (cell_quota) 当前是平行独立的,Stage C 不知道下游要什么,自由发挥 skeleton。
> **V5 主张**:**评测需求(cell_quota)反向驱动到 Seed 准备阶段**,让数据合成 pipeline 的每一步都被评测维度牵引。

---

## 1. SIGMOD 真正卖点 — Reverse-Driven Data Synthesis

把现有数据合成方法摆在一起,我们的差异化在哪:

| 方法 | 方向 | 关键词 |
|---|---|---|
| Self-Instruct (2022) | forward | 175 种子 → 52K 指令 |
| Evol-Instruct (2023) | forward + 难度演化 | 种子 → 演化 → 难题 |
| DataMorgana (2025) | declarative 分布控制 | 描述用户/题型分布 → 生成 RAG QA |
| AutoBencher (ICLR'25) | declarative description | 描述 benchmark → 搜索题型 |
| **我们 (V5)** | **reverse-driven** | **评测维度 → 反推 Seed → 合成 corpus → 合成题** |

**新颖点 = "评测维度通过 cell_quota 反向传播到 Seed 准备阶段"**。
- AutoBencher 只到"题型搜索"
- 我们一路反向到**种子工程**
- declarative chain 更长 → 可控性更强 → SIGMOD ablation 实验天然出现

**一句话 framing(SIGMOD intro 用)**:
> 现有 benchmark 合成方法都是 forward:种子 → corpus → 题目。我们提出 **cell_quota declarative spec → reverse-driven seed engineering**:评测维度通过 cell_quota 反向传播到 Seed 准备阶段,让 pipeline 每一步都被评测需求牵引,而不是末端用 LLM-as-Curator 救火。

---

## 2. V1 → V2 → V3 → V4 → V5 演化路径

| 版本 | 致命缺陷 | 主要修法 |
|---|---|---|
| V1 | 偷读 OfficeMem 全 1899 篇 + 复用旧 qa 贴标签 | 推翻 |
| V2 | OpsProfile 装饰品 + Spearman 循环论证 + failmode_signature 空 | 推翻 |
| V3 | LLM 真合成新题 + cell_quota 出题维度真消费 + signature 实填 | base |
| V3.1 patch | 三轴算子 quota 真消费 + qid 唯一 + 数学闭合 | A/C 修了 |
| V4 | LLM-as-Curator 自检 + 后处理质检 + 重试 | 工程胜,命门题没解决 |
| **V5** | **cell_quota 反向驱动到 Seed/Skeleton 源头** | **本版本** |

V3.1 / V4 代码继续保留,作 SIGMOD ablation:
- V3.1 vs V5:cell_quota 是否反向驱动的差异
- V4 vs V5:LLM-as-Curator(下游救火)vs reverse-driven(上游对症)

---

## 3. V5 数据流(关键改动)

```
RawScenarioInput (description + 5 篇)
   ↓
Stage 0 Clarifier            [不变]
   ↓ RefinedScenarioSpec
Stage A 维度推断              [不变]
   ↓ ScenarioDimensions (I, S, V, T, flavor)
★ Stage B 配额计划            [提前!不再后置]
   ↓ OpsProfileV3 (cell_quota)
★ Stage A' Seed Curator       [重构!从 cell_quota 反推]
   - 输入: 5 篇 seed + cell_quota + dims
   - 任务:
     ① 把 cell_quota 翻译成 CellRequirement 列表
     ② 检测 5 篇 seed 对每个 cell 的【弹药库覆盖度】
     ③ 缺什么补什么 — LLM 合成新 seed,带强反常识字段
   ↓ ExpandedSeedPool (含 per-cell coverage metadata)
★ Stage C corpus 合成         [重构!skeleton 接 requirements]
   - 输入: ExpandedSeedPool + cell_requirements
   - skeleton 必须从【强反常识字段白名单】挑 conflict 字段
   - 必须包含 cell_quota 需要的字段类型(F4 题 → 必有层级摘要等)
   ↓ list[Chain]
Stage D 题目生成              [V4 质检保留,但弹药库充足]
   - V4 的 LLM-as-Curator 仍在岗(末端拦垃圾)
   - reject 率应大幅下降(因为弹药库已经备好)
   ↓ list[Chain] qas 完整
Stage E 双 wrapper 输出       [不变]
```

**核心结构变化**:
- **Stage B 和 Stage A' 顺序对调**(B 先算 cell_quota,A' 才能反推)
- **Stage A' 接 cell_quota 作输入**(不再只看 I/S/V 维度)
- **Stage C 接 cell_requirements 作输入**(skeleton 不再自由发挥)
- Stage D 形式不变,但 reject 率应从 V4 的"看似 0%(实为 LLM 应付)"变成真正接近 0

---

## 4. 关键新数据结构(schema 改动)

```python
@dataclass
class CellRequirement:
    """从一个 (cap, fm) cell 反推出的 corpus 需求。"""
    cap: str                          # "KU"
    fm: Optional[str]                 # "M3"
    quota: int                        # 8

    # corpus 必须支持的"原料"类型
    required_field_types: list[str]   # e.g. ['strong_reverence_conflict', 'multi_period_evolution']

    # 推荐的字段白名单(用 LLM 训练分布中高频常识值,确保强反常识)
    field_whitelist: list[dict]       # [{type, default, injected_examples}, ...]

    # 至少需要几篇 seed 支持本 cell(经验值,quota/3)
    min_supporting_seeds: int

    # 该 cell 失败时的 fallback(降级到无 failmode 的同 capability cell)
    fallback_cell: Optional[tuple] = None


@dataclass
class SeedCoverageReportV5:
    """V5 升级版覆盖报告:不再只看 (I,S,V),还看 per-cell 弹药库。"""
    per_cell_coverage: dict           # {cell_key: {"required": int, "actual": int, "supporting_seeds": [doc_id]}}
    missing_cells: list               # [(cap, fm)] 弹药库不足的 cell
    bias_score_dims: float            # 5 维空间偏度(原 V3)
    bias_score_cells: float           # ★ 新增:cell-level 弹药库偏度


@dataclass
class StageCInputV5:
    """Stage C 的新输入:含 cell_requirements 引导 skeleton 生成。"""
    expanded_seeds: list[Document]
    dimensions: ScenarioDimensions
    profile: OpsProfileV3
    cell_requirements: list[CellRequirement]   # ★ 新增
    coverage_report: SeedCoverageReportV5      # ★ 新增
```

---

## 5. cell-to-requirements 映射表(V5 的灵魂表)

这是 Stage A' V5 把 cell_quota 反推到 corpus 需求的 lookup table:

| cell | 必须的 corpus 特征 | 字段白名单(强反常识 ground truth) |
|---|---|---|
| **(KU, M3)** ★命门 | `strong_reverence_conflict_field` | company_hq_city / ceo_name / currency / founding_year / capital / spoken_language |
| (KU, M2) | `hierarchical_summary_layer` | doc_index / section_titles / summary_tree |
| (KU, None) | `multi_period_field_update` | role_change / version_bump / status_evolve |
| (TR, M3) | `temporal_reverence_conflict` | inverted_release_year / chronological_swap |
| (TR, None) | `multi_period_state_chain` | weekly_state / monthly_state |
| (IE, M5) | `format_polymorphic_field` | numeric_with_units(20% / 0.20 / "20 percent") |
| (IE, M4) | `paraphrasable_verbatim` | quoted_phrases(原文逐字) |
| (IE, M1) | `apparent_multihop_actual_singlehop` | "X 的当前负责人的本周缺陷率"(看似 2 hop 实际 1 hop) |
| (IE, None) | `clean_field_value` | 任意 evolving field |
| (MR, M2) | `multi_doc_aggregation_with_summary` | 跨 period 聚合 + 高层摘要语义偏离 |
| (MR, M1) | `apparent_multihop_in_MR` | 多 period 中"看似多跳的单跳综合"题 |
| (MR, None) | `multi_period_aggregation` | 求最大/求和/比较 |
| (ABS, None) | `explicit_unmentioned_field` | corpus 中明确不出现的属性(个人邮箱地址等) |

**重点**:`(KU, M3)` 的字段白名单 = LLM 训练分布中**高频常识对**(可程序化验证),避免 V3/V4 的 `Minor/Critical / 生产/开发` 弱反差陷阱。

---

## 6. 强反常识字段白名单(Stage C skeleton 必须从这里挑 conflict)

| 类型 | common_default 范例 | injected_value 范例(明显反常识) |
|---|---|---|
| `company_hq_city` | Apple→Cupertino, Microsoft→Redmond | Apple→Pyongyang, Microsoft→Reykjavik |
| `ceo_name` | Apple→Tim Cook | Apple→Elvis Presley |
| `currency` | US company → USD | US company → BTC |
| `founding_year` | Apple→1976 | Apple→1342 |
| `capital_city` | France→Paris | France→Lyon |
| `spoken_language_official` | Japan→Japanese | Japan→Esperanto |
| `numeric_diff_5x` | annual_revenue=100M | annual_revenue=500M(5× injected) |
| `version_chronology` | iPhone 15→2023 | iPhone 15→2017 |
| `domain_canonical` | github.com → Microsoft | github.com → Pinduoduo |
| `physical_constant` | water_boiling_point=100°C | water_boiling_point=212°C(swap unit) |

**注意**:这些是"种子模板",Stage C 合成 skeleton 时,从该模板生成具体的 (entity, field, default, injected) 四元组,并插入到 chain skeleton 里。

---

## 7. Stage A' V5 算法(伪代码)

```python
def cell_driven_seed_curate(
    seeds: list[Document],
    cell_quota: dict,
    dims: ScenarioDimensions,
    llm,
) -> tuple[list[Document], SeedCoverageReportV5]:

    # Step 1: cell_quota → CellRequirement 列表
    requirements = []
    for cell_key, n in cell_quota.items():
        if n == 0:
            continue
        cap, fm = parse_cell_key(cell_key)
        req = CELL_TO_REQUIREMENT_TABLE[(cap, fm)]
        req.quota = n
        req.min_supporting_seeds = max(1, n // 3)
        requirements.append(req)

    # Step 2: 检测当前 seeds 对每个 requirement 的支持度
    # 用 LLM 给每篇 seed 标 "支持哪些 (cap, fm) cell"
    per_seed_cell_support = {}  # {doc_id: [(cap, fm)]}
    for seed in seeds:
        supported = llm_classify_seed_cell_support(seed, requirements, llm)
        per_seed_cell_support[seed.doc_id] = supported

    # Step 3: 反向聚合 per-cell coverage
    per_cell_coverage = {}
    missing_cells = []
    for req in requirements:
        supporting = [d for d, cells in per_seed_cell_support.items()
                      if (req.cap, req.fm) in cells]
        per_cell_coverage[cell_key(req.cap, req.fm)] = {
            "required": req.min_supporting_seeds,
            "actual": len(supporting),
            "supporting_seeds": supporting,
        }
        if len(supporting) < req.min_supporting_seeds:
            missing_cells.append((req.cap, req.fm))

    # Step 4: 缺什么补什么 — LLM 合成新 seed
    new_seeds = []
    for cell in missing_cells:
        req = next(r for r in requirements if (r.cap, r.fm) == cell)
        need = req.min_supporting_seeds - per_cell_coverage[cell_key(*cell)]["actual"]
        for _ in range(need):
            # ★ 关键:Prompt 里强制使用 field_whitelist 的强反常识对
            new_seed = llm_synthesize_seed_for_cell(
                target_cell=cell,
                field_whitelist=req.field_whitelist,
                existing_seeds=seeds,
                dims=dims,
                llm=llm,
            )
            new_seeds.append(new_seed)

    # Step 5: 输出
    return seeds + new_seeds, SeedCoverageReportV5(
        per_cell_coverage=per_cell_coverage,
        missing_cells=missing_cells,
        bias_score_cells=calc_cell_bias(per_cell_coverage),
        bias_score_dims=calc_dim_bias(seeds + new_seeds, dims),  # 兼容 V3
    )
```

---

## 8. Stage C V5 的 skeleton prompt 改动

V3/V4 的 skeleton prompt 给 LLM 太多自由:"6 fields,1 必须是 conflict 类型"。LLM 自由发挥 → 弱反差。

V5 改成:
```
【硬约束】skeleton 必须包含:
- type='conflict' 字段【至少 N 个】,其中 N = sum(cell_quota for cell in M3-bearing-cells)
- type='conflict' 字段【必须从下面白名单挑】:
  {render(cell_requirements.field_whitelist)}
- 禁止使用弱反差对(Minor/Critical, 生产/开发, True/False, X% vs X+5%)
- 每个 type='conflict' 字段必须显式标:
  {"common_default": "<from whitelist>", "injected_value": "<from whitelist>"}
```

---

## 9. SIGMOD 实验设计(reverse-driven 是否真有用?)

V5 上线后,论文的关键 ablation 实验:

### Ablation 1: cell-driven Seed vs Random Seed
- **Condition A**:V3.1 Stage A'(只看 I/S/V 维度多样性)
- **Condition B**:V5 Stage A'(cell-driven Seed Curator)
- 同 Stage B/C/D 跑,在毕设 9 baselines × 3 R 上看排名 Spearman
- **预期**:V5 Spearman 比 V3.1 高 0.05-0.15(因为弹药库准了)

### Ablation 2: cell_quota 反向传播深度
- **D0**:cell_quota 只到 Stage D(V3.1)
- **D1**:cell_quota 到 Stage A'(部分 V5)
- **D2**:cell_quota 到 Stage A' + Stage C(完整 V5)
- 看 Spearman 曲线随深度提升

### Ablation 3: 强反常识字段白名单 vs LLM 自由发挥
- **W0**:Stage C skeleton 完全自由发挥(V3/V4)
- **W1**:Stage C skeleton 接 whitelist 但 LLM 可拒绝(soft constraint)
- **W2**:Stage C skeleton 强制 whitelist(hard constraint,完整 V5)
- 看 (KU, M3) cell 在 simpleMem × R3 上的触发率
- **预期**:W2 触发率 > 60%(对比 V4 的 ~5%)

---

## 10. W1.5 V5 任务拆分(6 个 task,~7 天)

| Task | 内容 | 工作量 |
|---|---|---|
| **T1** | redesign_v5.md(本文档)+ cell_requirements 映射表 + 强反常识字段白名单 落 anchor | 0.5 天 |
| **T2** | schema 加 `CellRequirement` / `SeedCoverageReportV5` / `StageCInputV5` | 0.5 天 |
| **T3** | `run_pipeline_v5.py` 端到端:Stage B 提前,Stage A' 接 cell_quota | 0.5 天 |
| **T4** | `stage_a_seed_curator_v5.py` cell-driven Seed Curator(参 §7 伪代码) | 2-3 天 |
| **T5** | `stage_c_corpus_synthesis_v5.py` skeleton 接 cell_requirements + 强反常识白名单(参 §8) | 2 天 |
| **T6** | 端到端 smoke test + (KU, M3) 命门题反常识强度验证 | 1 天 |

**总 ~7 天**。Stage A' V5(T4)是最大不确定性 — cell-driven curation 是新概念,prompt 设计要打磨。

---

## 11. V5 与 V3.1 / V4 的代码关系

- V3.1 (`stage_d_question_synthesis.py`)、V4 (`stage_d_v4_question_synthesis.py`) **保留**
- V5 新文件:
  - `stage_a_seed_curator_v5.py`(Stage A' V5)
  - `stage_c_corpus_synthesis_v5.py`(Stage C V5)
  - `run_pipeline_v5.py`(端到端)
- Stage D 可复用 V4 或 V3.1(由 `run_pipeline_v5` cfg 选)
- 这样 SIGMOD ablation 实验可以一键切换 V3.1 / V4 / V5 跑

---

## 12. 关键设计原则(全部锁定)

1. **cell_quota 是 declarative spec,贯穿全流程** — 不只是 Stage B 的局部产物
2. **反向传播深度可配置** — 支持 Ablation 2 实验
3. **强反常识字段白名单是 ground truth** — 不是 LLM 自由发挥,可程序化校验
4. **Stage A' 和 Stage B 顺序对调** — B 先算 quota,A' 才能反推
5. **V3.1 / V4 代码保留** — SIGMOD ablation 必须能跑
6. **(KU, M3) 是命门 cell** — 上线后必须有专项验证(simpleMem × R3 触发率 ≥ 50%)
7. **Stage D V4 质检保留** — 末端救火,但 reject 率应大幅下降

---

## 13. 引用

- `survey/记忆体评测.pdf` 第 5.4 节(M3 反常识案例 Seattle vs Nuapada)
- `survey/memory_taxonomy/REPORT.md`(LME 5 维 + 三轴 cell_quota)
- `docs/anchors/redesign_v3.md`(V3 设计基础)
- `docs/anchors/scenario_dimensions.md`(5 维场景空间)
- `docs/anchors/failmode_taxonomy.md`(M1-M5 失败模式)
- V3.1 / V4 实测对照(output/v3_1_office_15q_*.json + stage_d_v4 测试日志)

V3 / V3.1 / V4 不作废,作为 SIGMOD ablation 实验的对照组。
