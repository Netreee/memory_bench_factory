"""只重跑 Stage G(LLM-judge 判分,治 M5),复用 v10_benchmark.json 的题+语料,不重跑 E+F。
跑法:cd memory_bench_factory && ./venv/bin/python tools/v10_regrade.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pipeline.stages_v10 import stage_g_validity_v10

SRC = ROOT / "output" / "v10_benchmark.json"


def main():
    d = json.load(open(SRC, encoding="utf-8"))
    print(f"重判 {len(d['questions'])} 题(LLM-judge 多口径)...")
    d["validity"] = stage_g_validity_v10(d["questions"], d["corpus"])
    SRC.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

    v = d["validity"]
    print("\n--- 每题 难度×区分度(重判后)---")
    for i, q in enumerate(d["questions"]):
        print(f"  [{q['capability']}] 难度{v['difficulty'][i]:.2f} 区分{v['discrimination'][i]:+.2f}  {q['question'][:30]}")
    print(f"\n✓ 已更新 {SRC}")


if __name__ == "__main__":
    main()
