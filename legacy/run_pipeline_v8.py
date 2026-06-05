"""
pipeline.run_pipeline_v8 — V8 端到端(LLM 当主角,裁判在校验层)。

数据流(redesign_v8.md):
  0 Refine → A Dimensions(真LLM) → B CapabilityPlan → C FactGraph → D Corpus
    → E Answer-first 出题(LLM 主角) → F 三道校验闸 → G 打包

跑法:cd memory_bench_factory && ./venv/bin/python -m pipeline.run_pipeline_v8
输出:output/v8_smoke_office.json(含 spec/dims/plan/fact_graph/corpus/questions/reject_log/conflict_log)
"""
from __future__ import annotations
from dataclasses import asdict
from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from .schema import RefinedScenarioSpec, Document
from .stage_a_dimensions import infer_dimensions
from .stages_v8 import (
    stage_0_refine, stage_b_capability_plan, stage_c_fact_graph,
    stage_d_corpus, stage_e_answer_first, stage_f_validate,
)

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"


def run_pipeline_v8(description, corpus_samples, target_size=12, n_sessions=5,
                    benchmark_id="v8_smoke"):
    print(f"[run_pipeline_v8] target_size={target_size}, n_sessions={n_sessions}")

    print("\n[Stage 0] Refine(模糊场景 → 结构化 spec)...")
    spec = stage_0_refine(description, corpus_samples)
    print(f"  description_refined: {spec.get('description_refined')}")
    print(f"  key_fields: {[f.get('name') for f in spec.get('key_fields', [])]}")
    print(f"  subject={spec.get('subject')}, perspective={spec.get('perspective')}")

    print("\n[Stage A] Dimensions(★ 真 LLM)...")
    docs = [Document(doc_id=d.get("doc_id", f"d{i}"), title=d.get("title", ""),
                     content=d.get("content", ""), metadata=d.get("metadata", {}))
            for i, d in enumerate(corpus_samples)]
    refined = RefinedScenarioSpec(
        name=benchmark_id, description_refined=spec.get("description_refined", ""),
        corpus_samples=docs, subject_type=spec.get("subject"),
        perspective=spec.get("perspective"), target_size=target_size)
    dims = infer_dimensions(refined, use_llm=True)
    print(f"  I={dims.I_ingest_channels}, S={dims.S_memory_subject}, "
          f"V={dims.V_perspective}, T={dims.T_temporal_pattern} ({dims.inference_notes.get('source')})")

    print("\n[Stage B] CapabilityPlan(LLM 规划,取代硬编码 profile)...")
    plan = stage_b_capability_plan(spec, dims, target_size)
    print(f"  plan: {[(it.get('capability'), it.get('n')) for it in plan.get('items', [])]}")

    print("\n[Stage C] FactGraph(时序事实演化图)...")
    graph = stage_c_fact_graph(spec, dims, plan, n_sessions=n_sessions)
    print(f"  entities={len(graph.get('entities', []))}, fields={len(graph.get('fields', []))}, "
          f"sessions={len(graph.get('sessions', []))}, evolutions={len(graph.get('evolutions', []))}")
    print(f"  evolutions: {graph.get('evolutions')}")
    print(f"  absent_fields: {graph.get('absent_fields')}")

    print("\n[Stage D] Corpus(多 session 多文档)...")
    corpus = stage_d_corpus(graph)
    total_docs = sum(len(s["docs"]) for s in corpus["sessions"])
    print(f"  {len(corpus['sessions'])} sessions, {total_docs} docs "
          f"(密度 ×{total_docs/max(1,len(corpus['sessions'])):.1f})")

    print("\n[Stage E] ★ Answer-first 出题(LLM 主角)...")
    raw_q = stage_e_answer_first(corpus, graph, plan)
    print(f"  raw questions: {len(raw_q)}")

    print("\n[Stage F] 三道校验闸(grounding 代码 + 可执行验证 + judge)...")
    passed, rejects, conflicts = stage_f_validate(raw_q, corpus, graph)

    print("\n[Stage G] 打包...")
    from collections import Counter
    bench = {
        "benchmark_id": benchmark_id,
        "spec": spec,
        "dimensions": asdict(dims),
        "capability_plan": plan,
        "fact_graph": graph,
        "corpus": corpus,
        "questions": passed,
        "reject_log": rejects,
        "conflict_log": conflicts,
        "stats": {
            "raw": len(raw_q), "passed": len(passed), "rejected": len(rejects),
            "structure_judge_conflicts": len(conflicts),
            "by_capability": dict(Counter(q.get("capability") for q in passed)),
            "by_gt_source": dict(Counter(q.get("gt_source", "?") for q in passed)),
        },
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUTPUT_DIR / f"{benchmark_id}.json"
    out.write_text(json.dumps(bench, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ {out} ({out.stat().st_size // 1024} KB)")

    print("\n" + "=" * 56 + f"\n=== ✓ V8: {len(passed)} 题 ===\n" + "=" * 56)
    print(f"  by_capability: {bench['stats']['by_capability']}")
    print(f"  by_gt_source : {bench['stats']['by_gt_source']}")
    print(f"  (代码结构裁判已弃用 → 裁判 = grounding(机械防幻觉) + LLM judge(读真值轨迹))")
    return bench


def main():
    description = ("我想评测记忆体系统在『AI 工程团队周报』场景下的表现。"
                  "我是业务线 leader,每周看团队周报,关注 P0 缺陷率、Oncall 数量、"
                  "负责人这些会随周变化的字段,要以最新信息为准。")
    corpus_samples = [
        {"doc_id": "d1", "title": "AI 工程周报 W17",
         "content": "本周 P0 缺陷率 20%,Oncall 数量 10。负责人:张三。",
         "metadata": {"date": "2025-04-17", "doc_type": "周报"}},
        {"doc_id": "d2", "title": "AI 工程周报 W18",
         "content": "本周 P0 缺陷率 17%,Oncall 数量 9。负责人:张三。",
         "metadata": {"date": "2025-04-24", "doc_type": "周报"}},
    ]
    run_pipeline_v8(description, corpus_samples, target_size=12, n_sessions=5,
                    benchmark_id="v8_smoke_office")


if __name__ == "__main__":
    main()
