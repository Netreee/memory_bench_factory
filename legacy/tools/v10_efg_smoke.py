"""V10 T5+T6 全段:复用世界+语料,跑 E(合成)+F(四闸)+G(有效性闭环),产出完整 V10 benchmark。
加 FORGET 配额让 FAMA 有料(世界里有 EXPIRE 字段)。
跑法:cd memory_bench_factory && ./venv/bin/python tools/v10_efg_smoke.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pipeline.world_state import WorldState
from pipeline.order_gen import generate_orders
from pipeline.stages_v10 import stage_e_synthesize_v10, stage_f_validate_v10, stage_g_validity_v10

OUT = ROOT / "output" / "v10_benchmark.json"


def main():
    c = json.load(open(ROOT / "output" / "v10_stage_c_smoke.json", encoding="utf-8"))
    d = json.load(open(ROOT / "output" / "v10_stage_d_smoke.json", encoding="utf-8"))
    ws = WorldState.from_dict(c["world_state"])
    corpus = d["corpus"]

    plan_req = {it["capability"]: it["n"] for it in c["capability_plan"]["items"]}
    plan_req["FORGET"] = 2                       # ★ 加 FORGET,让 FAMA 的 λ 惩罚有料
    print(f"plan={plan_req}")
    orders = generate_orders(ws, plan_req)
    print(f"点菜 {len(orders)} 张订单")

    print("\n[Stage E] fact-first 合成...")
    raw = stage_e_synthesize_v10(orders, corpus)
    print("\n[Stage F] 四闸...")
    passed, rejects = stage_f_validate_v10(raw, corpus, ws)
    print(f"\n=== 通过 {len(passed)}/{len(raw)} ===")
    from collections import Counter
    print(f"  by_capability: {dict(Counter(q['capability'] for q in passed))}")
    for q in passed:
        print(f"  [{q['capability']}] {q['question'][:46]} → {q['answer'][:24]}")

    print("\n[Stage G] ★ 有效性闭环(baseline 作答矩阵 → 难度/区分度/排名/FAMA)...")
    validity = stage_g_validity_v10(passed, corpus)

    OUT.write_text(json.dumps({
        "world_state": ws.to_dict(), "corpus": corpus,
        "questions": passed, "reject_log": rejects, "validity": validity,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✓ {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
