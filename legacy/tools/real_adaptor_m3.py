"""
tools.real_adaptor_m3 — 用公司真 simpleMem + adaptors(R1/R3)在 M3 题上做对照。

接 /Users/ryanleory/proj/memory-systems-eval 的:
  SimpleRAGMemory(纯 numpy dense 记忆) + SingleTurnAdaptor(R1) + PlanAndActAdaptor(R3)
在 benchmark 的 M3 题上跑,对比 R1 vs R3 的【信号竞争触发率】。

★ 毕设 M3 论点:R3 的多步反思会"挑常识/默认值",即使检索到新值也可能退回高频旧值。
   预期:R3 触发率 ≥ R1 触发率(反思型 M3,比 R1 的检索瓶颈型更深一层)。

跑法:
  cd memory_bench_factory
  ./venv/bin/python tools/real_adaptor_m3.py output/v6_office_x3_extended.json 3
"""
from __future__ import annotations
from pathlib import Path
import sys

MEMEVAL = "/Users/ryanleory/proj/memory-systems-eval"
sys.path.insert(0, MEMEVAL)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from simpleMem_src import SimpleRAGMemory, OpenAIClient, get_config  # noqa: E402
from adaptors import SingleTurnAdaptor, PlanAndActAdaptor            # noqa: E402
from pipeline.schema import load_benchmark                          # noqa: E402
from eval.judge import classify_m3                                  # noqa: E402


def _cls_key(cls: str) -> str:
    return {"m3_triggered_old": "trigger", "correct_new": "correct"}.get(cls, "other")


def _make_llm():
    cfg = get_config()
    return OpenAIClient(api_key=cfg.llm["api_key"], base_url=cfg.llm["base_url"],
                        model=cfg.llm["model"], temperature=0.3, max_tokens=8000)


def _safe_run(adaptor, question: str, top_k: int, retries: int = 4):
    """包裹 adaptor.run,对 DMXAPI 偶发超时/连接异常做指数退避重试,失败返回 None。"""
    import time
    for attempt in range(retries):
        try:
            return adaptor.run(question, top_k=top_k)
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(3.0 * (attempt + 1))
                continue
            print(f"    [skip] run 失败({type(e).__name__}),重试 {retries} 次仍失败,跳过",
                  flush=True)
            return None


def run(bench_path: str, top_k: int = 3, do_r3: bool = True):
    bm = load_benchmark(bench_path)
    llm = _make_llm()
    stats = {"R1": {"trigger": 0, "correct": 0, "other": 0, "n": 0},
             "R3": {"trigger": 0, "correct": 0, "other": 0, "n": 0}}

    print(f"[real-adaptor] {bm.benchmark_id}, top_k={top_k}, R3={do_r3}", flush=True)
    for ci, chain in enumerate(bm.chains):
        mem = SimpleRAGMemory(collection_name=f"{bm.benchmark_id}_{ci}")
        try:
            mem.reset()
        except Exception:
            pass
        # ingest(带日期表头,给 R3 做 recency 推理的公平机会)
        for p in sorted(chain.periods, key=lambda p: int(p.period_idx)):
            header = f"[周期{p.period_idx} | 日期 {p.date}] "
            for doc in p.ingest_docs:
                mem.add_memory(header + doc.get("content", ""),
                               metadata={"period": p.period_idx})
        r1 = SingleTurnAdaptor(llm, mem)
        r3 = PlanAndActAdaptor(llm, mem, max_expansion_steps=4, max_additions=1)

        m3s = [q for q in chain.qas if q.failmode == "M3" and q.failmode_evidence]
        for q in m3s:
            ev = q.failmode_evidence
            res1 = _safe_run(r1, q.question, top_k)
            if res1 is None:
                continue
            c1 = classify_m3(res1.answer, ev)
            stats["R1"][_cls_key(c1)] += 1
            stats["R1"]["n"] += 1
            line = (f"  [{q.field_name}] {ev.get('old_value')}→{ev.get('new_value')} "
                    f"| R1:{c1}('{res1.answer.strip()[:20]}')")
            if do_r3:
                res3 = _safe_run(r3, q.question, top_k)
                if res3 is not None:
                    c3 = classify_m3(res3.answer, ev)
                    stats["R3"][_cls_key(c3)] += 1
                    stats["R3"]["n"] += 1
                    line += f" | R3:{c3}('{res3.answer.strip()[:20]}')"
            print(line, flush=True)

    print(f"\n{'='*60}")
    print("=== R1 vs R3 信号竞争触发对照(真 simpleMem+adaptors)===")
    print(f"{'='*60}")
    for b in ("R1", "R3"):
        s = stats[b]
        if s["n"] == 0:
            continue
        n = s["n"]
        print(f"  {b}: 触发(答旧值)={s['trigger']}/{n}={s['trigger']/n:.1%}, "
              f"答对(新值)={s['correct']}/{n}={s['correct']/n:.1%}, "
              f"其他={s['other']}")
    if stats["R1"]["n"] and stats["R3"]["n"]:
        d = stats["R3"]["trigger"]/stats["R3"]["n"] - stats["R1"]["trigger"]/stats["R1"]["n"]
        verdict = "R3 更易触发 M3(反思退回旧值,契合毕设)" if d > 0 else \
                  "R3 未比 R1 更易触发(本规模下反思未恶化)" if d == 0 else \
                  "R3 反而比 R1 不易触发"
        print(f"\n  ★ R3 触发率 - R1 触发率 = {d:+.1%} → {verdict}")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "output/v6_office_x3_extended.json"
    tk = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    run(path, top_k=tk)
