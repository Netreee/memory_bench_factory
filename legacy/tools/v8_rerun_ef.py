"""复用已有 graph/corpus/plan,只重跑 Stage E(出题)+F(grounding+升级judge,弃结构裁判),
看通过率/覆盖能否恢复。跑法:./venv/bin/python tools/v8_rerun_ef.py [src.json]"""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.stages_v8 import stage_e_answer_first, stage_f_validate

src = sys.argv[1] if len(sys.argv) > 1 else "output/v8_smoke_office.json"
d = json.load(open(src, encoding="utf-8"))
graph, corpus, plan = d["fact_graph"], d["corpus"], d["capability_plan"]
plan_req = {it["capability"]: it["n"] for it in plan["items"]}
print(f"复用 {src}: {len(graph.get('sessions', []))} sessions, plan={plan_req}")

print("\n[Stage E] answer-first 重出题...")
raw = stage_e_answer_first(corpus, graph, plan)
print(f"  raw: {len(raw)}")

print("\n[Stage F] grounding + 升级 judge(读真值轨迹,弃结构裁判)...")
passed, rejects, _ = stage_f_validate(raw, corpus, graph)

print(f"\n{'='*56}\n=== 通过 {len(passed)}/{len(raw)} (上一版是 5/12) ===\n{'='*56}")
got = dict(Counter(q["capability"] for q in passed))
print(f"通过 by_capability: {got}")
print(f"plan 要求:        {plan_req}  ← 看 TR/ABS 是否还被清零")
print("\n通过的题:")
for q in passed:
    print(f"  [{q['capability']}] {q['question'][:52]} → {str(q.get('answer',''))[:28]}")
print(f"\nreject {len(rejects)}:")
for r in rejects:
    print(f"  [{r['gate']}] {str(r['reason'])[:54]} :: {str(r['q'])[:34]}")

out = {"fact_graph": graph, "corpus": corpus, "capability_plan": plan,
       "questions": passed, "reject_log": rejects}
json.dump(out, open("output/v8_rerun_ef.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
print("\n✓ output/v8_rerun_ef.json")
