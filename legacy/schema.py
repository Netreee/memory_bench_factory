"""
pipeline.schema — V3 核心数据结构。

V3 关键改动(相对 V1/V2):
  - 能力轴换 LongMemEval 5 维 (IE/MR/TR/KU/ABS + 可选 LRU),替代 V1 的 task_type=AR/CR/TTL/LR
  - 失败模式轴升为配额维度 (M1-M5,M6 不签发)
  - 新增 cell_quota: (capability, failmode) 二维配额(Stage D 签发原子)
  - 新增 RefinedScenarioSpec (Stage 0 Clarifier 输出)
  - Question.failmode_signature 强制实填
  - Question.qid 全局唯一(chain_id 前缀);Question.source_chain_id 反向溯源
  - Period.date 主字段对齐 OfficeMem;Period.timestamp 兼容字段
  - Benchmark.scenario_spec_snapshot(包括 Clarifier 完整对话,可 audit)
  - 双 wrapper 输出:扩展版 + OfficeMem 兼容版
  - 自包含:corpus content inline 到 ingest_docs

详见 docs/anchors/redesign_v3.md。
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Optional
from collections import Counter
import json


# ─────────────────────────────────────────────────────────────────────────────
# 1. 枚举常量(全部来自 redesign_v3.md)
# ─────────────────────────────────────────────────────────────────────────────

# 能力轴(LongMemEval 5 维 + 可选 LRU)
CAPABILITIES = ("IE", "MR", "TR", "KU", "ABS", "LRU")
# IE 信息抽取 / MR 多session推理 / TR 时序推理
# KU 知识更新(吸收 MAB·SF) / ABS 拒答 / LRU 长程理解(场景默认 0)

# 失败模式轴(毕设 5.4 节;M6 是评测设计问题,不签发)
FAILMODES = ("M1", "M2", "M3", "M4", "M5")
# M1 计划过拆 / M2 证据塌陷 / M3 冲突默认化 / M4 干扰项改写 / M5 格式错位

# 算子轴(毕设 3.3.1 节)
FORMATION_OPS = ("F1", "F2", "F3", "F4")
EVOLUTION_OPS = ("E1", "E2", "E3", "E4")
QUERY_OPS = ("Q1", "Q2", "Q3")

# 认知风格 prior(Stage A 推断,不上配额)
COGNITIVE_FLAVORS = ("episodic-heavy", "semantic-heavy",
                     "procedural-heavy", "mixed")

# 结构骨架 hint(StructMemEval 启发,Stage D prompt prior)
STRUCTURE_HINTS = ("tree", "state-machine", "ledger", "free-form")

# 5 维场景空间(scenario_dimensions.md)
INGEST_CHANNELS = (
    "dialogue", "meeting_minutes", "weekly_report", "long_document",
    "email_im", "code_diff_pr", "ticket_crm", "table_dashboard",
    "voice_transcript",
)
MEMORY_SUBJECTS = (
    "individual", "customer", "project", "business_line", "team",
    "contract_matter", "code_repo", "product_sku", "case", "compliance_item",
)
PERSPECTIVES = (
    "first_person", "line_manager", "cross_line_leader",
    "partner_customer", "audit_compliance", "successor", "external_analyst",
)
TEMPORAL_PATTERNS = (
    "preference_drift", "project_phase", "ticket_state_machine",
    "version_milestone", "financial_cycle", "event_driven",
    "weekly", "monthly", "daily", "none",
)

# 判分口径
JUDGE_MODES = ("EM", "sub_match", "fuzzy", "llm_judge")


# ─────────────────────────────────────────────────────────────────────────────
# 2. Document
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Document:
    """corpus 中的单篇文档。

    metadata 至少含 timestamp / author / doc_type 三字段(scenario_spec.md 第 2 节)。
    """
    doc_id: str
    title: str
    content: str
    metadata: dict = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Stage 0 输入/输出
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RawScenarioInput:
    """用户的原始输入(可能模糊)。Stage 0 Clarifier 把它 refine 成 RefinedScenarioSpec。"""
    description: str
    corpus_samples: list  # list[Document]
    optional_hints: dict = field(default_factory=dict)


@dataclass
class RefinedScenarioSpec:
    """经 Clarifier 多轮澄清后的结构化场景 spec(Stage 0 输出 → Stage A 输入)。

    Clarifier 5 原则:max specificity / fill open-ended NOT guess /
    no unwarranted assumptions / first person / cite corpus concretely.
    """
    name: str
    description_refined: str
    corpus_samples: list  # list[Document]

    # 维度提示(Clarifier 显式问出或留 open-ended)
    perspective: str = ""
    subject_type: str = ""
    temporal_pattern: str = ""
    target_size: int = 50
    cognitive_flavor_hint: Optional[str] = None

    # ★ 用户未明确说的字段,标 open-ended(不允许 LLM 脑补)
    open_ended_fields: list = field(default_factory=list)

    # Clarifier 的完整对话历史,可 audit(支持 W4 ablation)
    clarification_history: list = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Stage A 输出:ScenarioDimensions
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ScenarioDimensions:
    """从 RefinedScenarioSpec 推断的 5 维场景定位 + cognitive_flavor prior。"""
    I_ingest_channels: list  # list[str]
    S_memory_subject: str
    V_perspective: str
    T_temporal_pattern: str
    cognitive_flavor: str = "mixed"

    inference_notes: dict = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# 5. Stage A' 输出:Seed Coverage Report
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SeedCoverageReport:
    """V3 版本:corpus_samples 在 5 维空间的覆盖检测。"""
    covered_cells: list  # list[tuple]
    missing_cells: list  # 未覆盖的 (I, S, V) 组合
    bias_score: float = 0.0  # 0-1, 1=完全均衡, 0=完全偏
    per_doc_position: list = field(default_factory=list)  # 每篇 sample 的 5 维位置标注


# ─────────────────────────────────────────────────────────────────────────────
# V5 新增:cell-driven Seed Curator 相关数据结构
# (详见 docs/anchors/redesign_v5.md)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CellRequirement:
    """从一个 (cap, fm) cell 反推出的 corpus 需求。

    V6 改动:删 field_whitelist(白名单方向被 dry-run 证伪,见 redesign_v6.md)。
    cell_quota → CellRequirement 列表 → corpus 应支持的"题型原料"(required_field_types),
    但失败模式靠 corpus 内【自然信号竞争】实现,不靠白名单强反常识注入。
    """
    cap: str                                       # "KU"
    fm: Optional[str]                              # "M3" or None
    quota: int                                     # 8

    # corpus 应支持的"题型原料"类型(e.g. 'multi_period_field_update' → 支持 KU 题)
    # V6:仅作题型 hint,不再绑定白名单
    required_field_types: list = field(default_factory=list)

    # 至少需要几篇 seed 支持本 cell(经验值 max(1, quota // 3))
    min_supporting_seeds: int = 1

    # 该 cell 失败时的 fallback(降级到无 failmode 的同 capability cell)
    # e.g. ("KU", "M3") fallback → ("KU", None)
    fallback_cell: Optional[tuple] = None


@dataclass
class SeedCoverageReportV5:
    """V5 升级版覆盖报告:从"5 维多样性"升级为"per-cell 弹药库覆盖度"。"""

    # {cell_key_string: {"required": int, "actual": int, "supporting_seed_ids": [doc_id]}}
    per_cell_coverage: dict = field(default_factory=dict)

    # 弹药库不足的 cell 列表 [(cap, fm), ...]
    missing_cells: list = field(default_factory=list)

    # 兼容字段:5 维空间偏度(继承自 V3 SeedCoverageReport.bias_score)
    bias_score_dims: float = 0.0

    # ★ 新增:cell-level 弹药库偏度
    # 0 = 每个 cell 都有充足 supporting seeds,1 = 严重偏向少数 cell
    bias_score_cells: float = 0.0

    # 每篇 seed 标了"支持哪些 cell"(LLM 分类输出)
    # [{"doc_id": "...", "supported_cells": [("KU", "M3"), ("IE", None), ...]}]
    per_seed_cell_support: list = field(default_factory=list)


@dataclass
class StageCInputV5:
    """Stage C V5 的输入:相比 V3 多了 cell_requirements 和 coverage_report,
    让 skeleton 生成时被 cell_quota 引导。
    """
    expanded_seeds: list                            # list[Document]
    dimensions: Optional["ScenarioDimensions"] = None
    profile: Optional["OpsProfileV3"] = None
    cell_requirements: list = field(default_factory=list)   # list[CellRequirement]
    coverage_report: Optional["SeedCoverageReportV5"] = None


# ─────────────────────────────────────────────────────────────────────────────
# 6. Stage B 输出:OpsProfileV3 — 三轴架构 + cell_quota
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class OpsProfileV3:
    """配额计划三轴架构(redesign_v3.md 第 3 节)。

    cell_quota 是 SIGMOD novel angle:业界无人把 (能力, 失败模式) 作为配额维度。
    """
    # 主轴 1: 能力配额(LME 5 维 + 可选 LRU)
    capability_quota: dict  # {capability: int}

    # 主轴 2: 失败模式配额(M1-M5;M6/none 不签发)
    failmode_quota: dict   # {failmode_or_none: int}

    # 主轴 3: 算子配额(F/E/Q)
    formation_quota: dict
    evolution_quota: dict
    query_quota: dict

    # ★★ 二维 cell 配额(Stage D 签发原子)
    # key 字符串编码:"KU__M3" / "ABS__none"(JSON 友好)
    cell_quota: dict

    # Prior(只供 Stage D prompt 注入,不上配额)
    cognitive_flavor: str = "mixed"
    structure_hint: str = "free-form"

    target_size: int = 50
    profile_name: str = ""
    rationale: str = ""


def cell_key(capability: str, failmode: Optional[str]) -> str:
    """二维 cell 的字符串 key:KU + M3 → 'KU__M3';ABS + None → 'ABS__none'。"""
    return f"{capability}__{failmode or 'none'}"


def parse_cell_key(key: str) -> tuple:
    """'KU__M3' → ('KU', 'M3');'ABS__none' → ('ABS', None)。"""
    cap, fm = key.split("__", 1)
    return cap, (None if fm == "none" else fm)


# ─────────────────────────────────────────────────────────────────────────────
# 7. Stage D 输出:Question
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Question:
    """一道带完整 V3 三轴标签的题。

    硬约束:
      - qid 全局唯一(f"{chain_id}__{local_id}")
      - failmode_signature 必填(M3 题含 common_default + injected_value 等指纹)
      - answer_canonical_by_period 必填(供 OfficeMem 老 evaluator)
    """
    qid: str
    source_chain_id: str
    question: str
    answer_by_period: dict           # {period_idx: list[str] 等价集}
    answer_canonical_by_period: dict # {period_idx: str 单值}

    # 三轴标签
    capability: str = "IE"      # CAPABILITIES 之一
    # V6:failmode 语义改为【预测标签】(predicted_failmode)—— 出题时基于 corpus 信号
    # 特征预测"这题可能触发哪个失败模式",待 W2 跑 baseline 实测验证,非强造 ground truth
    failmode: Optional[str] = None  # FAILMODES 之一或 None
    failmode_signature: dict = field(default_factory=dict)  # M3/M4/M5 指纹(尽量填)
    # V6 新增:corpus 内信号竞争证据(为何预测此 failmode)
    # 如 M3: {"old_value": "张三", "old_freq": 5, "new_value": "李四", "new_freq": 1,
    #         "signal_ratio": 5.0}  ← 旧值信号强于新值 → 预测 KU 系统倾向答旧值
    failmode_evidence: dict = field(default_factory=dict)

    formation_op: str = "F2"
    evolution_op: Optional[str] = None
    query_op: str = "Q1"

    field_name: Optional[str] = None
    judge_modes: list = field(
        default_factory=lambda: ["EM", "sub_match", "fuzzy", "llm_judge"]
    )


# ─────────────────────────────────────────────────────────────────────────────
# 8. Period / Chain
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Period:
    """一个时间步。

    主字段 date 对齐 OfficeMem(老 evaluator 用这个 key);
    timestamp 是我们扩展字段。
    ingest_docs[i] 必须含 content(自包含,不依赖外部 cwd)。
    """
    period_idx: int
    date: str               # ★ 主字段(对齐 OfficeMem onpolicy_benchmark.json)
    timestamp: str = ""     # 兼容字段(我们扩展用)
    ingest_docs: list = field(default_factory=list)  # list[dict] with content inline
    state: dict = field(default_factory=dict)        # 该 period 后的字段值快照(ground truth)


@dataclass
class Chain:
    chain_id: str
    chain_name: str
    cluster: Optional[str] = None
    period_count: int = 0
    periods: list = field(default_factory=list)  # list[Period]
    qas: list = field(default_factory=list)      # list[Question]


# ─────────────────────────────────────────────────────────────────────────────
# 9. BenchmarkStats(论文实验表 + 配额验证)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BenchmarkStats:
    total_questions: int = 0
    by_capability: dict = field(default_factory=dict)
    by_failmode: dict = field(default_factory=dict)
    by_formation_op: dict = field(default_factory=dict)
    by_evolution_op: dict = field(default_factory=dict)
    by_query_op: dict = field(default_factory=dict)
    by_cell: dict = field(default_factory=dict)  # cell_key → 实际题数
    quota_vs_actual: dict = field(default_factory=dict)  # cell_key → {expected, actual, delta}


# ─────────────────────────────────────────────────────────────────────────────
# 10. Benchmark(顶层输出)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Benchmark:
    benchmark_id: str
    scenario_name: str

    # ★ 反向溯源(description + corpus_doc_ids + Clarifier 对话)
    scenario_spec_snapshot: dict = field(default_factory=dict)

    dimensions: Optional[ScenarioDimensions] = None
    ops_profile: Optional[OpsProfileV3] = None
    chains: list = field(default_factory=list)  # list[Chain]
    statistics: Optional[BenchmarkStats] = None


# ─────────────────────────────────────────────────────────────────────────────
# 11. 序列化:双 wrapper 输出
# ─────────────────────────────────────────────────────────────────────────────

def save_benchmark(bm: Benchmark, path: str) -> None:
    """扩展版 JSON(dict wrapper + 完整 V3 标签)。"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(asdict(bm), f, ensure_ascii=False, indent=2)


def save_benchmark_officemem_compat(bm: Benchmark, path: str) -> None:
    """OfficeMem 兼容版(顶层 list + canonical answer + date 字段)。

    给老 evaluator 喂的格式,丢弃我们的扩展标签。
    """
    out = []
    for c in bm.chains:
        chain_d = {
            "chain_id": c.chain_id,
            "chain_name": c.chain_name,
            "cluster": c.cluster,
            "period_count": c.period_count,
            "periods": [],
            "qas": [],
        }
        for p in c.periods:
            chain_d["periods"].append({
                "period_idx": p.period_idx,
                "date": p.date,                  # OfficeMem 用 date 不用 timestamp
                "ingest_docs": p.ingest_docs,    # content inline
            })
        for q in c.qas:
            chain_d["qas"].append({
                "qid": q.qid,
                "question": q.question,
                "field_name": q.field_name,
                "answer_by_period": q.answer_canonical_by_period,  # ★ 单值,不是等价集
            })
        out.append(chain_d)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)


def load_benchmark(path: str) -> Benchmark:
    """反序列化扩展版 JSON 回 typed Benchmark。"""
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    chains = [_chain_from_dict(c) for c in d.get("chains", [])]
    return Benchmark(
        benchmark_id=d.get("benchmark_id", ""),
        scenario_name=d.get("scenario_name", ""),
        scenario_spec_snapshot=d.get("scenario_spec_snapshot", {}) or {},
        dimensions=(ScenarioDimensions(**d["dimensions"])
                    if d.get("dimensions") else None),
        ops_profile=(OpsProfileV3(**d["ops_profile"])
                     if d.get("ops_profile") else None),
        chains=chains,
        statistics=(BenchmarkStats(**d["statistics"])
                    if d.get("statistics") else None),
    )


def _chain_from_dict(d: dict) -> Chain:
    periods = [Period(**p) for p in d.get("periods", [])]
    qas = [Question(**q) for q in d.get("qas", [])]
    return Chain(
        chain_id=d["chain_id"],
        chain_name=d.get("chain_name", ""),
        cluster=d.get("cluster"),
        period_count=d.get("period_count", len(periods)),
        periods=periods,
        qas=qas,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 12. 统计(cell-level 预算 vs 实际 diff)
# ─────────────────────────────────────────────────────────────────────────────

def compute_stats(bm: Benchmark) -> BenchmarkStats:
    """聚合统计 + cell-level 预算 vs 实际 diff,供论文实验表用。"""
    cap_c, fm_c, f_c, e_c, q_c = (Counter() for _ in range(5))
    cell_c: Counter = Counter()
    n = 0
    for chain in bm.chains:
        for qa in chain.qas:
            n += 1
            cap_c[qa.capability] += 1
            fm_c[qa.failmode or "none"] += 1
            f_c[qa.formation_op] += 1
            e_c[qa.evolution_op or "none"] += 1
            q_c[qa.query_op] += 1
            cell_c[cell_key(qa.capability, qa.failmode)] += 1

    stats = BenchmarkStats(
        total_questions=n,
        by_capability=dict(cap_c),
        by_failmode=dict(fm_c),
        by_formation_op=dict(f_c),
        by_evolution_op=dict(e_c),
        by_query_op=dict(q_c),
        by_cell=dict(cell_c),
    )

    # quota vs actual diff(配额验证)
    if bm.ops_profile and bm.ops_profile.cell_quota:
        diff = {}
        for k, expected in bm.ops_profile.cell_quota.items():
            actual = cell_c.get(k, 0)
            diff[k] = {
                "expected": expected,
                "actual": actual,
                "delta": actual - expected,
                "delta_pct": (
                    (actual - expected) / expected * 100 if expected else None
                ),
            }
        stats.quota_vs_actual = diff
    return stats


# ─────────────────────────────────────────────────────────────────────────────
# 13. 校验工具
# ─────────────────────────────────────────────────────────────────────────────

def validate_benchmark(bm: Benchmark) -> list:
    """V3 硬约束校验。返回问题列表,空 = 通过。"""
    problems: list = []

    # qid 全局唯一
    all_qids = [q.qid for c in bm.chains for q in c.qas]
    if len(all_qids) != len(set(all_qids)):
        dups = [q for q in all_qids if all_qids.count(q) > 1]
        problems.append(f"qid 全局不唯一(重复:{sorted(set(dups))[:5]}...)")

    # ABS 题的拒答 sentinel 列表(canonical 必须含其一)
    ABSTAIN_TOKENS = (
        "insufficient_evidence", "i don't know", "未提及", "未提及。",
        "不知道", "abstain", "no info", "无信息", "无法回答",
        "信息不足", "未提到", "无相关信息",
    )

    for c in bm.chains:
        for q in c.qas:
            # 必填项
            if not q.qid:
                problems.append(f"chain {c.chain_id} 有 qa 缺 qid")
            if not q.source_chain_id:
                problems.append(f"{q.qid} 缺 source_chain_id")

            # ABS 题用 sentinel 校验,非 ABS 题用常规校验
            if q.capability == "ABS":
                canon_vals = list(q.answer_canonical_by_period.values())
                if not canon_vals:
                    problems.append(
                        f"{q.qid} ABS 题 canonical 为空,应至少有 1 个 period "
                        f"标拒答 sentinel(如 'INSUFFICIENT_EVIDENCE')"
                    )
                else:
                    has_sentinel = any(
                        any(tok in str(v).lower() for tok in ABSTAIN_TOKENS)
                        for v in canon_vals
                    )
                    if not has_sentinel:
                        problems.append(
                            f"{q.qid} ABS 题 canonical={canon_vals} 不含拒答 sentinel"
                        )
            else:
                if not q.answer_by_period:
                    problems.append(f"{q.qid} 缺 answer_by_period")
                if not q.answer_canonical_by_period:
                    problems.append(f"{q.qid} 缺 answer_canonical_by_period")

            # 三轴枚举值
            if q.capability not in CAPABILITIES:
                problems.append(f"{q.qid}.capability 非法: {q.capability}")
            if q.failmode is not None and q.failmode not in FAILMODES:
                problems.append(f"{q.qid}.failmode 非法: {q.failmode}")
            if q.formation_op not in FORMATION_OPS:
                problems.append(f"{q.qid}.formation_op 非法: {q.formation_op}")
            if q.evolution_op is not None and q.evolution_op not in EVOLUTION_OPS:
                problems.append(f"{q.qid}.evolution_op 非法: {q.evolution_op}")
            if q.query_op not in QUERY_OPS:
                problems.append(f"{q.qid}.query_op 非法: {q.query_op}")

            # ★ failmode_signature 强约束(V6:M3 可改由 failmode_evidence 承载信号竞争证据)
            # V6 语义:failmode 是【预测标签】。仅 M3/M4/M5 有内容指纹(信号竞争/改写项/格式集);
            # M1(计划过拆)/M2(证据塌陷)是结构性推理陷阱,无内容指纹,不强求 signature。
            if (q.failmode in ("M3", "M4", "M5")
                    and not q.failmode_signature and not q.failmode_evidence):
                problems.append(f"{q.qid} failmode={q.failmode} 但 signature/evidence 均为空")
            if q.failmode == "M3":
                sig = q.failmode_signature or {}
                ev = q.failmode_evidence or {}
                if ev:  # V6 信号竞争:证据在 failmode_evidence
                    for key in ("old_value", "new_value", "signal_ratio"):
                        if key not in ev:
                            problems.append(f"{q.qid} M3(V6信号竞争) 缺 failmode_evidence.{key}")
                else:    # V5 反常识 fallback:证据在 failmode_signature
                    for key in ("common_default", "injected_value"):
                        if key not in sig:
                            problems.append(f"{q.qid} M3 题缺 signature.{key}")
            if q.failmode == "M4":
                sig = q.failmode_signature or {}
                if "verbatim_span" not in sig and "distractor_options" not in sig:
                    problems.append(f"{q.qid} M4 题缺 signature.verbatim_span/distractor_options")

    return problems
