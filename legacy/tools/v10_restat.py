"""从 v10_benchmark.json 存盘的作答矩阵【纯代码即时重算】有效性统计(改判分地板后无需再跑 LLM)。
跑法:cd memory_bench_factory && ./venv/bin/python tools/v10_restat.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pipeline.stages_v10 import _validity_stats

SRC = ROOT / "output" / "v10_benchmark.json"


def main():
    d = json.load(open(SRC, encoding="utf-8"))
    R = d["validity"]["matrix"]
    answers = d["validity"].get("raw_answers", {})
    baselines = list(R.keys())
    d["validity"] = _validity_stats(R, answers, d["questions"], baselines)
    SRC.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

    v = d["validity"]
    print("\n--- 每题 难度×区分度(判分地板修复后)---")
    for i, q in enumerate(d["questions"]):
        disc = v["discrimination"][i]
        ds = "  N/A" if disc is None else f"{disc:+.2f}"
        print(f"  [{q['capability']:<8}] 难度{v['difficulty'][i]:.2f} 区分{ds}  {q['question'][:28]}")
    print(f"\n✓ 已更新 {SRC}")


if __name__ == "__main__":
    main()
