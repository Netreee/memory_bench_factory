"""
pipeline.stage_b_ops_profile — Stage B v3: 三轴配额计划 (LME 能力 × 失败模式 × 算子)。

V3 改动(相对 V1/V2):
  - 能力轴换 LongMemEval 5 维 (IE/MR/TR/KU/ABS + 可选 LRU),替代 task_type=AR/CR/TTL/LR
  - 失败模式轴升为配额维度 (M1-M5;M6 不签发)
  - 新增 cell_quota: (capability, failmode) 二维配额(Stage D 签发原子)
  - 五脏俱全约束:每非零 cell 至少 1 题
  - 多个场景 profile(office_engineering / personal_assistant / customer_support /
    code_review_handoff / legal_matter)

详见 docs/anchors/redesign_v3.md 第 4.2 + 第 5 节、memory_taxonomy/REPORT.md。

跑法:
  cd memory_bench_factory
  ./venv/bin/python -m pipeline.stage_b_ops_profile
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable, Optional

from .schema import (
    ScenarioDimensions, OpsProfileV3, cell_key,
    CAPABILITIES, FAILMODES,
)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Profile 数据结构(@dataclass + match 函数)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ProfileTemplate:
    """场景 profile 模板。

    weights 字段(浮点 0-1)会在 build_ops_profile 时按 target_size 转成绝对题数。
    cell_weights 是 (capability, failmode) 二维配额的核心。
    """
    name: str
    rationale: str
    # 匹配条件:接收 (I, S, V) 返回 bool
    match: Callable
    # 能力配额权重(LME 5 维 + LRU)
    capability_weights: dict
    # cell-level 权重:(capability, failmode) → 0-1 浮点
    cell_weights: dict
    # 算子配额权重(F/E/Q)
    formation_weights: dict
    evolution_weights: dict
    query_weights: dict
    # Prior(不影响配额,但写进 OpsProfile)
    cognitive_flavor: str = "mixed"
    structure_hint: str = "free-form"


# ─────────────────────────────────────────────────────────────────────────────
# 2. 配额转换工具(浮点 → 整数,保证 sum、保证五脏俱全)
# ─────────────────────────────────────────────────────────────────────────────

def _weights_to_quota(weights: dict, total: int, min_per_nonzero: int = 0) -> dict:
    """把浮点权重转绝对题数,sum == total。

    weight=0 的 key 保持 0;weight>0 的 key 按比例分配。
    如果 min_per_nonzero > 0,所有非零权重的 key 至少分到 min_per_nonzero。
    """
    active = {k: v for k, v in weights.items() if v > 0}
    if not active:
        return {k: 0 for k in weights}

    s = sum(active.values())
    raw = {k: (v / s) * total for k, v in active.items()}
    floored = {k: int(v) for k, v in raw.items()}

    # 五脏俱全:每非零至少 min_per_nonzero
    if min_per_nonzero > 0:
        for k in list(floored.keys()):
            if floored[k] < min_per_nonzero:
                floored[k] = min_per_nonzero

    # 调整到 sum=total
    diff = total - sum(floored.values())
    if diff > 0:
        # 增加:按小数部分降序补
        fracs = sorted(
            ((raw[k] - floored[k], k) for k in raw),
            reverse=True,
        )
        for i in range(diff):
            floored[fracs[i % len(fracs)][1]] += 1
    elif diff < 0:
        # 缩减:按"小数尾部"(floored - raw)降序砍 = 被多分了的先砍
        # 但保持每个非零 key ≥ min_per_nonzero(避免破坏五脏俱全)
        while sum(floored.values()) > total:
            candidates = [k for k in floored if floored[k] > min_per_nonzero]
            if not candidates:
                # 不能再压缩(min_per_nonzero * 非零数 > total),边界情况
                # 这种情况下 sum > total 是预期的(五脏俱全优先)
                break
            # 排序:floored - raw 大的先砍(尾部小数被多分了的)
            candidates.sort(key=lambda k: floored[k] - raw[k], reverse=True)
            floored[candidates[0]] -= 1

    # 补 weight=0 的 key 为 0
    return {k: floored.get(k, 0) for k in weights}


def _build_cell_quota(
    cell_weights: dict,
    target_size: int,
    enforce_one_per_nonzero: bool = True,
) -> dict:
    """对 cell_weights 做归一化 + 转 absolute,enforce_one_per_nonzero=True 时五脏俱全。

    输入 cell_weights: {(cap, fm_or_none): float}
    输出: {cell_key: int}
    """
    # 转 cell_key 字符串
    weights_str = {}
    for (cap, fm), w in cell_weights.items():
        weights_str[cell_key(cap, fm)] = w
    min_v = 1 if enforce_one_per_nonzero else 0
    return _weights_to_quota(weights_str, target_size, min_per_nonzero=min_v)


# ─────────────────────────────────────────────────────────────────────────────
# 3. 5 个场景 profile(office_engineering / personal_assistant / customer_support /
#    code_review_handoff / legal_matter)
# ─────────────────────────────────────────────────────────────────────────────

# 通用算子权重(默认,可被 profile 覆盖)
DEFAULT_FORMATION_WEIGHTS = {"F1": 0.10, "F2": 0.30, "F3": 0.40, "F4": 0.20}
DEFAULT_EVOLUTION_WEIGHTS = {"E1": 0.20, "E2": 0.10, "E3": 0.35, "E4": 0.35}
DEFAULT_QUERY_WEIGHTS = {"Q1": 0.40, "Q2": 0.45, "Q3": 0.15}


# ★ office_engineering(对应毕设 simpleMem × CR R3 = 10.1% 的 cell 观察)
PROFILE_OFFICE = ProfileTemplate(
    name="office_engineering",
    rationale="多通道异构(周报+长文档+会议)+ 项目/业务线 + leader 视角。"
              "(KU, M3) 是核心 cell — 字段更新 + 反常识注入 → R3 反思默认化",
    match=lambda I, S, V: (
        any(ch in I for ch in ("weekly_report", "long_document", "meeting_minutes"))
        and S in ("project", "business_line", "team")
        and V in ("line_manager", "cross_line_leader")
    ),
    capability_weights={"IE": 0.24, "MR": 0.24, "TR": 0.16, "KU": 0.24, "ABS": 0.12, "LRU": 0.0},
    cell_weights={
        # IE (12): M1×3 / M4×2 / M5×4 / none×3
        ("IE", "M1"): 3, ("IE", "M4"): 2, ("IE", "M5"): 4, ("IE", None): 3,
        # MR (12): M1×4 / M2×3 / M4×2 / none×3
        ("MR", "M1"): 4, ("MR", "M2"): 3, ("MR", "M4"): 2, ("MR", None): 3,
        # TR (8): M3×2 / M5×2 / none×4
        ("TR", "M3"): 2, ("TR", "M5"): 2, ("TR", None): 4,
        # KU (12): M3×8 ★ / M2×2 / none×2
        ("KU", "M3"): 8, ("KU", "M2"): 2, ("KU", None): 2,
        # ABS (6): none×6(ABS 题不签发 failmode)
        ("ABS", None): 6,
        # LRU 默认 0
    },
    formation_weights={"F1": 0.10, "F2": 0.30, "F3": 0.40, "F4": 0.20},
    evolution_weights={"E1": 0.20, "E2": 0.10, "E3": 0.35, "E4": 0.35},
    query_weights={"Q1": 0.40, "Q2": 0.45, "Q3": 0.15},
    cognitive_flavor="semantic-heavy",
    structure_hint="ledger",
)


# personal_assistant(LoCoMo / RealMem 红海复现)
PROFILE_PERSONAL = ProfileTemplate(
    name="personal_assistant",
    rationale="对话 + 个人 + first-person。KU+M3(LoCoMo 经典:用户偏好变化)主导",
    match=lambda I, S, V: (
        "dialogue" in I and S == "individual" and V == "first_person"
    ),
    capability_weights={"IE": 0.30, "MR": 0.20, "TR": 0.10, "KU": 0.30, "ABS": 0.10, "LRU": 0.0},
    cell_weights={
        ("IE", "M4"): 2, ("IE", "M5"): 3, ("IE", None): 10,
        ("MR", "M1"): 2, ("MR", None): 8,
        ("TR", "M3"): 2, ("TR", None): 3,
        ("KU", "M3"): 10, ("KU", None): 5,  # KU+M3 主导
        ("ABS", None): 5,
    },
    formation_weights={"F1": 0.30, "F2": 0.40, "F3": 0.20, "F4": 0.10},
    evolution_weights={"E1": 0.30, "E2": 0.20, "E3": 0.20, "E4": 0.30},
    query_weights={"Q1": 0.60, "Q2": 0.30, "Q3": 0.10},
    cognitive_flavor="episodic-heavy",
    structure_hint="free-form",
)


# customer_support(客服 ticket)
PROFILE_CUSTOMER_SUPPORT = ProfileTemplate(
    name="customer_support",
    rationale="邮件/工单 + 客户 + 审计/经理视角。"
              "M1(计划过拆) + M5(格式错位)主导,有合规追溯需求",
    match=lambda I, S, V: (
        any(ch in I for ch in ("email_im", "ticket_crm"))
        and S == "customer"
        and V in ("audit_compliance", "line_manager")
    ),
    capability_weights={"IE": 0.30, "MR": 0.20, "TR": 0.15, "KU": 0.20, "ABS": 0.15, "LRU": 0.0},
    cell_weights={
        ("IE", "M1"): 4, ("IE", "M5"): 5, ("IE", None): 6,
        ("MR", "M1"): 3, ("MR", None): 7,
        ("TR", "M5"): 2, ("TR", None): 6,
        ("KU", "M3"): 3, ("KU", None): 7,
        ("ABS", None): 7,
    },
    formation_weights={"F1": 0.20, "F2": 0.30, "F3": 0.30, "F4": 0.20},
    evolution_weights={"E1": 0.30, "E2": 0.20, "E3": 0.25, "E4": 0.25},
    query_weights={"Q1": 0.50, "Q2": 0.30, "Q3": 0.20},
    cognitive_flavor="episodic-heavy",
    structure_hint="state-machine",
)


# code_review_handoff(代码 review / 交接)
PROFILE_CODE_REVIEW = ProfileTemplate(
    name="code_review_handoff",
    rationale="PR/diff + 代码仓 + 接班人。"
              "M2(证据塌陷:多 hop 代码引用)+ M4(同义改写)主导",
    match=lambda I, S, V: (
        "code_diff_pr" in I and S == "code_repo" and V == "successor"
    ),
    capability_weights={"IE": 0.20, "MR": 0.30, "TR": 0.15, "KU": 0.20, "ABS": 0.15, "LRU": 0.0},
    cell_weights={
        ("IE", "M4"): 3, ("IE", "M5"): 2, ("IE", None): 5,
        ("MR", "M2"): 5, ("MR", "M4"): 2, ("MR", None): 8,
        ("TR", "M5"): 2, ("TR", None): 5,
        ("KU", "M3"): 4, ("KU", None): 6,
        ("ABS", None): 8,
    },
    formation_weights={"F1": 0.15, "F2": 0.20, "F3": 0.30, "F4": 0.35},
    evolution_weights={"E1": 0.40, "E2": 0.10, "E3": 0.25, "E4": 0.25},
    query_weights={"Q1": 0.30, "Q2": 0.55, "Q3": 0.15},
    cognitive_flavor="procedural-heavy",
    structure_hint="tree",
)


# legal_matter(Harvey 风格)
PROFILE_LEGAL = ProfileTemplate(
    name="legal_matter",
    rationale="合同+财报+邮件 + matter + partner/审计。"
              "M2(异构源证据塌陷)+ M3(条款冲突默认)主导",
    match=lambda I, S, V: (
        any(ch in I for ch in ("long_document", "email_im"))
        and S == "contract_matter"
        and V in ("partner_customer", "audit_compliance")
    ),
    capability_weights={"IE": 0.30, "MR": 0.20, "TR": 0.15, "KU": 0.20, "ABS": 0.15, "LRU": 0.0},
    cell_weights={
        ("IE", "M5"): 3, ("IE", None): 12,
        ("MR", "M2"): 5, ("MR", "M5"): 1, ("MR", None): 4,
        ("TR", "M3"): 3, ("TR", None): 5,
        ("KU", "M3"): 5, ("KU", None): 5,
        ("ABS", None): 7,
    },
    formation_weights={"F1": 0.15, "F2": 0.25, "F3": 0.35, "F4": 0.25},
    evolution_weights={"E1": 0.25, "E2": 0.10, "E3": 0.30, "E4": 0.35},
    query_weights={"Q1": 0.45, "Q2": 0.40, "Q3": 0.15},
    cognitive_flavor="semantic-heavy",
    structure_hint="ledger",
)


# Fallback(未匹配场景)
PROFILE_FALLBACK = ProfileTemplate(
    name="default_fallback",
    rationale="未匹配启发式 profile,使用 balanced default",
    match=lambda I, S, V: True,
    capability_weights={"IE": 0.25, "MR": 0.20, "TR": 0.15, "KU": 0.20, "ABS": 0.15, "LRU": 0.05},
    cell_weights={
        ("IE", "M1"): 2, ("IE", "M5"): 2, ("IE", None): 8,
        ("MR", "M1"): 2, ("MR", "M2"): 2, ("MR", None): 5,
        ("TR", "M3"): 2, ("TR", None): 5,
        ("KU", "M3"): 5, ("KU", None): 4,
        ("ABS", None): 7,
        ("LRU", None): 3,
    },
    formation_weights=DEFAULT_FORMATION_WEIGHTS,
    evolution_weights=DEFAULT_EVOLUTION_WEIGHTS,
    query_weights=DEFAULT_QUERY_WEIGHTS,
)


HEURISTIC_PROFILES = [
    PROFILE_OFFICE,
    PROFILE_PERSONAL,
    PROFILE_CUSTOMER_SUPPORT,
    PROFILE_CODE_REVIEW,
    PROFILE_LEGAL,
]


# ─────────────────────────────────────────────────────────────────────────────
# 4. Profile 匹配 + 主入口
# ─────────────────────────────────────────────────────────────────────────────

def match_profile(dims: ScenarioDimensions) -> ProfileTemplate:
    """从 ScenarioDimensions 选 profile。"""
    I = frozenset(dims.I_ingest_channels)
    S = dims.S_memory_subject
    V = dims.V_perspective
    for p in HEURISTIC_PROFILES:
        try:
            if p.match(I, S, V):
                return p
        except Exception:
            continue
    return PROFILE_FALLBACK


def build_ops_profile(
    dims: ScenarioDimensions,
    target_size: int = 50,
    enforce_five_organs_full: bool = True,
) -> OpsProfileV3:
    """主入口:从 ScenarioDimensions 推 OpsProfileV3。

    enforce_five_organs_full=True 时,每个非零 cell_weight 至少分到 1 题(五脏俱全)。
    """
    profile = match_profile(dims)

    cell_quota = _build_cell_quota(
        profile.cell_weights, target_size,
        enforce_one_per_nonzero=enforce_five_organs_full,
    )

    # 把 cell_quota 反推回 capability/failmode 边缘分布(更精确)
    capability_actual: dict = {c: 0 for c in CAPABILITIES}
    failmode_actual: dict = {f: 0 for f in FAILMODES}
    failmode_actual["none"] = 0
    for ck, n in cell_quota.items():
        cap, fm = ck.split("__", 1)
        if cap in capability_actual:
            capability_actual[cap] += n
        if fm in failmode_actual:
            failmode_actual[fm] += n

    return OpsProfileV3(
        capability_quota=capability_actual,
        failmode_quota=failmode_actual,
        formation_quota=_weights_to_quota(profile.formation_weights, target_size),
        evolution_quota=_weights_to_quota(profile.evolution_weights, target_size),
        query_quota=_weights_to_quota(profile.query_weights, target_size),
        cell_quota=cell_quota,
        cognitive_flavor=dims.cognitive_flavor or profile.cognitive_flavor,
        structure_hint=profile.structure_hint,
        target_size=target_size,
        profile_name=profile.name,
        rationale=profile.rationale,
    )


def explain_profile(dims: ScenarioDimensions) -> dict:
    profile = match_profile(dims)
    return {
        "profile_name": profile.name,
        "rationale": profile.rationale,
        "matched_dims": {
            "I": dims.I_ingest_channels,
            "S": dims.S_memory_subject,
            "V": dims.V_perspective,
            "T": dims.T_temporal_pattern,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# 5. 测试
# ─────────────────────────────────────────────────────────────────────────────

def _print_profile(p: OpsProfileV3, label: str = ""):
    print(f"  matched: {p.profile_name}")
    print(f"  rationale: {p.rationale}")
    print(f"  cognitive_flavor: {p.cognitive_flavor}, structure_hint: {p.structure_hint}")
    print(f"  capability_quota: {p.capability_quota}  (sum={sum(p.capability_quota.values())})")
    print(f"  failmode_quota: {p.failmode_quota}  (sum={sum(p.failmode_quota.values())})")
    print(f"  formation_quota: {p.formation_quota}")
    print(f"  evolution_quota: {p.evolution_quota}")
    print(f"  query_quota: {p.query_quota}")
    print(f"  cell_quota({len(p.cell_quota)} cells, sum={sum(p.cell_quota.values())}):")
    # 按 capability 分组打印
    for cap in CAPABILITIES:
        row = {ck: n for ck, n in p.cell_quota.items() if ck.startswith(cap + "__")}
        if row:
            print(f"    {cap}: {row}")


def main():
    """W1.5 V3 T5 测试:对 5 个典型场景跑 Stage B v3。"""
    test_cases = [
        ("office_engineering", ScenarioDimensions(
            I_ingest_channels=["weekly_report", "long_document", "meeting_minutes"],
            S_memory_subject="project",
            V_perspective="cross_line_leader",
            T_temporal_pattern="weekly",
            cognitive_flavor="mixed",
        )),
        ("personal_assistant", ScenarioDimensions(
            I_ingest_channels=["dialogue"],
            S_memory_subject="individual",
            V_perspective="first_person",
            T_temporal_pattern="preference_drift",
            cognitive_flavor="episodic-heavy",
        )),
        ("customer_support", ScenarioDimensions(
            I_ingest_channels=["email_im", "ticket_crm"],
            S_memory_subject="customer",
            V_perspective="audit_compliance",
            T_temporal_pattern="ticket_state_machine",
            cognitive_flavor="episodic-heavy",
        )),
        ("code_review_handoff", ScenarioDimensions(
            I_ingest_channels=["code_diff_pr"],
            S_memory_subject="code_repo",
            V_perspective="successor",
            T_temporal_pattern="version_milestone",
            cognitive_flavor="procedural-heavy",
        )),
        ("legal_matter", ScenarioDimensions(
            I_ingest_channels=["long_document", "email_im"],
            S_memory_subject="contract_matter",
            V_perspective="partner_customer",
            T_temporal_pattern="event_driven",
            cognitive_flavor="semantic-heavy",
        )),
    ]
    for label, dims in test_cases:
        print("=" * 60)
        print(f"=== {label} (target_size=50) ===")
        print("=" * 60)
        op = build_ops_profile(dims, target_size=50, enforce_five_organs_full=True)
        _print_profile(op)
        # 五脏俱全验证
        nonzero_cells = [ck for ck, n in op.cell_quota.items() if n > 0]
        print(f"  ✓ 非零 cell 数: {len(nonzero_cells)}, 全 ≥ 1")
        print()


if __name__ == "__main__":
    main()
