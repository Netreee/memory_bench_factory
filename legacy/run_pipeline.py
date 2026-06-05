"""
pipeline.run_pipeline — W1 端到端 pipeline 主入口(Stage A → Stage E)。

输入:ScenarioSpec(只给 description + corpus_samples,不给 QA examples)
输出:三重兼容 benchmark JSON
  ① 兼容 OfficeMem onpolicy_benchmark.json schema(chain/period/qas 三层)
  ② 携带毕设的算子标签(F/E/Q)+ 失败模式标签(M1-M4)+ task_type(AR/CR/TTL/LR)
  ③ 答案升级为等价表达集合(避免 M5 格式错位)

跑法:
  cd memory_bench_factory
  ./venv/bin/python -m pipeline.run_pipeline

输出位置:
  output/benchmark_w1_office.json
"""
from __future__ import annotations
import os

from .schema import ScenarioSpec, Benchmark, compute_stats, save_benchmark
from .scenario_loader import build_scenario_office
from .stage_a_dimensions import infer_dimensions
from .stage_b_ops_profile import infer_ops_profile, explain_profile
from .stage_c_corpus import load_chains_from_officemem
from .stage_d_question_forger import forge_chains_with_labels


OUTPUT_DIR = "output"
DEFAULT_OUTPUT = os.path.join(OUTPUT_DIR, "benchmark_w1_office.json")


def run(
    spec: ScenarioSpec,
    output_path: str = DEFAULT_OUTPUT,
    verbose: bool = True,
) -> Benchmark:
    """端到端跑 5 个 Stage,落盘 benchmark JSON。"""
    if verbose:
        print(f"[run] === Pipeline 启动:scenario = {spec.name} ===")
        print()

    # ── Stage A:从 description + corpus_samples 推断 4 维 ──
    if verbose:
        print("[Stage A] 维度推断 (I, S, V, T)")
    dims = infer_dimensions(spec)
    if verbose:
        print(f"  I = {dims.I_ingest_channels}")
        print(f"  S = {dims.S_memory_subject}")
        print(f"  V = {dims.V_perspective}")
        print(f"  T = {dims.T_temporal_pattern}")
        print()

    # ── Stage B:从 (I,S,V,T) 推断 O 维度配额 ──
    if verbose:
        print("[Stage B] O 维度配额推断")
    target = spec.target_size or 30
    profile = infer_ops_profile(dims, target_size=target)
    expl = explain_profile(dims)
    if verbose:
        print(f"  matched profile: {expl['matched_profile']}")
        print(f"  failmode_weights: {profile.failmode_weights}")
        print(f"  task_type_quota:  {profile.task_type_quota}")
        print(f"  formation_quota:  {profile.formation_quota}")
        print(f"  evolution_quota:  {profile.evolution_quota}")
        print(f"  query_quota:      {profile.query_quota}")
        print()

    # ── Stage C:加载 chain + period 结构(W1:从 OfficeMem 复用) ──
    if verbose:
        print("[Stage C] 加载 chain + period 结构")
    chains = load_chains_from_officemem()
    n_periods = sum(c.period_count for c in chains)
    n_docs = sum(len(p.ingest_docs) for c in chains for p in c.periods)
    if verbose:
        print(f"  {len(chains)} chains | {n_periods} periods | {n_docs} ingest_docs")
        print()

    # ── Stage D:签发带完整标签的 qa ──
    if verbose:
        print("[Stage D] 签发带标签的 qa")
    chains = forge_chains_with_labels(chains)
    n_qa = sum(len(c.qas) for c in chains)
    if verbose:
        print(f"  {n_qa} questions across {len(chains)} chains")
        print()

    # ── Stage E:打包 + 落盘 ──
    if verbose:
        print("[Stage E] 打包 + 落盘")
    bm = Benchmark(
        benchmark_id=f"w1_{spec.name}",
        scenario_name=spec.name,
        dimensions=dims,
        ops_profile=profile,
        chains=chains,
    )
    bm.statistics = compute_stats(bm)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    save_benchmark(bm, output_path)

    if verbose:
        print(f"  ✓ 保存到 {output_path}")
        print()
        print("[run] === 统计分布 ===")
        s = bm.statistics
        print(f"  total: {s.total_questions}")
        print(f"  by task_type:       {s.by_task_type}")
        print(f"  by formation_op:    {s.by_formation_op}")
        print(f"  by evolution_op:    {s.by_evolution_op}")
        print(f"  by query_op:        {s.by_query_op}")
        print(f"  by target_failmode: {s.by_target_failmode}")

    return bm


def main():
    spec = build_scenario_office()
    run(spec)


if __name__ == "__main__":
    main()
