"""
pipeline.stage_a_seed_curator_v5 — Stage A' V5: cell-driven Seed Curator。

★ V5 reverse-driven data synthesis 的核心入口。
从 cell_quota 反推 corpus 需求,确保每个 cell 都有"弹药库"(supporting seeds)。

输入: 5 篇 seed + cell_quota + dims
输出: ExpandedSeedPool + SeedCoverageReportV5

算法(参 docs/anchors/redesign_v5.md 第 7 节):
  1. build_cell_requirements(profile) — cell_quota → CellRequirement 列表
  2. classify_seed_cell_support — LLM 给每篇 seed 标"支持哪些 cell"
  3. 聚合 per-cell coverage,找 missing cells
  4. 对 missing cells 合成新 seed(用 field_whitelist 强反常识对)

跑法:
  cd memory_bench_factory
  ./venv/bin/python -m pipeline.stage_a_seed_curator_v5
"""
from __future__ import annotations
import json
import sys
import uuid
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config

from .schema import (
    Document, ScenarioDimensions, CellRequirement,
    SeedCoverageReportV5, OpsProfileV3, cell_key,
)
from .cell_requirements_table import (
    FIELD_WHITELIST_REGISTRY, CELL_TO_REQUIREMENTS, build_cell_requirements,
)


# ─────────────────────────────────────────────────────────────────────────────
# Step 2: LLM 分类 — 给一篇 seed 标"支持哪些 cell"
# ─────────────────────────────────────────────────────────────────────────────

CLASSIFY_SYSTEM = """你是 corpus 分析专家。给定一篇真实文档(seed)和一批 cell 需求,\
你要判断这篇 seed 能为哪些 cell 提供"弹药"(支持下游基于此 seed 生成该 cell 类型的题)。

【判断准则】
- 一篇 seed 支持 cell C,当且仅当:
  - C 的 required_field_types 在 seed 中能找到对应字段/结构;且
  - C 的 field_whitelist(如有)中至少一类字段在 seed 中已出现或可被注入
- 例(参考):
  - cell=(KU, M3) 要 strong_reverence_conflict → seed 必须含 LLM 训练分布高频字段
    (公司总部/CEO/创立年份/首都/语言/币种 等)可被注入反常识值
  - cell=(IE, M5) 要 format_polymorphic → seed 必须含数值/百分比/单位等多格式字段
  - cell=(ABS, None) 要 explicit_unmentioned → seed 必须有明确缺失的字段领域

【输出严格 JSON】(不要 markdown 包裹)
{
  "supported_cells": [["KU","M3"], ["IE","none"], ...],
  "rationale": "一句话依据,引用 seed 里的具体字段"
}"""


def _format_cells_for_prompt(reqs: list) -> str:
    lines = []
    for r in reqs:
        whitelist_keys = [w["field_type"] for w in r.field_whitelist]
        lines.append(
            f"- cell ({r.cap}, {r.fm or 'None'}): "
            f"required={r.required_field_types}, whitelist={whitelist_keys}"
        )
    return "\n".join(lines)


def classify_seed_cell_support(
    seed: Document,
    requirements: list,
) -> list:
    """LLM 判定一篇 seed 支持哪些 cell。返回 list[(cap, fm)]."""
    cells_desc = _format_cells_for_prompt(requirements)
    user_msg = (
        f"【seed 内容】\n"
        f"title: {seed.title}\n"
        f"metadata: {json.dumps(seed.metadata, ensure_ascii=False)}\n"
        f"content (前 1500 字):\n{(seed.content or '')[:1500]}\n\n"
        f"【cell 需求列表】\n{cells_desc}\n\n"
        f"判断这篇 seed 支持哪些 cell,严格 JSON 输出。"
    )
    msgs = [
        {"role": "system", "content": CLASSIFY_SYSTEM},
        {"role": "user", "content": user_msg},
    ]
    try:
        data = config.chat_json(msgs, temperature=0.2, max_tokens=2048)
    except Exception as e:
        print(f"[curator] 分类 seed={seed.doc_id} LLM 异常: {e}")
        return []
    supported_raw = data.get("supported_cells", [])
    out = []
    for c in supported_raw:
        if isinstance(c, list) and len(c) >= 2:
            cap = c[0]
            fm = c[1]
            if fm in ("none", "None", "null", None):
                fm = None
            out.append((cap, fm))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Step 5: LLM 合成新 seed — 必须用 field_whitelist 的强反常识对
# ─────────────────────────────────────────────────────────────────────────────

SYNTHESIZE_SYSTEM = """你是 corpus 合成专家。给定:
- 一组已有的真实 seed(示范风格)
- 一个【目标 cell】需求(本次必须支持)
- 该 cell 的【字段白名单】(强反常识字段示例对)
- 场景维度(I/S/V)

你要合成【1 篇新 seed 文档】,严格满足:
1. 文档风格与已有 seed 一致(同一公司/项目/团队/语境),实体连续
2. 文档内容包含【至少 1 个白名单字段】的【injected_value】(反常识值)
3. 文档要【自然提及】该字段(不能硬塞),为下游基于此出 cell 题提供弹药
4. metadata 含 date / author / doc_type(对齐已有 seed 的格式)
5. 内容长度 300-700 字

【硬性约束】
- 不要用弱反差对(Minor/Critical, 生产/开发, True/False)
- 用 white-list 的 injected_examples 中的具体反常识值(如 Apple→Pyongyang,Tesla→Reykjavik)
- 让反常识值出现得自然(用"合规要求"/"重组公告"/"内部调整"等剧情包装)

【输出严格 JSON】
{
  "doc_id": "synth_v5_<short_uuid>",
  "title": "...",
  "content": "...(300-700 字)",
  "metadata": {"date": "YYYY-MM-DD", "author": "...", "doc_type": "..."},
  "supports_cell": ["KU", "M3"],
  "used_whitelist_entry": {
    "field_type": "company_hq_city",
    "entity": "Apple",
    "default_value": "Cupertino",
    "injected_value": "Pyongyang"
  }
}"""


def _summarize_existing_seeds(seeds: list, max_per: int = 250) -> str:
    lines = []
    for i, s in enumerate(seeds[:5]):
        lines.append(
            f"[seed {i}] doc_type={(s.metadata or {}).get('doc_type','?')} "
            f"author={(s.metadata or {}).get('author','?')} title='{s.title}'\n"
            f"  preview: {(s.content or '')[:max_per]}..."
        )
    return "\n".join(lines)


def _format_whitelist_for_prompt(field_whitelist: list) -> str:
    lines = []
    for w in field_whitelist:
        lines.append(
            f"- {w['field_type']}: {w.get('description', '')}\n"
            f"  default 示例(常识): {w.get('default_examples', [])[:3]}\n"
            f"  injected 示例(反常识,从此处挑): {w.get('injected_examples', [])[:3]}"
        )
    return "\n".join(lines)


def synthesize_seed_for_cell(
    target_cell: tuple,
    requirement: CellRequirement,
    existing_seeds: list,
    dims: ScenarioDimensions,
) -> Optional[Document]:
    """对一个 missing cell,合成一篇新 seed。"""
    if not requirement.field_whitelist:
        # 白名单为空 → 不强反常识 cell(如 ABS/普通 IE)— 用 fallback prompt
        return _synthesize_minimal_seed(target_cell, requirement, existing_seeds, dims)

    samples_str = _summarize_existing_seeds(existing_seeds)
    whitelist_str = _format_whitelist_for_prompt(requirement.field_whitelist)
    user_msg = (
        f"【目标 cell】({target_cell[0]}, {target_cell[1] or 'None'})\n"
        f"required_field_types: {requirement.required_field_types}\n\n"
        f"【字段白名单(必从此处挑反常识值)】\n{whitelist_str}\n\n"
        f"【已有 seed 风格示例】\n{samples_str}\n\n"
        f"【场景维度】I={dims.I_ingest_channels}, "
        f"S={dims.S_memory_subject}, V={dims.V_perspective}\n\n"
        f"请合成 1 篇新 seed,严格 JSON 输出。"
    )
    msgs = [
        {"role": "system", "content": SYNTHESIZE_SYSTEM},
        {"role": "user", "content": user_msg},
    ]
    try:
        data = config.chat_json(msgs, temperature=0.7, max_tokens=8192)
    except Exception as e:
        print(f"[curator] 合成 cell={target_cell} seed LLM 异常: {e}")
        return None

    doc_id = str(data.get("doc_id", f"synth_v5_{uuid.uuid4().hex[:6]}"))
    return Document(
        doc_id=doc_id,
        title=str(data.get("title", "")),
        content=str(data.get("content", "")),
        metadata={
            **(data.get("metadata", {}) or {}),
            "synthetic": True,
            "v5_target_cell": list(target_cell),
            "v5_used_whitelist": data.get("used_whitelist_entry", {}),
        },
    )


def _synthesize_minimal_seed(
    target_cell: tuple,
    requirement: CellRequirement,
    existing_seeds: list,
    dims: ScenarioDimensions,
) -> Optional[Document]:
    """无 whitelist 的 cell(如 ABS/IE_none),合成 minimal seed 覆盖结构。"""
    samples_str = _summarize_existing_seeds(existing_seeds)
    user_msg = (
        f"【目标 cell】({target_cell[0]}, {target_cell[1] or 'None'})\n"
        f"required_field_types: {requirement.required_field_types}\n\n"
        f"【已有 seed 风格示例】\n{samples_str}\n\n"
        f"【场景维度】I={dims.I_ingest_channels}, "
        f"S={dims.S_memory_subject}, V={dims.V_perspective}\n\n"
        f"该 cell 无强反常识字段需求。请合成 1 篇新 seed,自然覆盖 "
        f"required_field_types 中的特征(例如 explicit_unmentioned_field 意味着"
        f"seed 应有明确缺失的字段领域)。严格 JSON 输出。"
    )
    msgs = [
        {"role": "system", "content": SYNTHESIZE_SYSTEM},
        {"role": "user", "content": user_msg},
    ]
    try:
        data = config.chat_json(msgs, temperature=0.7, max_tokens=8192)
    except Exception as e:
        print(f"[curator] minimal seed cell={target_cell} LLM 异常: {e}")
        return None

    doc_id = str(data.get("doc_id", f"synth_v5_{uuid.uuid4().hex[:6]}"))
    return Document(
        doc_id=doc_id,
        title=str(data.get("title", "")),
        content=str(data.get("content", "")),
        metadata={
            **(data.get("metadata", {}) or {}),
            "synthetic": True,
            "v5_target_cell": list(target_cell),
            "v5_used_whitelist": {},
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────────────────────────────────────

def cell_driven_seed_curate(
    seeds: list,
    profile: OpsProfileV3,
    dims: ScenarioDimensions,
    max_new_seeds_per_cell: int = 1,   # 默认每 cell 最多合成 1 篇(控成本)
    only_priority_cells: Optional[set] = None,   # 只对高优先级 cell 合成(避免爆 token)
    verbose: bool = True,
) -> tuple:
    """V5 Stage A' 主入口。

    Returns:
        (expanded_seeds, SeedCoverageReportV5)
    """
    # ── Step 1: cell_quota → CellRequirement
    requirements = build_cell_requirements(profile)
    if verbose:
        print(f"[curator] 从 cell_quota 反推出 {len(requirements)} 个 CellRequirement")
        for r in requirements:
            print(f"   ({r.cap}, {r.fm or 'none'}): quota={r.quota}, "
                  f"need ≥{r.min_supporting_seeds} 支持 seed, "
                  f"whitelist={[w['field_type'] for w in r.field_whitelist][:3]}")

    # ── Step 2-3: LLM 给每篇 seed 标支持的 cell
    if verbose:
        print(f"\n[curator] 用 LLM 分类 {len(seeds)} 篇 seed 的 cell 支持情况...")
    per_seed_support = []
    for seed in seeds:
        supported = classify_seed_cell_support(seed, requirements)
        per_seed_support.append({
            "doc_id": seed.doc_id,
            "supported_cells": supported,
        })
        if verbose:
            cells_str = ", ".join(f"({c[0]},{c[1] or 'none'})" for c in supported)
            print(f"   {seed.doc_id}: 支持 {len(supported)} 个 cell ({cells_str})")

    # ── Step 4: 聚合 per-cell coverage,找 missing cells
    per_cell_coverage = {}
    missing_cells = []
    for req in requirements:
        target = (req.cap, req.fm)
        supporting_ids = [
            s["doc_id"] for s in per_seed_support
            if any(c[0] == target[0] and c[1] == target[1] for c in s["supported_cells"])
        ]
        cov = {
            "required": req.min_supporting_seeds,
            "actual": len(supporting_ids),
            "supporting_seed_ids": supporting_ids,
        }
        per_cell_coverage[cell_key(req.cap, req.fm)] = cov
        if len(supporting_ids) < req.min_supporting_seeds:
            missing_cells.append(target)

    if verbose:
        print(f"\n[curator] missing_cells {len(missing_cells)}/{len(requirements)}: "
              f"{[(c[0], c[1] or 'none') for c in missing_cells]}")

    # ── Step 5: 合成新 seed
    new_seeds = []
    cells_to_synth = missing_cells
    if only_priority_cells is not None:
        cells_to_synth = [c for c in missing_cells if c in only_priority_cells]
        if verbose:
            print(f"[curator] only_priority_cells 过滤后,要合成 {len(cells_to_synth)} 个 cell")

    for cell in cells_to_synth:
        req = next(r for r in requirements if (r.cap, r.fm) == cell)
        cov_now = per_cell_coverage[cell_key(*cell)]["actual"]
        need = min(max_new_seeds_per_cell, req.min_supporting_seeds - cov_now)
        for _ in range(need):
            if verbose:
                print(f"   合成 cell={cell}...", end=" ", flush=True)
            new_seed = synthesize_seed_for_cell(cell, req, seeds, dims)
            if new_seed:
                new_seeds.append(new_seed)
                if verbose:
                    wl = new_seed.metadata.get("v5_used_whitelist", {})
                    inj = wl.get("injected_value", "?")
                    print(f"✓ doc_id={new_seed.doc_id} injected={inj}")
            else:
                if verbose:
                    print("✗ 失败")

    expanded = seeds + new_seeds

    # bias score (cell-level)
    if requirements:
        cov_ratios = []
        for r in requirements:
            actual_now = per_cell_coverage[cell_key(r.cap, r.fm)]["actual"]
            # 加上新合成的算上
            new_for_this = sum(1 for s in new_seeds
                               if tuple(s.metadata.get("v5_target_cell", [])) == (r.cap, r.fm))
            ratio = min(1.0, (actual_now + new_for_this) / max(1, r.min_supporting_seeds))
            cov_ratios.append(ratio)
        bias_cells = 1.0 - (sum(cov_ratios) / len(cov_ratios))
    else:
        bias_cells = 0.0

    report = SeedCoverageReportV5(
        per_cell_coverage=per_cell_coverage,
        missing_cells=[list(c) for c in missing_cells],
        bias_score_dims=0.0,
        bias_score_cells=bias_cells,
        per_seed_cell_support=per_seed_support,
    )

    if verbose:
        print(f"\n[curator] ✓ 完成。{len(seeds)} → {len(expanded)} seeds")
        print(f"          bias_score_cells={bias_cells:.3f}")
        print(f"          (0=每 cell 都有充足支持,1=严重偏向少数 cell)")

    return expanded, report


# ─────────────────────────────────────────────────────────────────────────────
# 测试入口
# ─────────────────────────────────────────────────────────────────────────────

def main():
    """W1.5 V5 T4 测试:5 篇 office 周报 + cell_quota → ExpandedSeedPool。

    重点观察 (KU, M3) 命门 cell 是否被合成 seed 覆盖,且
    injected_value 是否真的来自 white-list(Apple→Pyongyang 这种强反常识)。
    """
    seeds = [
        Document(doc_id="d1", title="AI 工程周报 W17",
                 content="本周 P0 缺陷率 20%(2/10),Oncall 数量 10。负责人:张三。"
                         "本周代码合并 PR 12 个,主要修复推理链路超时。",
                 metadata={"date": "2025-04-17", "doc_type": "周报", "author": "张三"}),
        Document(doc_id="d2", title="AI 工程周报 W18",
                 content="本周 P0 缺陷率 17%。Oncall 数量 9。负责人:张三。"
                         "新增 GPT-4o 接口适配,延迟 P99 = 180ms。",
                 metadata={"date": "2025-04-24", "doc_type": "周报", "author": "张三"}),
        Document(doc_id="d3", title="AI 工程周报 W19",
                 content="本周 P0 缺陷率 14%。Oncall 数量 8。负责人变更为李四。"
                         "完成新索引架构 prototype 验证。",
                 metadata={"date": "2025-05-01", "doc_type": "周报", "author": "李四"}),
    ]

    dims = ScenarioDimensions(
        I_ingest_channels=["weekly_report"],
        S_memory_subject="project",
        V_perspective="cross_line_leader",
        T_temporal_pattern="weekly",
        cognitive_flavor="mixed",
    )

    profile = OpsProfileV3(
        capability_quota={"IE": 3, "KU": 3, "MR": 2, "TR": 1, "ABS": 1},
        failmode_quota={"M3": 4, "none": 6},
        formation_quota={}, evolution_quota={}, query_quota={},
        cell_quota={
            "KU__M3": 3,         # ★ 命门 cell
            "IE__M5": 2,
            "IE__none": 1,
            "MR__none": 2,
            "TR__M3": 1,
            "ABS__none": 1,
            "KU__none": 0,       # 0 配额不进 requirements
        },
        target_size=10, profile_name="test_v5",
    )

    # 只对高优先级 cell 合成 seed(避免测试时爆 token)
    priority = {("KU", "M3"), ("ABS", None)}

    expanded, report = cell_driven_seed_curate(
        seeds, profile, dims,
        max_new_seeds_per_cell=1,
        only_priority_cells=priority,
    )

    print("\n" + "=" * 60)
    print("=== ExpandedSeedPool ===")
    print("=" * 60)
    for s in expanded:
        synth = " [SYNTHETIC-V5]" if s.metadata.get("synthetic") else ""
        target = s.metadata.get("v5_target_cell")
        print(f"\n  {s.doc_id}: {s.title}{synth}")
        if target:
            print(f"    target_cell: {target}")
            wl = s.metadata.get("v5_used_whitelist", {})
            if wl:
                print(f"    ★ used_whitelist:")
                print(f"        field_type:     {wl.get('field_type')}")
                print(f"        entity:         {wl.get('entity')}")
                print(f"        default_value:  {wl.get('default_value')}")
                print(f"        injected_value: {wl.get('injected_value')}")
            print(f"    content_preview: {s.content[:250]}...")

    print("\n" + "=" * 60)
    print("=== Coverage Report V5 ===")
    print("=" * 60)
    print(f"bias_score_cells: {report.bias_score_cells:.3f}  "
          f"(0=均衡, 1=偏)")
    print(f"missing_cells (before synth): {report.missing_cells}")
    print(f"\nper_cell_coverage:")
    for ck, cov in sorted(report.per_cell_coverage.items()):
        print(f"  {ck}: req={cov['required']} actual={cov['actual']}  "
              f"supporting={cov['supporting_seed_ids']}")


if __name__ == "__main__":
    main()
