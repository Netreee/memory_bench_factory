"""V10 真系统评测器:用【真记忆系统】(EmbedMemory = simpleMem 式 dense 检索)在
v10_bench_*.json 上跑,而不是 Stage G 那种"喂全文"的 oracle proxy。

流程(= 一个真 simpleMem F1+E1+Q1+R1 系统):
  ingest 全语料(每片段加[第N周 日期]表头) → 每题 retrieve top-k → LLM 据检索片段作答 → gt 判分
这才是"记忆系统到底能不能在草堆里检索到对的周、答对题"的真信号。

跑法:
  ./venv/bin/python tools/v10_eval_memsys.py [output/v10_bench_crm.json] [top_k=6]
  不给文件则跑全部 output/v10_bench_*.json
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from eval.memory_interface import EmbedMemory
from pipeline.stages_v10 import _robust_answer, _judge_grade


def evaluate(bench_path: Path, top_k: int = 6, verbose: bool = True) -> dict:
    d = json.load(open(bench_path, encoding="utf-8"))
    corpus, questions = d["corpus"], d["questions"]
    if not questions:
        return {"scenario": d.get("scenario"), "n": 0}

    # ── ingest:真记忆系统吃下全语料(含草堆),每片段带周/日期表头 ──
    mem = EmbedMemory()
    n_docs = 0
    for sess in corpus["sessions"]:
        wk, date = sess["session_id"], sess["date"]
        prefix = f"[第{wk}周 {date}] "
        for doc in sess.get("docs", []):
            mem.ingest(doc.get("content", ""), doc_id=doc.get("doc_id", ""), text_prefix=prefix)
            n_docs += 1

    # ── 每题:retrieve top-k → 据检索片段作答 → 判分 ──
    rows, by_cap = [], defaultdict(lambda: [0, 0])
    for q in questions:
        chunks = mem.retrieve(q["question"], top_k=top_k)
        ctx = "\n".join(chunks)
        ans = _robust_answer(q["question"], ctx)
        ok = _judge_grade(q, ans)
        by_cap[q["capability"]][0] += ok
        by_cap[q["capability"]][1] += 1
        rows.append({"cap": q["capability"], "q": q["question"], "gt": q["answer"], "ans": ans, "ok": ok})

    acc = sum(r["ok"] for r in rows) / len(rows)
    oracle = d.get("validity", {}).get("model_accuracy", {}).get("oracle_full")
    noctx = d.get("validity", {}).get("model_accuracy", {}).get("no_context")
    res = {
        "scenario": d.get("scenario"), "n": len(rows), "top_k": top_k, "n_docs": n_docs,
        "memsys_acc": round(acc, 3), "oracle_proxy": oracle, "no_context_proxy": noctx,
        "by_capability": {c: f"{v[0]}/{v[1]}" for c, v in by_cap.items()},
        "wrong": [{"cap": r["cap"], "q": r["q"][:46], "gt": str(r["gt"])[:22], "got": str(r["ans"])[:30]}
                  for r in rows if not r["ok"]],
    }
    if verbose:
        print(f"\n━━━ {res['scenario']}  ({len(rows)}题 / {n_docs}篇语料 / top-{top_k}) ━━━")
        print(f"  ★真系统 EmbedMemory: {acc:.2f}   |   oracle(喂全文)proxy: {oracle}   no_context: {noctx}")
        print(f"  各能力(对/总): {res['by_capability']}")
        print(f"  答错 {len(res['wrong'])}/{len(rows)} 题(真系统在草堆里检索失败/记忆失败):")
        for w in res["wrong"][:8]:
            print(f"    [{w['cap']}] {w['q']} | gt={w['gt']} | 真系统答:{w['got']}")
    return res


def main():
    args = [a for a in sys.argv[1:] if not a.isdigit()]
    top_k = next((int(a) for a in sys.argv[1:] if a.isdigit()), 6)
    files = [Path(a) for a in args] or sorted(ROOT.glob("output/v10_bench_*.json"))
    out = ROOT / "output" / "v10_memeval_summary.json"
    merged = {}                                      # ★ 增量合并:按场景 key,跑子集也累积
    if out.exists():
        try:
            for r in json.load(open(out, encoding="utf-8")):
                merged[r.get("scenario")] = r
        except Exception:
            pass
    for f in files:
        if f.exists():
            r = evaluate(f, top_k=top_k)
            merged[r.get("scenario")] = r
    allres = list(merged.values())
    out.write_text(json.dumps(allres, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{'='*60}\n横评(真系统 vs oracle proxy):")
    for r in allres:
        if r.get("n"):
            print(f"  {r['scenario']:<8} 真系统 {r['memsys_acc']:.2f}  vs oracle {r['oracle_proxy']}  "
                  f"(落差 {r['oracle_proxy']-r['memsys_acc']:+.2f} = 检索瓶颈的代价)")
    print(f"✓ {out}")


if __name__ == "__main__":
    main()
