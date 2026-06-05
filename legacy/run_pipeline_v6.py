"""
pipeline.run_pipeline_v6 — V6 端到端入口(信号竞争 + failmode 预测)。

V6 数据流(比 V5 更简,删了白名单就删了 cell-driven seed bias 那一站):

  Stage 0 (Clarifier,可选)
    → Stage A   维度推断 (I/S/V/T)
    → Stage B   cell_quota 配额计划 + cell_requirements
    → Stage C V6  信号竞争 corpus(skeleton 自然演进,旧值高频/新值低频)
    → Stage D V6  信号竞争 M3 题 + failmode 预测(非 M3 复用 V4 质量层)
    → Stage E   打包 + 双 wrapper 输出 + validate

vs V5 的关键变化(redesign_v6.md §2/§4):
  - ✗ 删 cell_driven_seed_curate(白名单 seed bias):few-shot 样本直接喂 Stage C
    (更忠实"few-shot 输入"本意,不再偷偷扩成大池)
  - ✓ Stage C V5 → C V6:whitelist 反常识注入 → corpus 内自然信号竞争
  - ✓ Stage D V3.1/V4 → D V6:M3 由 signal_competition 字段确定性出题 + 预测标签

跑法:
  cd memory_bench_factory
  ./venv/bin/python -m pipeline.run_pipeline_v6

输出:
  output/v6_smoke_office_extended.json   ← 完整 V3+V6 标签
  output/v6_smoke_office_oflat.json      ← OfficeMem 兼容版(供 W2 评测)
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from .schema import (
    Document, RawScenarioInput, RefinedScenarioSpec, Benchmark,
    save_benchmark, save_benchmark_officemem_compat, compute_stats,
    validate_benchmark,
)
from .stage_a_dimensions import infer_dimensions
from .stage_b_ops_profile import build_ops_profile
from .cell_requirements_table import build_cell_requirements
from .stage_c_corpus_synthesis_v6 import synthesize_corpus_v6
from .stage_d_v6_question_synthesis import synthesize_questions_for_chains_v6


OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"


@dataclass
class PipelineConfigV6:
    """V6 pipeline 配置。"""
    # Stage 0
    use_clarifier: bool = False
    # Stage A
    use_llm_for_dims: bool = False
    # Stage B
    target_size: int = 12
    enforce_five_organs_full: bool = False
    # Stage C V6
    n_chains: int = 1
    n_periods_per_chain: int = 5   # ★ 信号竞争需 ≥3 period(旧值多 period 高频)
    n_docs_per_period: int = 1
    # Stage D V6
    max_retries: int = 1
    # 输出
    benchmark_id: str = "v6_smoke"
    out_dir: Path = field(default_factory=lambda: OUTPUT_DIR)


def run_pipeline_v6(
    raw_input: Optional[RawScenarioInput] = None,
    refined_spec: Optional[RefinedScenarioSpec] = None,
    cfg: Optional[PipelineConfigV6] = None,
) -> Benchmark:
    """V6 端到端入口。数据流:Stage 0 → A → B → C V6 → D V6 → E。"""
    cfg = cfg or PipelineConfigV6()
    print(f"[run_pipeline_v6] cfg = {cfg}")

    # ── Stage 0 ──────────────────────────────────────────
    if refined_spec is None:
        if raw_input is None:
            raise ValueError("必须给 raw_input 或 refined_spec 之一")
        if cfg.use_clarifier:
            from .stage_0_refinement import refine_scenario
            print("\n[Stage 0] Clarifier 启动...")
            refined_spec = refine_scenario(raw_input, interactive=False)
        else:
            print("\n[Stage 0] (跳过)直接 wrap raw_input")
            refined_spec = RefinedScenarioSpec(
                name="direct_input",
                description_refined=raw_input.description,
                corpus_samples=raw_input.corpus_samples,
                target_size=cfg.target_size,
            )
    print(f"  refined_spec.name = {refined_spec.name}")
    print(f"  few-shot 样本数 = {len(refined_spec.corpus_samples)} (V6 不扩池,直接喂 Stage C)")

    # ── Stage A ──────────────────────────────────────────
    print("\n[Stage A] 维度推断...")
    dims = infer_dimensions(refined_spec, use_llm=cfg.use_llm_for_dims)
    print(f"  I={dims.I_ingest_channels}, S={dims.S_memory_subject}, "
          f"V={dims.V_perspective}, T={dims.T_temporal_pattern}")

    # ── Stage B(cell_quota + cell_requirements)──────────
    print("\n[Stage B] cell_quota 配额计划...")
    profile = build_ops_profile(
        dims, target_size=cfg.target_size,
        enforce_five_organs_full=cfg.enforce_five_organs_full,
    )
    print(f"  matched: {profile.profile_name}")
    print(f"  cell_quota sum={sum(profile.cell_quota.values())}, "
          f"非零 cells={len([k for k,v in profile.cell_quota.items() if v>0])}")
    top_cells = sorted(profile.cell_quota.items(), key=lambda x: -x[1])[:5]
    print(f"  top cells: {top_cells}")

    cell_requirements = build_cell_requirements(profile)
    m3_cells = [(r.cap, r.fm, r.quota) for r in cell_requirements if r.fm == "M3"]
    print(f"  cell_requirements: {len(cell_requirements)} 条, "
          f"M3 cells(信号竞争材料): {m3_cells}")

    # ── Stage C V6(信号竞争 corpus)─────────────────────
    print(f"\n[Stage C V6] ★ 信号竞争 corpus 合成(自然演进,旧值高频/新值低频)...")
    chains = synthesize_corpus_v6(
        refined_spec.corpus_samples, dims, cell_requirements,
        n_chains=cfg.n_chains,
        n_periods_per_chain=cfg.n_periods_per_chain,
        n_docs_per_period=cfg.n_docs_per_period,
        verbose=True,
    )
    if not chains:
        raise RuntimeError("Stage C V6 没产出任何 chain!")
    print(f"  ✓ {len(chains)} chains, "
          f"total docs = {sum(len(p.ingest_docs) for c in chains for p in c.periods)}")

    # ── Stage D V6(信号竞争 M3 + failmode 预测)─────────
    print(f"\n[Stage D V6] ★ M3 信号竞争出题 + 非 M3 走 V4 质量层...")
    chains = synthesize_questions_for_chains_v6(
        chains, profile, verbose=True, max_retries=cfg.max_retries,
    )
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
            "few_shot_count": len(refined_spec.corpus_samples),
            "perspective": refined_spec.perspective,
            "subject_type": refined_spec.subject_type,
            "temporal_pattern": refined_spec.temporal_pattern,
            "v6_pipeline": "信号竞争(删白名单)+ failmode 预测(待 W2 实测)",
        },
        dimensions=dims,
        ops_profile=profile,
        chains=chains,
    )

    # 抽取信号竞争证据进 snapshot(W2 验证标准 A 时用)
    m3_evidence = []
    for c in chains:
        for q in c.qas:
            if q.failmode == "M3" and q.failmode_evidence:
                m3_evidence.append({
                    "qid": q.qid, "field": q.field_name,
                    **q.failmode_evidence,
                })
    bm.scenario_spec_snapshot["m3_signal_competition_evidence"] = m3_evidence
    print(f"  M3 信号竞争证据条目: {len(m3_evidence)}")

    # 清掉 _skeleton(嵌套 + 不进 JSON)
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
    """V6 端到端 smoke test:office_engineering 场景。"""
    refined = RefinedScenarioSpec(
        name="office_engineering_v6",
        description_refined=(
            "我是业务线 leader,每周审阅 AI 工程团队迭代进展。"
            "关键字段:P0 缺陷率、Oncall 数量、负责人、客户对接人、SLA 达标率、版本。"
        ),
        corpus_samples=[
            Document(doc_id="d1", title="AI 工程周报 W17",
                     content="本周 P0 缺陷率 20%(2/10),Oncall 数量 10。负责人:张三。",
                     metadata={"date": "2025-04-17", "doc_type": "周报", "author": "张三"}),
            Document(doc_id="d2", title="AI 工程周报 W18",
                     content="本周 P0 缺陷率 17%。Oncall 数量 9。负责人:张三。",
                     metadata={"date": "2025-04-24", "doc_type": "周报", "author": "张三"}),
            Document(doc_id="d3", title="AI 工程周报 W19",
                     content="本周 P0 缺陷率 14%。Oncall 数量 8。负责人:张三。",
                     metadata={"date": "2025-05-01", "doc_type": "周报", "author": "张三"}),
        ],
        perspective="cross_line_leader",
        subject_type="project",
        temporal_pattern="weekly",
        target_size=12,
    )
    cfg = PipelineConfigV6(
        use_clarifier=False,
        use_llm_for_dims=False,
        target_size=12,
        enforce_five_organs_full=False,
        n_chains=1,
        n_periods_per_chain=5,
        n_docs_per_period=1,
        benchmark_id="v6_smoke_office",
    )
    bm = run_pipeline_v6(refined_spec=refined, cfg=cfg)

    print("\n" + "=" * 60)
    print("=== ✓ V6 端到端完成 ===")
    print("=" * 60)
    print(f"benchmark_id: {bm.benchmark_id}")
    print(f"total: {bm.statistics.total_questions}")
    print(f"by_capability: {bm.statistics.by_capability}")
    print(f"by_failmode: {bm.statistics.by_failmode}")
    print(f"by_cell: {bm.statistics.by_cell}")

    # M3 信号竞争证据快照
    ev = bm.scenario_spec_snapshot.get("m3_signal_competition_evidence", [])
    print(f"\n=== ★ M3 信号竞争证据({len(ev)} 条,供 W2 验证标准 A)===")
    for e in ev:
        print(f"  [{e['qid']}] field={e.get('field')}: "
              f"旧'{e.get('old_value')}'({e.get('old_freq')}x) vs "
              f"新'{e.get('new_value')}'({e.get('new_freq')}x), "
              f"ratio={e.get('signal_ratio')}")


if __name__ == "__main__":
    main()
