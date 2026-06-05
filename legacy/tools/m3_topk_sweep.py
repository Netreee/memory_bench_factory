"""
tools.m3_topk_sweep — M3 信号竞争触发率 vs 检索预算(top_k)扫描。

核心论点(V6):M3 触发 = 旧值在多 period 高频出现 → 其文档挤占检索 top-k →
新值(单 period 低频)进不了 top-k → baseline 看不到新值 → 答高频旧值(冲突默认)。

可证伪预测:top_k 越大,新值越可能被检索到 → 触发率应【单调下降】。

鲁棒性:先 ingest 一次,再按 top_k 外层循环,【每个 top_k 跑完立即 print+flush】,
中断也保留已算完的行。

跑法:
  ./venv/bin/python tools/m3_topk_sweep.py output/v6_office_x3_extended.json 1 3 8
  (不传 top_k 列表则默认 1 2 3 5 8)
"""
from __future__ import annotations
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.schema import load_benchmark
from eval.memory_interface import EmbedMemory
from eval.baseline_r1 import r1_answer
from eval.judge import classify_m3, _norm


def sweep(bench_path: str, topks=(1, 2, 3, 5, 8)):
    bm = load_benchmark(bench_path)

    # 1. ingest 所有 chain(一次),收集 (mem, [m3题])
    units = []
    for chain in bm.chains:
        mem = EmbedMemory(chunk=True)
        for p in sorted(chain.periods, key=lambda p: int(p.period_idx)):
            header = f"[周期{p.period_idx} | 日期 {p.date}] "
            for doc in p.ingest_docs:
                mem.ingest(doc.get("content", ""), doc.get("doc_id", ""),
                           {}, text_prefix=header)
        m3s = [q for q in chain.qas if q.failmode == "M3" and q.failmode_evidence]
        units.append((mem, m3s))
    n_m3 = sum(len(m) for _, m in units)
    print(f"[sweep] ingest 完成, {n_m3} 道 M3, 扫 top_k={topks}", flush=True)

    print(f"\n{'='*70}")
    print(f"=== M3 触发率 vs top_k ===  {Path(bench_path).name}")
    print(f"{'='*70}")
    print(f"{'top_k':>6} | {'触发率(旧)':>11} | {'答对率(新)':>11} | "
          f"{'新值检索':>9} | {'旧值检索':>9} | n")
    print("-" * 70, flush=True)

    # 2. 按 top_k 外层,每个 top_k 跑完立即出一行(中断也保留)
    for k in topks:
        r = {"trigger": 0, "correct": 0, "other": 0,
             "retr_old": 0, "retr_new": 0, "n": 0}
        for mem, m3s in units:
            for q in m3s:
                ev = q.failmode_evidence
                old_v, new_v = _norm(ev.get("old_value")), _norm(ev.get("new_value"))
                snips = mem.retrieve(q.question, top_k=k)
                pred = r1_answer(q.question, snips)
                cls = classify_m3(pred, ev)
                r["n"] += 1
                if cls == "m3_triggered_old":
                    r["trigger"] += 1
                elif cls == "correct_new":
                    r["correct"] += 1
                else:
                    r["other"] += 1
                r["retr_old"] += sum(1 for s in snips if old_v and old_v in _norm(s))
                r["retr_new"] += sum(1 for s in snips if new_v and new_v in _norm(s))
        n = max(1, r["n"])
        print(f"{k:>6} | {r['trigger']/n:>10.1%} | {r['correct']/n:>10.1%} | "
              f"{r['retr_new']:>9} | {r['retr_old']:>9} | {r['n']}", flush=True)

    print("-" * 70)
    print("预期:top_k↑ → 新值被检索↑ → 触发率↓(单调下降则信号竞争机制因果成立)")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "output/v6_office_x3_extended.json"
    tks = tuple(int(x) for x in sys.argv[2:]) or (1, 2, 3, 5, 8)
    sweep(path, topks=tks)
