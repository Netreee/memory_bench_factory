"""V10 T5 出题+四闸冒烟:复用已存的世界+语料,跑 Stage E(fact-first 直接合成)+ F(四闸)。
gt 全来自状态机(代码),judge 只判 well_formed。
跑法:cd memory_bench_factory && ./venv/bin/python tools/v10_stage_ef_smoke.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pipeline.world_state import WorldState
from pipeline.order_gen import generate_orders
from pipeline.stages_v10 import stage_e_synthesize_v10, stage_f_validate_v10

OUT = ROOT / "output" / "v10_stage_ef_smoke.json"


def main():
    c = json.load(open(ROOT / "output" / "v10_stage_c_smoke.json", encoding="utf-8"))
    d = json.load(open(ROOT / "output" / "v10_stage_d_smoke.json", encoding="utf-8"))
    ws = WorldState.from_dict(c["world_state"])
    corpus = d["corpus"]
    plan_req = {it["capability"]: it["n"] for it in c["capability_plan"]["items"]}
    print(f"复用世界 {len(ws.entities)} 实体 + 语料 {len(corpus['sessions'])} sessions;plan={plan_req}")

    orders = generate_orders(ws, plan_req)
    print(f"点菜 {len(orders)} 张订单")

    print("\n[Stage E] fact-first 合成(gt 来自订单)...")
    raw = stage_e_synthesize_v10(orders, corpus)

    print("\n[Stage F] 四闸校验(grounding + 硬判别器 + judge well_formed)...")
    passed, rejects = stage_f_validate_v10(raw, corpus, ws)

    print(f"\n{'=' * 56}\n=== 通过 {len(passed)}/{len(raw)} ===\n{'=' * 56}")
    for q in passed:
        print(f"  [{q['capability']}] {q['question']}")
        print(f"       → {q['answer']}  (证据周 {q['evidence_sessions']}, gt_source={q.get('gt_source')})")
    if rejects:
        print("\n被拒(看四闸有没有冤杀/漏网):")
        for r in rejects:
            tail = f"/{r.get('reason')}" if r.get("reason") else ""
            print(f"  ✗[{r['gate']}{tail}] {r['question'][:52]}")

    OUT.write_text(json.dumps({"questions": passed, "reject_log": rejects}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"\n✓ {OUT}")


if __name__ == "__main__":
    main()
