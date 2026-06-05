"""V10 地基冒烟:Stage 0→A→B→C(状态机世界)→ T2 点菜,在【真 LLM】上验证。
gt 全由代码算。落盘 output/v10_stage_c_smoke.json(世界表 + WorldState + 订单,治可回溯性)。
跑法:cd memory_bench_factory && ./venv/bin/python tools/v10_stage_c_smoke.py
"""
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.schema import RefinedScenarioSpec, Document
from pipeline.stage_a_dimensions import infer_dimensions
from pipeline.stages_v8 import stage_0_refine, stage_b_capability_plan
from pipeline.stages_v10 import stage_c_world_v10
from pipeline.order_gen import generate_orders
from pipeline.world_state import answer as gt_answer

OUT = Path(__file__).resolve().parent.parent / "output" / "v10_stage_c_smoke.json"

# ── 多部门 office 周报场景(扩成多实体,撑起 MR/CONFLICT/级联)──
description = (
    "我想评测记忆体系统在『公司多部门周报』场景下的表现。我是跨业务线 leader,"
    "每周看 AI 工程部、数据平台部、算法研究部等多个部门的周报,关注各部门的 P0 缺陷率、"
    "Oncall 数量、负责人、汇报对象这些会随周变化的字段,要以最新信息为准;"
    "部门负责人变动有时会带动汇报关系变化。"
)
corpus_samples = [
    {"doc_id": "d1", "title": "AI 工程周报 W17",
     "content": "本周 P0 缺陷率 20%,Oncall 数量 10。负责人:张三。",
     "metadata": {"date": "2025-04-17", "doc_type": "周报"}},
    {"doc_id": "d2", "title": "数据平台周报 W17",
     "content": "本周 SLA 达成率 99.1%,值班 8 人。负责人:王五。",
     "metadata": {"date": "2025-04-17", "doc_type": "周报"}},
]
TARGET_SIZE, N_ENTITIES, N_SESSIONS = 14, 3, 6


def main():
    print("[Stage 0] Refine...")
    spec = stage_0_refine(description, corpus_samples)
    print(f"  fields: {[f.get('name') for f in spec.get('key_fields', [])]}")

    print("[Stage A] Dimensions(真 LLM)...")
    docs = [Document(doc_id=d["doc_id"], title=d["title"], content=d["content"], metadata=d["metadata"])
            for d in corpus_samples]
    refined = RefinedScenarioSpec(name="v10_smoke", description_refined=spec.get("description_refined", ""),
                                  corpus_samples=docs, subject_type=spec.get("subject"),
                                  perspective=spec.get("perspective"), target_size=TARGET_SIZE)
    dims = infer_dimensions(refined, use_llm=True)
    print(f"  S={dims.S_memory_subject}, T={dims.T_temporal_pattern}")

    print("[Stage B] CapabilityPlan...")
    plan = stage_b_capability_plan(spec, dims, TARGET_SIZE)
    print(f"  plan: {[(it.get('capability'), it.get('n')) for it in plan.get('items', [])]}")

    print("[Stage C V10] ★ 多实体状态机世界(LLM 填表 + 代码 assemble)...")
    ws, table, issues = stage_c_world_v10(spec, dims, plan, n_entities=N_ENTITIES, n_sessions=N_SESSIONS)

    print("\n[T2 点菜] 从世界枚举 gt 已烘焙的订单...")
    plan_req = {it["capability"]: it["n"] for it in plan.get("items", [])}
    orders = generate_orders(ws, plan_req)
    xdoc = sum(1 for o in orders if o.capability != "ABS" and len(o.evidence_sessions) >= 2)
    print(f"  订单 {len(orders)} 张,跨文档(≥2 session)非 ABS:{xdoc}/{sum(1 for o in orders if o.capability!='ABS')}")
    for o in orders:
        g = o.gt if not isinstance(o.gt, dict) else {k: v for k, v in o.gt.items() if k != "values"}
        print(f"  [{o.capability}] {o.entity}.{o.field} | gt={g} | 证据 sessions={o.evidence_sessions}"
              + (f" | {o.aux}" if o.aux else ""))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "spec": spec, "capability_plan": plan, "world_table_llm": table,
        "world_state": ws.to_dict(), "world_issues": issues,
        "orders": [asdict(o) for o in orders],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✓ {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
