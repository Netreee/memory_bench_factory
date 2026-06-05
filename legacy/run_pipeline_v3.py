"""
pipeline.run_pipeline_v3 — W1.5 V3 端到端入口。

链路:Stage 0 (Clarifier) → A (维度) → A' (seed expansion) → B (配额)
     → C (corpus 合成) → D (题目签发) → E (双 wrapper 输出)

可配置每个 Stage 是否启用 LLM(LLM 全开会消耗 ~30 次 LLM 调用)。
默认 smoke test 配置:跳过 Stage 0/A',Stage A 用启发式,Stage C/D 必须用 LLM。

详见 docs/anchors/redesign_v3.md 全文。

跑法:
  cd memory_bench_factory
  ./venv/bin/python -m pipeline.run_pipeline_v3
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
    validate_benchmark, cell_key,
)
from .stage_a_dimensions import infer_dimensions
from .stage_a_seed_expansion import expand_scenario_seeds
from .stage_b_ops_profile import build_ops_profile, explain_profile
from .stage_c_corpus_synthesis import synthesize_corpus
from .stage_d_question_synthesis import synthesize_questions_for_chains


OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"


@dataclass
class PipelineConfig:
    """V3 pipeline 配置(可控 LLM 开销)。"""
    # Stage 0 Clarifier
    use_clarifier: bool = False           # smoke test 默认跳过,直接给 RefinedScenarioSpec
    # Stage A
    use_llm_for_dims: bool = False        # smoke test 用启发式省 1 次 LLM
    # Stage A' Seed Expansion
    use_seed_expansion: bool = False      # smoke test 跳过(假设 samples 已覆盖)
    max_new_seeds: int = 3
    # Stage B
    target_size: int = 15
    enforce_five_organs_full: bool = True
    # Stage C Corpus 合成
    n_chains: int = 1
    n_periods_per_chain: int = 3
    n_docs_per_period: int = 1
    # 输出
    benchmark_id: str = "v3_smoke"
    out_dir: Path = field(default_factory=lambda: OUTPUT_DIR)


def run_pipeline(
    raw_input: Optional[RawScenarioInput] = None,
    refined_spec: Optional[RefinedScenarioSpec] = None,
    cfg: Optional[PipelineConfig] = None,
) -> Benchmark:
    """端到端入口。

    必须提供 raw_input(走 Stage 0 Clarifier)或 refined_spec(跳过 Stage 0)其一。
    """
    cfg = cfg or PipelineConfig()
    print(f"[run_pipeline_v3] cfg = {cfg}")

    # ── Stage 0 ────────────────────────────────────────────
    if refined_spec is None:
        if raw_input is None:
            raise ValueError("必须给 raw_input 或 refined_spec 之一")
        if cfg.use_clarifier:
            print("\n[Stage 0] Clarifier 启动...")
            from .stage_0_refinement import refine_scenario
            refined_spec = refine_scenario(raw_input, interactive=False)
        else:
            # 跳过 Clarifier:直接 wrap raw_input
            print("\n[Stage 0] (跳过)直接把 raw_input 当 refined_spec")
            refined_spec = RefinedScenarioSpec(
                name="direct_input",
                description_refined=raw_input.description,
                corpus_samples=raw_input.corpus_samples,
                target_size=cfg.target_size,
            )
    print(f"  refined_spec.name = {refined_spec.name}")

    # ── Stage A ────────────────────────────────────────────
    print("\n[Stage A] 维度推断...")
    dims = infer_dimensions(refined_spec, use_llm=cfg.use_llm_for_dims)
    print(f"  I={dims.I_ingest_channels}, S={dims.S_memory_subject}, "
          f"V={dims.V_perspective}, T={dims.T_temporal_pattern}, "
          f"flavor={dims.cognitive_flavor}")

    # ── Stage A' Seed Expansion ───────────────────────────
    expanded_pool = list(refined_spec.corpus_samples)
    coverage_report = None
    if cfg.use_seed_expansion:
        print("\n[Stage A'] Seed Bias 检测 + 多样化...")
        expanded_pool, coverage_report = expand_scenario_seeds(
            refined_spec, max_new_seeds=cfg.max_new_seeds,
        )
        print(f"  bias_score = {coverage_report.bias_score:.3f}")
        print(f"  expanded {len(refined_spec.corpus_samples)} → {len(expanded_pool)} seeds")

    # ── Stage B ────────────────────────────────────────────
    print("\n[Stage B] 配额计划...")
    profile = build_ops_profile(
        dims, target_size=cfg.target_size,
        enforce_five_organs_full=cfg.enforce_five_organs_full,
    )
    print(f"  matched: {profile.profile_name}")
    print(f"  cell_quota: {len(profile.cell_quota)} cells, "
          f"sum={sum(profile.cell_quota.values())}")
    top_cells = sorted(profile.cell_quota.items(), key=lambda x: -x[1])[:5]
    print(f"  top cells: {top_cells}")

    # ── Stage C Corpus 合成 ───────────────────────────────
    print(f"\n[Stage C] Corpus 合成 ({cfg.n_chains} chain × "
          f"{cfg.n_periods_per_chain} period × {cfg.n_docs_per_period} doc)...")
    chains = synthesize_corpus(
        expanded_pool, dims, profile,
        n_chains=cfg.n_chains,
        n_periods_per_chain=cfg.n_periods_per_chain,
        n_docs_per_period=cfg.n_docs_per_period,
        verbose=True,
    )
    if not chains:
        raise RuntimeError("Stage C 没产出任何 chain!")
    print(f"  ✓ 合成 {len(chains)} chains, "
          f"total docs = {sum(len(p.ingest_docs) for c in chains for p in c.periods)}")

    # ── Stage D 题目签发 ──────────────────────────────────
    print(f"\n[Stage D] 按 cell_quota 签发题目...")
    chains = synthesize_questions_for_chains(chains, profile, verbose=True)
    total_qs = sum(len(c.qas) for c in chains)
    print(f"  ✓ 总题数 = {total_qs}")

    # ── Stage E 打包 + 双 wrapper 输出 ─────────────────────
    print(f"\n[Stage E] 打包 + 双 wrapper 输出...")
    bm = Benchmark(
        benchmark_id=cfg.benchmark_id,
        scenario_name=refined_spec.name,
        scenario_spec_snapshot={
            "description": refined_spec.description_refined,
            "corpus_doc_ids": [d.doc_id for d in refined_spec.corpus_samples],
            "expanded_seed_count": len(expanded_pool),
            "perspective": refined_spec.perspective,
            "subject_type": refined_spec.subject_type,
            "temporal_pattern": refined_spec.temporal_pattern,
            "cognitive_flavor_hint": refined_spec.cognitive_flavor_hint,
            "open_ended_fields": refined_spec.open_ended_fields,
            "clarification_rounds": len(refined_spec.clarification_history),
        },
        dimensions=dims,
        ops_profile=profile,
        chains=chains,
    )
    # 清掉 _skeleton(嵌套结构 + 重量级,不需要进 JSON)
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

    # ── 校验 ──────────────────────────────────────────────
    print(f"\n[Validate] V3 硬约束校验...")
    problems = validate_benchmark(bm)
    if not problems:
        print(f"  ✓ 通过(0 个问题)")
    else:
        print(f"  ⚠ 发现 {len(problems)} 个问题:")
        for p in problems[:10]:
            print(f"    - {p}")

    # ── 配额 vs 实际 ─────────────────────────────────────
    print(f"\n[Quota vs Actual]")
    diff = bm.statistics.quota_vs_actual
    perfect = sum(1 for d in diff.values() if d.get("delta") == 0)
    print(f"  完美匹配 cell: {perfect}/{len(diff)}")
    drift_cells = [(k, v) for k, v in diff.items() if v.get("delta") != 0]
    for k, d in drift_cells[:5]:
        print(f"    drift: {k} expected={d['expected']} actual={d['actual']} delta={d['delta']}")

    return bm


def main():
    """smoke test:office_engineering 场景,15 题。"""
    raw = RawScenarioInput(
        description="AI 工程团队的项目周报与技术方案,业务线 leader 视角",
        corpus_samples=[
            Document(doc_id="d1", title="AI 工程周报 W17",
                     content="本周 P0 缺陷率 20%(2/10),Oncall 数量 10。负责人:张三。",
                     metadata={"date": "2025-04-17", "doc_type": "周报"}),
            Document(doc_id="d2", title="AI 工程周报 W18",
                     content="本周 P0 缺陷率 17%。Oncall 数量 9。负责人:张三。",
                     metadata={"date": "2025-04-24", "doc_type": "周报"}),
            Document(doc_id="d3", title="AI 工程周报 W19",
                     content="本周 P0 缺陷率 14%。Oncall 数量 8。负责人变更为李四。",
                     metadata={"date": "2025-05-01", "doc_type": "周报"}),
        ],
    )
    # 跳过 Clarifier,直接给 refined_spec
    refined = RefinedScenarioSpec(
        name="office_engineering_smoke",
        description_refined=raw.description,
        corpus_samples=raw.corpus_samples,
        perspective="cross_line_leader",
        subject_type="project",
        temporal_pattern="weekly",
        target_size=15,
        cognitive_flavor_hint="mixed",
    )
    cfg = PipelineConfig(
        use_clarifier=False,
        use_llm_for_dims=False,
        use_seed_expansion=False,
        target_size=6,                       # 缩范围:6 题确保 ~5-8 LLM 调用
        enforce_five_organs_full=False,      # 不强制五脏俱全,只保留 top 配额 cell
        n_chains=1,
        n_periods_per_chain=2,               # 缩到 2 periods
        n_docs_per_period=1,
        benchmark_id="v3_smoke_office_v2",
    )
    bm = run_pipeline(refined_spec=refined, cfg=cfg)

    # 最终汇报
    print("\n" + "=" * 60)
    print("=== W1.5 V3 端到端 smoke test 完成 ===")
    print("=" * 60)
    print(f"benchmark_id: {bm.benchmark_id}")
    print(f"total_questions: {bm.statistics.total_questions}")
    print(f"by_capability: {bm.statistics.by_capability}")
    print(f"by_failmode: {bm.statistics.by_failmode}")
    print(f"by_cell: {bm.statistics.by_cell}")


if __name__ == "__main__":
    main()
