"""
pipeline.run_pipeline_v5 — V5 端到端入口(reverse-driven data synthesis)。

V5 关键流程变化 vs V3:
  - ★ Stage B 提前到 Stage A' 之前(B 先算 cell_quota,A' 才能反推)
  - ★ Stage A' V5 (cell_driven_seed_curate) 接 cell_quota → 按 cell 反推 Seed 需求
  - ★ Stage C V5 (synthesize_corpus_v5) 接 cell_requirements → skeleton 强制从 whitelist 挑 conflict
  - Stage D 沿用 V3.1(三轴算子 quota 真消费版),cfg 可切换 V4

详见 docs/anchors/redesign_v5.md 第 3 节。

跑法:
  cd memory_bench_factory
  ./venv/bin/python -m pipeline.run_pipeline_v5

输出:
  output/v5_smoke_office_extended.json   ← 完整 V3+V5 标签
  output/v5_smoke_office_oflat.json      ← OfficeMem 兼容版
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from .schema import (
    Document, RawScenarioInput, RefinedScenarioSpec, Benchmark,
    save_benchmark, save_benchmark_officemem_compat, compute_stats,
    validate_benchmark,
)
from .stage_a_dimensions import infer_dimensions
from .stage_b_ops_profile import build_ops_profile, explain_profile
from .stage_a_seed_curator_v5 import cell_driven_seed_curate
from .stage_c_corpus_synthesis_v5 import synthesize_corpus_v5
from .stage_d_question_synthesis import synthesize_questions_for_chains
from .cell_requirements_table import build_cell_requirements


OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"


@dataclass
class PipelineConfigV5:
    """V5 pipeline 配置。"""
    # Stage 0
    use_clarifier: bool = False
    # Stage A
    use_llm_for_dims: bool = False
    # Stage A' V5 cell-driven curator
    only_priority_cells_for_curate: Optional[set] = None
    max_new_seeds_per_cell: int = 1
    # Stage B
    target_size: int = 12
    enforce_five_organs_full: bool = False
    # Stage C V5
    n_chains: int = 1
    n_periods_per_chain: int = 2
    n_docs_per_period: int = 1
    # 输出
    benchmark_id: str = "v5_smoke"
    out_dir: Path = field(default_factory=lambda: OUTPUT_DIR)


def run_pipeline_v5(
    raw_input: Optional[RawScenarioInput] = None,
    refined_spec: Optional[RefinedScenarioSpec] = None,
    cfg: Optional[PipelineConfigV5] = None,
) -> Benchmark:
    """V5 端到端入口。

    数据流:Stage 0 → A → B → A' V5 → C V5 → D V3.1 → E
    """
    cfg = cfg or PipelineConfigV5()
    print(f"[run_pipeline_v5] cfg = {cfg}")

    # ── Stage 0 ──────────────────────────────────────────
    if refined_spec is None:
        if raw_input is None:
            raise ValueError("必须给 raw_input 或 refined_spec 之一")
        if cfg.use_clarifier:
            from .stage_0_refinement import refine_scenario
            print("\n[Stage 0] Clarifier 启动...")
            refined_spec = refine_scenario(raw_input, interactive=False)
        else:
            print("\n[Stage 0] (跳过)直接 wrap raw_input 为 RefinedScenarioSpec")
            refined_spec = RefinedScenarioSpec(
                name="direct_input",
                description_refined=raw_input.description,
                corpus_samples=raw_input.corpus_samples,
                target_size=cfg.target_size,
            )
    print(f"  refined_spec.name = {refined_spec.name}")

    # ── Stage A ──────────────────────────────────────────
    print("\n[Stage A] 维度推断...")
    dims = infer_dimensions(refined_spec, use_llm=cfg.use_llm_for_dims)
    print(f"  I={dims.I_ingest_channels}, S={dims.S_memory_subject}, "
          f"V={dims.V_perspective}, T={dims.T_temporal_pattern}")

    # ── ★ Stage B 提前 ───────────────────────────────────
    print("\n[Stage B] ★ cell_quota 配额计划(提前到 A' 之前,V5 关键流程改动)...")
    profile = build_ops_profile(
        dims, target_size=cfg.target_size,
        enforce_five_organs_full=cfg.enforce_five_organs_full,
    )
    print(f"  matched: {profile.profile_name}")
    print(f"  cell_quota sum={sum(profile.cell_quota.values())}, "
          f"非零 cells={len([k for k,v in profile.cell_quota.items() if v>0])}")
    top_cells = sorted(profile.cell_quota.items(), key=lambda x: -x[1])[:5]
    print(f"  top cells: {top_cells}")

    # ── ★ Stage A' V5 (cell-driven) ──────────────────────
    print("\n[Stage A' V5] ★ cell-driven Seed Curator(接 cell_quota 反推 Seed 需求)...")
    expanded_pool, coverage_report = cell_driven_seed_curate(
        refined_spec.corpus_samples, profile, dims,
        max_new_seeds_per_cell=cfg.max_new_seeds_per_cell,
        only_priority_cells=cfg.only_priority_cells_for_curate,
        verbose=True,
    )
    print(f"  expanded: {len(refined_spec.corpus_samples)} → {len(expanded_pool)} seeds")
    print(f"  bias_score_cells: {coverage_report.bias_score_cells:.3f}")

    # ── ★ 构建 cell_requirements(供 Stage C V5) ────────
    cell_requirements = build_cell_requirements(profile)
    print(f"\n[★ cell_requirements] {len(cell_requirements)} 条已构建,准备喂给 Stage C V5")

    # ── ★ Stage C V5 ────────────────────────────────────
    print(f"\n[Stage C V5] ★ Corpus 合成(skeleton 接 cell_requirements,whitelist 引导)...")
    chains = synthesize_corpus_v5(
        expanded_pool, dims, profile, cell_requirements,
        n_chains=cfg.n_chains,
        n_periods_per_chain=cfg.n_periods_per_chain,
        n_docs_per_period=cfg.n_docs_per_period,
        verbose=True,
    )
    if not chains:
        raise RuntimeError("Stage C V5 没产出任何 chain!")
    print(f"  ✓ {len(chains)} chains, "
          f"total docs = {sum(len(p.ingest_docs) for c in chains for p in c.periods)}")

    # ── Stage D V3.1(三轴 quota 真消费,沿用) ──────────
    print(f"\n[Stage D V3.1] 按 cell_quota 签发题目(三轴算子真消费)...")
    chains = synthesize_questions_for_chains(chains, profile, verbose=True)
    total_qs = sum(len(c.qas) for c in chains)
    print(f"  ✓ 总题数 = {total_qs}")

    # ── Stage E 打包 + 双 wrapper ────────────────────────
    print(f"\n[Stage E] 打包 + 双 wrapper 输出...")
    bm = Benchmark(
        benchmark_id=cfg.benchmark_id,
        scenario_name=refined_spec.name,
        scenario_spec_snapshot={
            "description": refined_spec.description_refined,
            "corpus_doc_ids": [d.doc_id for d in refined_spec.corpus_samples],
            "expanded_seed_count": len(expanded_pool),
            "v5_coverage_report": {
                "bias_score_cells": coverage_report.bias_score_cells,
                "missing_cells": coverage_report.missing_cells,
                "per_cell_coverage": coverage_report.per_cell_coverage,
            },
            "perspective": refined_spec.perspective,
            "subject_type": refined_spec.subject_type,
            "temporal_pattern": refined_spec.temporal_pattern,
            "v5_pipeline": "Stage B 提前 + Stage A'/C V5 反向驱动",
        },
        dimensions=dims,
        ops_profile=profile,
        chains=chains,
    )
    # 清掉 _skeleton(嵌套结构 + 不需要进 JSON)
    for c in bm.chains:
        for p in c.periods:
            if isinstance(p.state, dict) and "_skeleton" in p.state:
                p.state = {k: v for k, v in p.state.items() if k != "_skeleton"}
    bm.statistics = compute_stats(bm)

    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    ext_path = cfg.out_dir / f"{cfg.benchmark_id}_extended.json"
    oflat_path = cfg.out_dir / f"{cfg.benchmark_id}_oflat.json"
    save_benchmark(bm, str(ext_path))
    save_benchmark_officemem_compat(bm, str(oflat_path))
    print(f"  ✓ 扩展版: {ext_path} ({ext_path.stat().st_size // 1024} KB)")
    print(f"  ✓ 兼容版: {oflat_path} ({oflat_path.stat().st_size // 1024} KB)")

    # ── Validate ─────────────────────────────────────────
    print(f"\n[Validate]")
    problems = validate_benchmark(bm)
    if not problems:
        print(f"  ✓ 通过 (0 问题)")
    else:
        print(f"  ⚠ {len(problems)} 个问题:")
        for p in problems[:8]:
            print(f"    - {p}")

    # ── Quota vs Actual ──────────────────────────────────
    print(f"\n[Quota vs Actual]")
    diff = bm.statistics.quota_vs_actual
    perfect = sum(1 for d in diff.values() if d.get("delta") == 0)
    print(f"  完美匹配 cell: {perfect}/{len(diff)}")

    return bm


def main():
    """V5 端到端 smoke test:office_engineering 场景。"""
    refined = RefinedScenarioSpec(
        name="office_engineering_v5",
        description_refined="AI 工程团队的项目周报和技术方案,业务线 leader 视角",
        corpus_samples=[
            Document(doc_id="d1", title="AI 工程周报 W17",
                     content="本周 P0 缺陷率 20%(2/10),Oncall 数量 10。负责人:张三。",
                     metadata={"date": "2025-04-17", "doc_type": "周报", "author": "张三"}),
            Document(doc_id="d2", title="AI 工程周报 W18",
                     content="本周 P0 缺陷率 17%。Oncall 数量 9。负责人:张三。",
                     metadata={"date": "2025-04-24", "doc_type": "周报", "author": "张三"}),
            Document(doc_id="d3", title="AI 工程周报 W19",
                     content="本周 P0 缺陷率 14%。Oncall 数量 8。负责人变更为李四。",
                     metadata={"date": "2025-05-01", "doc_type": "周报", "author": "李四"}),
        ],
        perspective="cross_line_leader",
        subject_type="project",
        temporal_pattern="weekly",
        target_size=10,
    )
    cfg = PipelineConfigV5(
        use_clarifier=False,
        use_llm_for_dims=False,
        only_priority_cells_for_curate={("KU", "M3")},  # 只对命门 cell 合成 seed
        max_new_seeds_per_cell=1,
        target_size=10,
        enforce_five_organs_full=False,
        n_chains=1,
        n_periods_per_chain=2,
        n_docs_per_period=1,
        benchmark_id="v5_smoke_office",
    )
    bm = run_pipeline_v5(refined_spec=refined, cfg=cfg)

    print("\n" + "=" * 60)
    print("=== ✓ V5 端到端完成 ===")
    print("=" * 60)
    print(f"benchmark_id: {bm.benchmark_id}")
    print(f"total: {bm.statistics.total_questions}")
    print(f"by_capability: {bm.statistics.by_capability}")
    print(f"by_failmode: {bm.statistics.by_failmode}")
    print(f"by_cell: {bm.statistics.by_cell}")


if __name__ == "__main__":
    main()
