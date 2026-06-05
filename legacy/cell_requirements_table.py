"""
pipeline.cell_requirements_table — V6: cell → 题型 hint(白名单已删)。

V6 改动(redesign_v6.md §4):
  - 删 FIELD_WHITELIST_REGISTRY(白名单方向被 dry-run 证伪:反常识要么自洽反噬,要么元识别)
  - CELL_TO_REQUIREMENTS 只保留 required_field_types 作题型 hint,删 field_whitelist_keys
  - build_cell_requirements 不再填 CellRequirement.field_whitelist

失败模式不再靠白名单强反常识注入,改靠 Stage C V6 的 corpus 内【自然信号竞争】
(如 M3 = 旧值在多 period 高频出现,新值低频 → 系统倾向答信号更强的旧值)。
详见 redesign_v6.md §2 转变 2。
"""
from __future__ import annotations

from .schema import CellRequirement


# 兼容空壳:V5 历史文件(stage_a_seed_curator_v5 / stage_c_corpus_synthesis_v5)
# 可能 import 此名,保留空 dict 避免 import 崩(已 deprecated,V6 不用)。
FIELD_WHITELIST_REGISTRY: dict = {}


# ─────────────────────────────────────────────────────────────────────────────
# CELL_TO_REQUIREMENTS — (cap, fm) → 题型原料 hint(V6:无白名单)
# required_field_types 仅作 Stage C/D 的题型提示,不绑定具体字段值
# ─────────────────────────────────────────────────────────────────────────────

CELL_TO_REQUIREMENTS = {
    # ── KU 系列 ──────────────────────────────────────────
    ("KU", "M3"): {
        "required_field_types": ["multi_period_field_update_with_signal_competition"],
        "fallback_cell": ("KU", None),
        "notes": "★ V6 信号竞争:旧值在多 period 高频出现,新值低频出现 → "
                 "问'最新值'时系统倾向答信号更强的旧值(conflict defaulting)",
    },
    ("KU", "M2"): {
        "required_field_types": ["hierarchical_summary_layer"],
        "fallback_cell": ("KU", None),
        "notes": "需 corpus 含层级摘要结构(F4 配合)",
    },
    ("KU", None): {
        "required_field_types": ["multi_period_field_update"],
        "fallback_cell": None,
    },

    # ── TR 系列 ──────────────────────────────────────────
    ("TR", "M3"): {
        "required_field_types": ["temporal_signal_competition"],
        "fallback_cell": ("TR", None),
        "notes": "时序信号竞争:某时间点状态被高频引用,真实最新状态低频",
    },
    ("TR", "M5"): {
        "required_field_types": ["temporal_format_polymorphic"],
        "fallback_cell": ("TR", None),
    },
    ("TR", None): {
        "required_field_types": ["multi_period_state_chain"],
        "fallback_cell": None,
    },

    # ── IE 系列 ──────────────────────────────────────────
    ("IE", "M5"): {
        "required_field_types": ["format_polymorphic_field"],
        "fallback_cell": ("IE", None),
        "notes": "格式多样,等价集 ≥7 变体",
    },
    ("IE", "M4"): {
        "required_field_types": ["paraphrasable_verbatim"],
        "fallback_cell": ("IE", None),
        "notes": "原文逐字 + 同义改写干扰项",
    },
    ("IE", "M1"): {
        "required_field_types": ["apparent_multihop_actual_singlehop"],
        "fallback_cell": ("IE", None),
        "notes": "看似 2 hop 实际 1 hop,引诱 R3 过度拆解",
    },
    ("IE", None): {
        "required_field_types": ["clean_field_value"],
        "fallback_cell": None,
    },

    # ── MR 系列 ──────────────────────────────────────────
    ("MR", "M2"): {
        "required_field_types": ["multi_doc_aggregation_with_summary"],
        "fallback_cell": ("MR", None),
        "notes": "跨 period 聚合 + 高层摘要语义偏离",
    },
    ("MR", "M1"): {
        "required_field_types": ["apparent_multihop_in_MR"],
        "fallback_cell": ("MR", None),
    },
    ("MR", "M4"): {
        "required_field_types": ["multi_doc_paraphrasable"],
        "fallback_cell": ("MR", None),
    },
    ("MR", None): {
        "required_field_types": ["multi_period_aggregation"],
        "fallback_cell": None,
    },

    # ── ABS ─────────────────────────────────────────────
    ("ABS", None): {
        "required_field_types": ["explicit_unmentioned_field"],
        "fallback_cell": None,
        "notes": "corpus 明确不出现的字段(如个人邮箱);canonical 必填拒答 sentinel",
    },

    ("LRU", None): {
        "required_field_types": ["long_range_global_context"],
        "fallback_cell": None,
    },
}


def build_cell_requirements(profile) -> list:
    """从 OpsProfileV3.cell_quota 反推出 CellRequirement 列表。

    V6:不再填 field_whitelist(白名单已弃)。只保留 required_field_types 作题型 hint。
    """
    requirements = []
    for ck, quota in profile.cell_quota.items():
        if quota <= 0:
            continue
        cap, fm = ck.split("__", 1)
        fm = None if fm == "none" else fm
        cell = (cap, fm)
        spec = CELL_TO_REQUIREMENTS.get(cell, {
            "required_field_types": [],
            "fallback_cell": None,
        })
        req = CellRequirement(
            cap=cap,
            fm=fm,
            quota=quota,
            required_field_types=list(spec.get("required_field_types", [])),
            min_supporting_seeds=max(1, quota // 3),
            fallback_cell=spec.get("fallback_cell"),
        )
        requirements.append(req)
    return requirements


def main():
    """自检:用 mock profile 跑 build_cell_requirements。"""
    from .schema import OpsProfileV3

    mock = OpsProfileV3(
        capability_quota={"IE": 5, "KU": 3, "MR": 3, "TR": 2, "ABS": 2},
        failmode_quota={"M1": 2, "M2": 2, "M3": 4, "M4": 1, "M5": 1, "none": 5},
        formation_quota={}, evolution_quota={}, query_quota={},
        cell_quota={"KU__M3": 3, "IE__M5": 2, "IE__M4": 1, "IE__M1": 1,
                    "MR__M2": 2, "TR__M3": 1, "ABS__none": 2},
        target_size=15, profile_name="test",
    )
    reqs = build_cell_requirements(mock)
    print(f"=== {len(reqs)} 个 CellRequirement(V6:无白名单)===")
    for r in reqs:
        print(f"  ({r.cap}, {r.fm or 'none'}): quota={r.quota}, "
              f"required_field_types={r.required_field_types}, "
              f"min_seeds={r.min_supporting_seeds}, fallback={r.fallback_cell}")
    # 重点看 KU_M3 的 V6 信号竞争 hint
    ku_m3 = next((r for r in reqs if (r.cap, r.fm) == ("KU", "M3")), None)
    if ku_m3:
        print(f"\n=== ★ (KU, M3) V6 信号竞争 hint ===")
        print(f"  required_field_types: {ku_m3.required_field_types}")
        print(f"  (失败模式靠 corpus 内信号竞争,不靠白名单反常识)")


if __name__ == "__main__":
    main()
