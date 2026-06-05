"""
pipeline.run_pipeline_v7 — V7 端到端入口(structure-first 贯彻到出题端 + 多文档密度)。

V7 数据流:Stage 0 → A 维度 → B 配额 → C V7(多文档密度 corpus) → D V7(程序化出题) → E

vs V6 的关键变化(redesign_v7.md):
  - Stage C V6 → C V7:每 period 单文档 → 多篇异质文档(字段分散),打破单文档天花板
  - Stage D V6 → D V7:非 M3 题让 LLM 现场算 → 全部从 state 代码派生(gt 保证正确、无元信息)
  - Stage B 配额语义修(T16):cell_quota = benchmark 总预算,在 n_chains 间【分配】而非复制
    (V6 是每 chain 复制整套 profile → target_size 被静默 ×n_chains)
  - 算子轴(T16):Stage D V7 用 observed 自然赋值(_natural_ops),不再事后改写凑配额

跑法:cd memory_bench_factory && ./venv/bin/python -m pipeline.run_pipeline_v7
输出:output/v7_smoke_*_extended.json / _oflat.json
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from .schema import (
    Document, RawScenarioInput, RefinedScenarioSpec, Benchmark,
    save_benchmark, save_benchmark_officemem_compat, compute_stats, validate_benchmark,
)
from .stage_a_dimensions import infer_dimensions
from .stage_b_ops_profile import build_ops_profile
from .cell_requirements_table import build_cell_requirements
from .stage_c_corpus_synthesis_v7 import synthesize_corpus_v7
from .stage_d_v7_question_synthesis import (
    synthesize_questions_for_chains_v7, _get_periods_state, _to_number,
)

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"


@dataclass
class PipelineConfigV7:
    use_llm_for_dims: bool = False
    target_size: int = 12          # benchmark 总预算(在 n_chains 间分配)
    enforce_five_organs_full: bool = False
    n_chains: int = 1
    n_periods_per_chain: int = 5
    benchmark_id: str = "v7_smoke"
    out_dir: Path = field(default_factory=lambda: OUTPUT_DIR)


def run_pipeline_v7(refined_spec: RefinedScenarioSpec,
                    cfg: Optional[PipelineConfigV7] = None) -> Benchmark:
    cfg = cfg or PipelineConfigV7()
    print(f"[run_pipeline_v7] cfg = {cfg}")

    # ── Stage A ──
    print("\n[Stage A] 维度推断...")
    dims = infer_dimensions(refined_spec, use_llm=cfg.use_llm_for_dims)
    print(f"  I={dims.I_ingest_channels}, V={dims.V_perspective}")

    # ── Stage B(★T16:总预算在 n_chains 间分配,不再每 chain 复制)──
    per_chain_target = (max(5, cfg.target_size // cfg.n_chains)
                        if cfg.n_chains > 1 else cfg.target_size)
    print(f"\n[Stage B] 配额:总预算 {cfg.target_size} / {cfg.n_chains} chain "
          f"= 每 chain {per_chain_target} 题")
    profile = build_ops_profile(dims, target_size=per_chain_target,
                                enforce_five_organs_full=cfg.enforce_five_organs_full)
    cell_reqs = build_cell_requirements(profile)
    print(f"  per-chain cell_quota sum={sum(profile.cell_quota.values())}, "
          f"M3 cells: {[(r.cap, r.fm) for r in cell_reqs if r.fm=='M3']}")

    # ── Stage C V7(多文档密度)──
    print(f"\n[Stage C V7] ★ 多文档密度 corpus(每 period 多篇异质文档)...")
    chains = synthesize_corpus_v7(refined_spec.corpus_samples, dims, cell_reqs,
                                  n_chains=cfg.n_chains,
                                  n_periods_per_chain=cfg.n_periods_per_chain,
                                  verbose=True)
    if not chains:
        raise RuntimeError("Stage C V7 没产出任何 chain!")
    total_docs = sum(len(p.ingest_docs) for c in chains for p in c.periods)
    total_periods = sum(len(c.periods) for c in chains)
    print(f"  ✓ {len(chains)} chains, {total_docs} 篇文档 "
          f"(密度 ×{total_docs/max(1,total_periods):.1f} 篇/period)")

    # ── Stage D V7(程序化出题,answer 全代码派生)──
    print(f"\n[Stage D V7] ★ 程序化出题(answer 代码派生,gt 保证正确)...")
    chains = synthesize_questions_for_chains_v7(chains, profile, verbose=True)
    total_qs = sum(len(c.qas) for c in chains)
    print(f"  ✓ 总题数 = {total_qs}")

    # ── Stage E 打包 ──
    print(f"\n[Stage E] 打包 + 输出...")
    bm = Benchmark(
        benchmark_id=cfg.benchmark_id,
        scenario_name=refined_spec.name,
        scenario_spec_snapshot={
            "description": refined_spec.description_refined,
            "v7_pipeline": "structure-first 出题(代码派生 gt)+ 多文档密度 + 种子扰动",
            "total_budget": cfg.target_size, "n_chains": cfg.n_chains,
            "per_chain_quota": dict(profile.cell_quota),
            "doc_density": round(total_docs / max(1, total_periods), 2),
        },
        dimensions=dims, ops_profile=profile, chains=chains)
    # 清 _skeleton(D 已用完)
    for c in bm.chains:
        for p in c.periods:
            if isinstance(p.state, dict) and "_skeleton" in p.state:
                p.state = {k: v for k, v in p.state.items() if k != "_skeleton"}
    bm.statistics = compute_stats(bm)

    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    ext = cfg.out_dir / f"{cfg.benchmark_id}_extended.json"
    save_benchmark(bm, str(ext))
    save_benchmark_officemem_compat(bm, str(cfg.out_dir / f"{cfg.benchmark_id}_oflat.json"))
    print(f"  ✓ {ext} ({ext.stat().st_size // 1024} KB)")

    # ── 全貌验证(V7 重点:gt 正确 / 无占位 / 多文档密度)──
    print(f"\n[Validate]")
    problems = validate_benchmark(bm)
    print(f"  validate: {'✓ 0 问题' if not problems else f'⚠ {len(problems)} 问题'}")
    for p in problems[:5]:
        print(f"    - {p}")

    print(f"\n[全貌体检]")
    # 1. 占位符/空 gold(V7 应为 0)
    PLACEHOLDERS = ("", "none", "not_asked", "不适用", "n/a")
    empty = 0
    for c in bm.chains:
        for q in c.qas:
            for v in q.answer_canonical_by_period.values():
                if str(v).strip().lower() in PLACEHOLDERS:
                    empty += 1
                    break
    print(f"  空/占位 gold 题数: {empty} (V7 程序化派生应为 0)")
    # 2. MR gt 代码复核
    bad_mr = 0
    for c in bm.chains:
        sk = None  # _skeleton 已清,改用 period.state 重算
        ps = [(p.period_idx, p.date, p.state) for p in c.periods]
        for q in c.qas:
            if q.capability == "MR" and q.field_name:
                nums = [(pi, _to_number(st.get(q.field_name))) for pi, _, st in ps
                        if _to_number(st.get(q.field_name)) is not None]
                if nums:
                    # 题的 gt 周期应是 argmax 或 argmin
                    gt_p = next(iter(q.answer_canonical_by_period.keys()), None)
                    amax = str(max(nums, key=lambda x: x[1])[0])
                    amin = str(min(nums, key=lambda x: x[1])[0])
                    if gt_p not in (amax, amin):
                        bad_mr += 1
    print(f"  MR gt 与代码 argmax/min 不符: {bad_mr} (应为 0)")
    print(f"  by_capability: {bm.statistics.by_capability}")
    print(f"  by_failmode: {bm.statistics.by_failmode}")
    return bm


def main():
    spec = RefinedScenarioSpec(
        name="office_engineering_v7",
        description_refined=("我是业务线 leader,每周审阅 AI 工程团队迭代进展。"
                             "关键字段:P0 缺陷率、Oncall 数量、负责人、客户对接人、版本、阶段。"),
        corpus_samples=[
            Document(doc_id="d1", title="AI 工程周报 W17",
                     content="本周 P0 缺陷率 20%。Oncall 10。负责人:张三。阶段:开发。",
                     metadata={"date": "2025-04-17", "doc_type": "周报"}),
            Document(doc_id="d2", title="AI 工程周报 W18",
                     content="本周 P0 缺陷率 17%。Oncall 9。负责人:张三。阶段:开发。",
                     metadata={"date": "2025-04-24", "doc_type": "周报"}),
        ],
        perspective="cross_line_leader", subject_type="project",
        temporal_pattern="weekly", target_size=12)
    cfg = PipelineConfigV7(target_size=12, n_chains=1, n_periods_per_chain=5,
                           benchmark_id="v7_smoke_office")
    bm = run_pipeline_v7(spec, cfg)
    print("\n" + "=" * 56 + "\n=== ✓ V7 端到端完成 ===\n" + "=" * 56)
    print(f"total: {bm.statistics.total_questions}, by_cell: {bm.statistics.by_cell}")


if __name__ == "__main__":
    main()
