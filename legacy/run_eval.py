"""
eval.run_eval — W2 编排:跑 baseline over benchmark + 聚合(标准 A + failmode 命中率)。

流程(每条 chain):
  1. reset 记忆 → 按 period 顺序 ingest 所有文档(模拟时间推进)
  2. 终态(全部 ingest 完)对每道题:retrieve top-k → R1 合成答案 → 判分
  3. M3 题额外:分类答案命中旧值/新值 + 统计 top-k 里旧/新值出现次数(检索瓶颈诊断)

聚合输出:
  - overall accuracy(标准 A:benchmark 是否有区分度,不是全 0/全 100)
  - per-failmode accuracy(failmode 预测是否成立:M3 应显著低于普通题)
  - ★ M3 信号竞争触发率(答成高频旧值的比例)+ 检索瓶颈诊断

跑法:
  cd memory_bench_factory
  ./venv/bin/python -m eval.run_eval                      # 默认 top_k=3
  ./venv/bin/python -m eval.run_eval output/xxx_extended.json 3
"""
from __future__ import annotations
from collections import defaultdict
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.schema import load_benchmark
from eval.memory_interface import EmbedMemory
from eval.baseline_r1 import r1_answer
from eval.judge import judge, classify_m3, literal_match, _norm


def _is_empty_val(v) -> bool:
    """空占位判定:空串/纯空白/字面量 'none'(Stage D 给单期题其他 period 填的占位)。"""
    s = str(v).strip().lower()
    return (not s) or s == "none"


def _eval_period(q, final_idx: str) -> str:
    """本题的评测周期 = answer 里【有非空值】的最大 period。

    - KU/M3"最新值"题:各 period 都非空,最大 period 即终期(正确的最新值)。
    - IE 单期题:LLM 常只在被问那期填真值、其余期填空占位('' 或 'None');
      取【最大非空期】才能对齐被问周期,不被空占位带偏(否则撞上终期空占位 → 误判不可判分)。
    全空才回退全局 final_idx(此时 _gold_final 会正确判为不可判分)。
    """
    abp = q.answer_by_period or {}
    acp = q.answer_canonical_by_period or {}

    def _nonempty(pk: str) -> bool:
        vs = abp.get(pk)
        if vs:
            for v in (vs if isinstance(vs, list) else [vs]):
                if not _is_empty_val(v):
                    return True
        return not _is_empty_val(acp.get(pk, "")) if acp.get(pk) is not None else False

    all_keys = set(abp.keys()) | set(acp.keys())
    nonempty = [int(k) for k in all_keys if _nonempty(k)]
    if nonempty:
        return str(max(nonempty))
    any_keys = [int(k) for k in all_keys]
    return str(max(any_keys)) if any_keys else final_idx


def _gold_final(q, final_idx: str) -> list:
    """评测周期的标准答案等价集(按本题 answer_by_period 的最大 period 对齐)。

    过滤掉空串/纯空白(Stage D 偶尔给某些题填空答案 → 不可判分,不应计入准确率)。
    """
    idx = _eval_period(q, final_idx)
    eq = (q.answer_by_period or {}).get(idx)
    vals = list(eq) if eq else []
    if not vals:
        canon = (q.answer_canonical_by_period or {}).get(idx)
        vals = [canon] if canon else []
    return [v for v in vals if str(v).strip() and str(v).strip().lower() != "none"]


def _gold_any(q) -> list:
    """所有 period 的标准答案并集(宽松判分用)。"""
    vals = []
    for eq in (q.answer_by_period or {}).values():
        vals.extend(eq if isinstance(eq, list) else [eq])
    for v in (q.answer_canonical_by_period or {}).values():
        vals.append(v)
    return list({str(v) for v in vals if v})


def run_eval(bm, top_k: int = 3, use_llm_judge: bool = True, verbose: bool = True) -> tuple:
    """跑 R1 × EmbedMemory over benchmark。返回 (records, summary)。"""
    mem = EmbedMemory(chunk=True)
    records: list = []

    for chain in bm.chains:
        if verbose:
            print(f"\n[run_eval] chain {chain.chain_id} ({len(chain.qas)} 题)")
        mem.reset()
        # 1. 按 period 顺序 ingest
        sorted_periods = sorted(chain.periods, key=lambda p: int(p.period_idx))
        for p in sorted_periods:
            # ★ 每个片段自带【周期+日期】表头:公平给 baseline 做 recency 推理的机会。
            #   若此时 M3 仍触发(答高频旧值),才是真·conflict defaulting(非藏日期的假象)。
            header = f"[周期{p.period_idx} | 日期 {p.date}] "
            for doc in p.ingest_docs:
                mem.ingest(doc.get("content", ""), doc.get("doc_id", ""),
                           {**(doc.get("metadata") or {}), "period_idx": p.period_idx},
                           text_prefix=header)
        final_idx = str(max(int(p.period_idx) for p in sorted_periods))

        # 2. 终态提问
        for i, q in enumerate(chain.qas):
            snippets = mem.retrieve(q.question, top_k=top_k)
            pred = r1_answer(q.question, snippets)
            gold_final = _gold_final(q, final_idx)
            gold_any = _gold_any(q)
            judgeable = bool(gold_final)   # 空 gold(Stage D 漏填)→ 不可判分,不计入准确率
            if judgeable:
                correct_final = judge(q.question, gold_final, pred, use_llm=use_llm_judge)
            else:
                correct_final = False
            correct_any = correct_final or literal_match(pred, gold_any)

            rec = {
                "qid": q.qid, "capability": q.capability, "failmode": q.failmode,
                "question": q.question, "pred": pred, "judgeable": judgeable,
                "gold_final": gold_final, "correct_final": correct_final,
                "correct_any": correct_any,
            }
            # 3. M3 诊断
            if q.failmode == "M3" and q.failmode_evidence:
                ev = q.failmode_evidence
                rec["m3_class"] = classify_m3(pred, ev)
                old_v, new_v = _norm(ev.get("old_value")), _norm(ev.get("new_value"))
                rec["retr_old"] = sum(1 for s in snippets if old_v and old_v in _norm(s))
                rec["retr_new"] = sum(1 for s in snippets if new_v and new_v in _norm(s))
            records.append(rec)
            if verbose:
                tag = ""
                if "m3_class" in rec:
                    tag = (f"  [M3:{rec['m3_class']} | 检索 旧×{rec['retr_old']}"
                           f"/新×{rec['retr_new']}]")
                mark = ("∅" if not judgeable else ("✓" if correct_final else "✗"))
                gold_show = gold_final if judgeable else "(空·不可判分)"
                print(f"  {mark} ({q.capability},{q.failmode or 'none'}) "
                      f"pred='{pred[:30]}' gold={gold_show}{tag}")

    summary = aggregate(records)
    return records, summary


def aggregate(records: list) -> dict:
    """聚合标准 A 指标 + per-failmode + M3 触发率。"""
    n_total = len(records)
    if n_total == 0:
        return {}
    # ★ 只在【可判分】题上算准确率(空 gold = Stage D 漏填,剔除以免污染标准 A)
    recs = [r for r in records if r.get("judgeable", True)]
    n = len(recs)
    n_unjudgeable = n_total - n
    if n == 0:
        return {"n": 0, "n_total": n_total, "n_unjudgeable": n_unjudgeable,
                "accuracy_final": 0.0, "accuracy_any": 0.0,
                "by_failmode": {}, "by_capability": {}, "m3_signal_competition": {}}
    acc_final = sum(r["correct_final"] for r in recs) / n
    acc_any = sum(r["correct_any"] for r in recs) / n

    by_fm = defaultdict(lambda: {"n": 0, "correct": 0})
    by_cap = defaultdict(lambda: {"n": 0, "correct": 0})
    for r in recs:
        fm = r["failmode"] or "none"
        by_fm[fm]["n"] += 1
        by_fm[fm]["correct"] += int(r["correct_final"])
        by_cap[r["capability"]]["n"] += 1
        by_cap[r["capability"]]["correct"] += int(r["correct_final"])

    # M3 信号竞争分析
    m3 = [r for r in recs if r["failmode"] == "M3"]
    m3_summary = {}
    if m3:
        cls_count = defaultdict(int)
        for r in m3:
            cls_count[r.get("m3_class", "other")] += 1
        retr_old = sum(r.get("retr_old", 0) for r in m3)
        retr_new = sum(r.get("retr_new", 0) for r in m3)
        m3_summary = {
            "n": len(m3),
            "class_breakdown": dict(cls_count),
            "trigger_rate": cls_count.get("m3_triggered_old", 0) / len(m3),
            "accuracy": sum(r["correct_final"] for r in m3) / len(m3),
            "retrieval_old_total": retr_old,
            "retrieval_new_total": retr_new,
        }

    return {
        "n": n,
        "n_total": n_total,
        "n_unjudgeable": n_unjudgeable,
        "accuracy_final": round(acc_final, 3),
        "accuracy_any": round(acc_any, 3),
        "by_failmode": {k: {**v, "acc": round(v["correct"] / v["n"], 3)}
                        for k, v in by_fm.items()},
        "by_capability": {k: {**v, "acc": round(v["correct"] / v["n"], 3)}
                          for k, v in by_cap.items()},
        "m3_signal_competition": m3_summary,
    }


def print_report(summary: dict):
    print("\n" + "=" * 64)
    print("=== W2 评测报告 ===")
    print("=" * 64)
    nun = summary.get("n_unjudgeable", 0)
    print(f"可判分题数: {summary['n']} / 总 {summary.get('n_total', summary['n'])} "
          f"(不可判分剔除 {nun} 道 ← Stage D 漏填空 gold,benchmark 质量信号)")
    print(f"overall accuracy(终态严格): {summary['accuracy_final']:.1%}")
    print(f"overall accuracy(任一period宽松): {summary['accuracy_any']:.1%}")
    print(f"\n--- 标准 A:区分度 ---")
    af = summary["accuracy_final"]
    if 0.05 < af < 0.95:
        print(f"  ✓ accuracy={af:.1%} ∈ (5%,95%),benchmark 有区分度(非全对/全错)")
    else:
        print(f"  ⚠ accuracy={af:.1%} 太极端,区分度弱(corpus 太小或题太易/难)")

    print(f"\n--- per-failmode accuracy(failmode 预测验证)---")
    for fm, v in sorted(summary["by_failmode"].items()):
        print(f"  {fm:6s}: {v['correct']}/{v['n']} = {v['acc']:.1%}")

    print(f"\n--- per-capability accuracy ---")
    for cap, v in sorted(summary["by_capability"].items()):
        print(f"  {cap:5s}: {v['correct']}/{v['n']} = {v['acc']:.1%}")

    m3 = summary.get("m3_signal_competition")
    if m3:
        print(f"\n--- ★ M3 信号竞争分析(V6 核心验证)---")
        print(f"  M3 题数: {m3['n']}")
        print(f"  M3 accuracy: {m3['accuracy']:.1%}")
        print(f"  ★ 信号竞争触发率(答成高频旧值): {m3['trigger_rate']:.1%}")
        print(f"  分类明细: {m3['class_breakdown']}")
        print(f"  检索瓶颈诊断: top-k 里旧值出现 {m3['retrieval_old_total']} 次, "
              f"新值出现 {m3['retrieval_new_total']} 次")
        if m3["retrieval_old_total"] > m3["retrieval_new_total"]:
            print(f"    → 检索确实偏向旧值(信号竞争机制成立)")
        else:
            print(f"    → 检索未明显偏旧值(corpus 太小/ top_k 太大,需放大规模)")


def main():
    args = sys.argv[1:]
    bench_path = args[0] if args else "output/v6_smoke_office_extended.json"
    top_k = int(args[1]) if len(args) > 1 else 3
    print(f"[W2] benchmark={bench_path}, top_k={top_k}")
    bm = load_benchmark(bench_path)
    print(f"[W2] loaded: {bm.benchmark_id}, {sum(len(c.qas) for c in bm.chains)} 题, "
          f"{len(bm.chains)} chains")
    records, summary = run_eval(bm, top_k=top_k, use_llm_judge=True, verbose=True)
    print_report(summary)


if __name__ == "__main__":
    main()
