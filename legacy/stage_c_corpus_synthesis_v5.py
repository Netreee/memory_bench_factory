"""
pipeline.stage_c_corpus_synthesis_v5 — Stage C V5: skeleton 接 cell_requirements。

V5 关键改动 (vs V3):
  - skeleton prompt 接 cell_requirements + field_whitelist
  - 硬约束:type='conflict' 字段必须从 whitelist 挑(强反常识,如 Apple→Pyongyang)
  - 禁止弱反差(Minor/Critical, 生产/开发, True/False, X% vs X+5%)
  - period 文档合成复用 V3 的 synthesize_period_doc(不重写)

详见 docs/anchors/redesign_v5.md 第 8 节。

跑法:
  cd memory_bench_factory
  ./venv/bin/python -m pipeline.stage_c_corpus_synthesis_v5
"""
from __future__ import annotations
from pathlib import Path
from typing import Optional
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config

from .schema import (
    Document, Chain, Period, ScenarioDimensions, OpsProfileV3,
    CellRequirement, SeedCoverageReportV5,
)
# 复用 V3 的 period 文档合成
from .stage_c_corpus_synthesis import (
    synthesize_period_doc,
    _suggest_doc_type,
)


# ─────────────────────────────────────────────────────────────────────────────
# V5 强化的 skeleton prompt(接 cell_requirements 反向引导)
# ─────────────────────────────────────────────────────────────────────────────

SKELETON_SYSTEM_V5 = """你是 memory benchmark corpus 合成专家。\
为一个测试 chain 设计【结构骨架】(skeleton),作为后续生成文档和题目的 ground truth。

【V5 强化设计原则】
1. 每个 chain 围绕一个【主题】(项目/产品线/客户案例)
2. 设计 5-8 个【字段(fields)】,每个字段有类型:
   - "stable": 不变(用于 IE/AR 题)
   - "evolving": 跨 period 变化(用于 KU 普通题)
   - "conflict": ★ 必须从下面 field_whitelist 挑反常识对(用于 KU+M3 命门 cell)

3. ★【conflict 字段硬约束】(违反将被丢弃):
   a) 禁止弱反差对:Minor/Critical, 生产/开发, True/False, X% vs X+5%, 普通同类切换
   b) 必须从 field_whitelist 列表选(LLM 训练分布高频常识 vs 反常识对)
   c) 必须显式标 common_default(常识)+ injected_value(反常识)
   d) common_default 必须是 LLM 训练分布里该 entity 的高频值
   e) injected_value 必须明显反常识(如 Apple 总部从 Cupertino → Pyongyang)

4. 每个 period state 必须列出所有字段当前值
5. events 数组:对 conflict 字段,用【合规剧情/重组公告/政策升级/制裁清单/审计发现】等
   包装注入,让反常识值在 corpus 内部自洽 — 让被测系统读到这个反常识值时,
   感觉"corpus 在告诉我有这件事",而不是"corpus 显然在胡说"

【输出严格 JSON 格式】(不要 markdown 包裹)
{
  "chain_id": "chain_<short_id>",
  "chain_name": "...",
  "main_theme": "...",
  "fields": [
    {
      "name": "...",
      "type": "stable" | "evolving" | "conflict",
      "domain": "字段值取值领域",
      "common_default": "若 conflict, LLM 训练分布高频值(从 whitelist 选)",
      "injected_value": "若 conflict, 反常识值(从 whitelist 选)",
      "whitelist_source": "若 conflict, 引用 whitelist 中的 field_type 名"
    },
    ...
  ],
  "periods": [
    {"period_idx": 0, "date": "YYYY-MM-DD", "state": {"field_name": "value", ...}},
    ...
  ],
  "events": [
    {"period_idx": int, "type": "update"|"reveal"|"injection",
     "field": "...", "old_value": "...", "new_value": "...",
     "trigger": "事件简述(对 conflict 字段:用合规剧情包装)"},
    ...
  ]
}"""


def _format_cell_requirements_for_prompt(cell_requirements: list) -> str:
    """格式化 cell_requirements 概览给 LLM。"""
    if not cell_requirements:
        return "(无 cell_requirements,自由设计)"
    lines = []
    for req in cell_requirements:
        wl_keys = [w["field_type"] for w in req.field_whitelist]
        lines.append(
            f"- cell ({req.cap}, {req.fm or 'None'}) quota={req.quota}: "
            f"required={req.required_field_types}, "
            f"whitelist_types={wl_keys}"
        )
    return "\n".join(lines)


def _format_field_whitelist_for_prompt(cell_requirements: list,
                                        max_examples: int = 4) -> str:
    """格式化 conflict 字段白名单的具体 (entity, default, injected) 三元组。"""
    seen = set()
    lines = []
    for req in cell_requirements:
        for w in req.field_whitelist:
            ft = w.get("field_type")
            if ft in seen:
                continue
            seen.add(ft)
            defaults = w.get("default_examples", [])[:max_examples]
            injecteds = w.get("injected_examples", [])[:max_examples]
            lines.append(
                f"\n  ★ field_type: {ft}\n"
                f"    desc: {w.get('description', '')}\n"
                f"    default 示例(LLM 训练分布常识值): {defaults}\n"
                f"    injected 示例(反常识,必从此挑): {injecteds}"
            )
    return "\n".join(lines) if lines else "(无白名单字段类型)"


def _build_skeleton_user_prompt_v5(
    expanded_pool: list,
    dims: ScenarioDimensions,
    profile: OpsProfileV3,
    cell_requirements: list,
    n_periods: int,
    chain_idx: int,
) -> str:
    samples_summary = "\n".join(
        f"[doc {i}] doc_type={(d.metadata or {}).get('doc_type','?')} "
        f"date={(d.metadata or {}).get('date','?')} title='{d.title}'\n"
        f"  preview: {(d.content or '')[:250]}..."
        for i, d in enumerate(expanded_pool[:5])
    )
    cells_str = _format_cell_requirements_for_prompt(cell_requirements)
    whitelist_str = _format_field_whitelist_for_prompt(cell_requirements)

    # 统计 M3 cell 总配额(决定需要多少 conflict 字段)
    m3_quota = sum(req.quota for req in cell_requirements if req.fm == "M3")
    n_conflict_fields = max(1, min(3, m3_quota // 2 + 1))

    return (
        f"【场景描述】\n"
        f"I (ingest channels): {dims.I_ingest_channels}\n"
        f"S (memory subject): {dims.S_memory_subject}\n"
        f"V (perspective): {dims.V_perspective}\n"
        f"T (temporal pattern): {dims.T_temporal_pattern}\n"
        f"cognitive_flavor: {dims.cognitive_flavor}\n\n"
        f"【ExpandedSeedPool 摘要】({len(expanded_pool)} 篇)\n"
        f"{samples_summary}\n\n"
        f"【★ V5 cell_requirements(下游要出这些 cell 的题,fields 必须支持)】\n"
        f"{cells_str}\n\n"
        f"【★★ field_whitelist 详细(conflict 字段必从此挑)】\n"
        f"{whitelist_str}\n\n"
        f"【任务】\n"
        f"为 chain {chain_idx + 1} 设计 skeleton:\n"
        f"- {n_periods} 个 period\n"
        f"- 5-8 个 fields\n"
        f"- ★ 必须包含 [{n_conflict_fields}] 个 type='conflict' 字段(M3 cell 总配额={m3_quota})\n"
        f"- 每个 conflict 字段必须从上面 field_whitelist 挑,显式标 whitelist_source\n"
        f"- events 数组用合规剧情/重组公告/制裁清单等包装 conflict 注入,\n"
        f"  让反常识值在 corpus 内部【自圆其说】(被测系统读到时感觉是真事而非胡说)\n\n"
        f"严格 JSON 输出。"
    )


def synthesize_chain_skeleton_v5(
    expanded_pool: list,
    dims: ScenarioDimensions,
    profile: OpsProfileV3,
    cell_requirements: list,
    n_periods: int = 10,
    chain_idx: int = 0,
) -> Optional[dict]:
    """V5 主入口:LLM 生成接 cell_requirements 引导的 chain skeleton。"""
    msgs = [
        {"role": "system", "content": SKELETON_SYSTEM_V5},
        {"role": "user", "content": _build_skeleton_user_prompt_v5(
            expanded_pool, dims, profile, cell_requirements, n_periods, chain_idx,
        )},
    ]
    try:
        data = config.chat_json(msgs, temperature=0.5, max_tokens=8192)
    except Exception as e:
        print(f"[skeleton-v5] LLM 异常 chain={chain_idx}: {e}")
        return None
    return data


# ─────────────────────────────────────────────────────────────────────────────
# 主入口:synthesize_corpus_v5
# ─────────────────────────────────────────────────────────────────────────────

def synthesize_corpus_v5(
    expanded_pool: list,
    dimensions: ScenarioDimensions,
    profile: OpsProfileV3,
    cell_requirements: list,
    n_chains: int = 1,
    n_periods_per_chain: int = 10,
    n_docs_per_period: int = 1,
    verbose: bool = True,
) -> list:
    """V5 主入口:合成 corpus → list[Chain]。

    Stage C V5 关键变化:skeleton 接 cell_requirements 反向引导,
    conflict 字段强制从 field_whitelist 挑,杜绝 V3/V4 弱反差陷阱。
    """
    chains: list = []
    for chain_idx in range(n_chains):
        if verbose:
            print(f"\n[stage_c_v5] === Chain {chain_idx+1}/{n_chains} ===")
            print(f"[stage_c_v5] Step 1: V5 skeleton 合成(★ 接 cell_requirements)...")
        skeleton = synthesize_chain_skeleton_v5(
            expanded_pool, dimensions, profile, cell_requirements,
            n_periods=n_periods_per_chain, chain_idx=chain_idx,
        )
        if skeleton is None:
            print(f"[stage_c_v5] chain {chain_idx} skeleton 合成失败,跳过")
            continue
        if verbose:
            print(f"[stage_c_v5]   chain_name: {skeleton.get('chain_name')}")
            print(f"[stage_c_v5]   main_theme: {skeleton.get('main_theme')}")
            print(f"[stage_c_v5]   fields ({len(skeleton.get('fields', []))}):")
            for f in skeleton.get("fields", []):
                if f.get("type") == "conflict":
                    wl = f.get("whitelist_source", "?")
                    print(f"     - ★ {f.get('name')} [conflict] "
                          f"default='{f.get('common_default')}' "
                          f"injected='{f.get('injected_value')}' "
                          f"(whitelist: {wl})")
                else:
                    print(f"     - {f.get('name')} [{f.get('type')}]")
            print(f"[stage_c_v5]   events: {len(skeleton.get('events', []))}")

        # Step 2: 合成每个 period 文档(复用 V3)
        periods: list = []
        period_info_list = skeleton.get("periods", [])
        for period_idx in range(n_periods_per_chain):
            period_info = period_info_list[period_idx] if period_idx < len(period_info_list) else {}
            period_docs = []
            for doc_idx in range(n_docs_per_period):
                doc_type_hint = _suggest_doc_type(
                    dimensions.I_ingest_channels, period_idx + doc_idx
                )
                if verbose:
                    print(f"[stage_c_v5]   period {period_idx} doc {doc_idx+1}/"
                          f"{n_docs_per_period} ({doc_type_hint})...",
                          end=" ", flush=True)
                doc_dict = synthesize_period_doc(
                    skeleton, period_idx, expanded_pool, doc_type_hint
                )
                if doc_dict is None:
                    print("[失败]")
                    continue
                period_docs.append(doc_dict)
                if verbose:
                    print(f"len={len(doc_dict.get('content',''))}c")
            periods.append(Period(
                period_idx=period_idx,
                date=str(period_info.get("date", "")),
                timestamp=str(period_info.get("date", "")),
                ingest_docs=period_docs,
                state=period_info.get("state", {}),
            ))

        chain = Chain(
            chain_id=str(skeleton.get("chain_id", f"chain_v5_{chain_idx}")),
            chain_name=str(skeleton.get("chain_name", f"chain_v5_{chain_idx}")),
            cluster=skeleton.get("main_theme"),
            period_count=len(periods),
            periods=periods,
            qas=[],
        )
        # 把 skeleton 存到 first period state["_skeleton"],供 Stage D 用
        if periods:
            periods[0].state["_skeleton"] = skeleton
        chains.append(chain)
    return chains


def main():
    """V5 T5 测试:Stage A → B → A' V5 → C V5 链路。"""
    from .schema import RefinedScenarioSpec
    from .stage_a_dimensions import infer_dimensions
    from .stage_b_ops_profile import build_ops_profile
    from .stage_a_seed_curator_v5 import cell_driven_seed_curate
    from .cell_requirements_table import build_cell_requirements

    spec = RefinedScenarioSpec(
        name="office_v5_test",
        description_refined="AI 工程团队周报,业务线 leader 视角",
        corpus_samples=[
            Document(doc_id="d1", title="AI 工程周报 W17",
                     content="本周 P0 缺陷率 20%。负责人:张三。",
                     metadata={"date": "2025-04-17", "doc_type": "周报", "author": "张三"}),
            Document(doc_id="d2", title="AI 工程周报 W18",
                     content="本周 P0 缺陷率 17%。负责人:张三。",
                     metadata={"date": "2025-04-24", "doc_type": "周报", "author": "张三"}),
        ],
        perspective="cross_line_leader",
        subject_type="project",
        temporal_pattern="weekly",
        target_size=8,
    )

    print("=== Stage A ===")
    dims = infer_dimensions(spec, use_llm=False)

    print("\n=== Stage B (target=8) ===")
    profile = build_ops_profile(dims, target_size=8, enforce_five_organs_full=False)
    print(f"  cell_quota: {dict(sorted(profile.cell_quota.items(), key=lambda x: -x[1])[:6])}")

    cell_reqs = build_cell_requirements(profile)
    print(f"\n=== build_cell_requirements: {len(cell_reqs)} 条 ===")
    for r in cell_reqs[:5]:
        print(f"  ({r.cap}, {r.fm or 'none'}): "
              f"whitelist={[w['field_type'] for w in r.field_whitelist][:3]}")

    print("\n=== Stage C V5 (skeleton 接 cell_requirements)===")
    # 直接用现有 seed 不走 A' V5(节省 LLM 调用)
    chains = synthesize_corpus_v5(
        spec.corpus_samples, dims, profile, cell_reqs,
        n_chains=1, n_periods_per_chain=2, n_docs_per_period=1,
    )

    print(f"\n=== ✓ V5 Stage C 产出: {len(chains)} chains ===")
    for c in chains:
        skeleton = c.periods[0].state.get("_skeleton", {})
        conflict_fields = [f for f in skeleton.get("fields", []) if f.get("type") == "conflict"]
        print(f"\n  chain: {c.chain_name}")
        print(f"  ★★ conflict 字段 {len(conflict_fields)} 个 (V5 强反常识验证):")
        for f in conflict_fields:
            print(f"    - {f.get('name')} (whitelist={f.get('whitelist_source')})")
            print(f"        default:  '{f.get('common_default')}'")
            print(f"        injected: '{f.get('injected_value')}'")


if __name__ == "__main__":
    main()
