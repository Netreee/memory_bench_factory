"""复用已有 graph/corpus/plan,跑 V9 的 Stage E(多步 exploration)+F(硬判别器+judge多票)。
跑法:./venv/bin/python tools/v9_rerun_ef.py [src.json]"""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.stages_v9 import stage_e_explore_synthesize, stage_f_validate_v9

import os
src = sys.argv[1] if len(sys.argv) > 1 else "output/v8_smoke_office.json"
d = json.load(open(src, encoding="utf-8"))
graph, corpus, plan0 = d["fact_graph"], d["corpus"], d["capability_plan"]
# mini 模式:每能力少出题 + 探索步数可调(避免长任务被环境 kill;先验证机制)
per_cap = int(os.environ.get("V9_PER_CAP", "1"))
max_steps = int(os.environ.get("V9_MAX_STEPS", "3"))
plan = {"items": [{"capability": it["capability"], "n": per_cap} for it in plan0["items"]]}
plan_req = {it["capability"]: it["n"] for it in plan["items"]}
print(f"复用 {src}: plan={plan_req} (per_cap={per_cap}, max_steps={max_steps})", flush=True)

print("\n[Stage E V9] 多步 exploration 出题(search/read 工具 + ReAct)...", flush=True)
raw = stage_e_explore_synthesize(corpus, graph, plan, max_steps=max_steps)
avg_steps = sum(q.get("explore_steps", 0) for q in raw) / max(1, len(raw))
print(f"  raw: {len(raw)} 题, 平均探索步数 {avg_steps:.1f}")

print("\n[Stage F V9] grounding + ★硬判别器 + judge 多票...")
passed, rejects = stage_f_validate_v9(raw, corpus, graph)

print(f"\n{'='*56}\n=== 通过 {len(passed)}/{len(raw)} ===\n{'='*56}")
print(f"通过 by_capability: {dict(Counter(q['capability'] for q in passed))}")
print(f"reject by gate:     {dict(Counter(r['gate'] for r in rejects))}")

print("\n通过的题(看是否更深、题面无泄漏):")
for q in passed:
    print(f"  [{q['capability']}/hops{q.get('hops')}/探{q.get('explore_steps')}步] "
          f"{q['question'][:48]} → {str(q.get('answer'))[:22]}")

print("\n★ 硬判别器抓到的伪记忆题(白痴说的单篇可答/泄漏):")
for r in rejects:
    if r["gate"] == "memory_necessity":
        print(f"  [{r['reason']}] {r['q'][:52]}")

json.dump({"fact_graph": graph, "corpus": corpus, "capability_plan": plan,
           "questions": passed, "reject_log": rejects},
          open("output/v9_rerun_ef.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("\n✓ output/v9_rerun_ef.json")
