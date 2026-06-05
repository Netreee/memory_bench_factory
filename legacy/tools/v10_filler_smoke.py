"""草堆验证:复用世界 → 重渲染(写实证据文档, mention-on-change) → 加 FILLER 干扰 → 量尺寸。
跑法:cd memory_bench_factory && ./venv/bin/python tools/v10_filler_smoke.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pipeline.world_state import WorldState
from pipeline.stages_v10 import stage_d_corpus_v10, stage_d_add_filler_v10

OUT = ROOT / "output" / "v10_stage_d_smoke.json"


def _measure(corpus, tag):
    docs = [d for s in corpus["sessions"] for d in s["docs"]]
    lens = [len(d.get("content", "")) for d in docs] or [0]
    fil = sum(1 for d in docs if d.get("is_filler"))
    print(f"  [{tag}] {len(corpus['sessions'])}周/{len(docs)}篇(干扰{fil}) "
          f"平均{sum(lens)//len(lens)}字 总{sum(lens)}字")


def main():
    c = json.load(open(ROOT / "output" / "v10_stage_c_smoke.json", encoding="utf-8"))
    ws = WorldState.from_dict(c["world_state"])
    print(f"复用世界 {len(ws.entities)} 实体, {ws.n_sessions} 周")

    print("\n[Stage D] 重渲染证据文档(写实 200-400 字, mention-on-change)...")
    corpus, leak = stage_d_corpus_v10(ws)
    _measure(corpus, "仅证据")

    print("\n[Stage D-filler] ★ 加草堆/干扰文档...")
    corpus = stage_d_add_filler_v10(ws, corpus, n_per_session=3)
    _measure(corpus, "证据+草堆")

    # 抽 1 个空周(原本无变更)看现在有没有被草堆填上
    for sess in corpus["sessions"]:
        ev = [d for d in sess["docs"] if not d.get("is_filler")]
        fl = [d for d in sess["docs"] if d.get("is_filler")]
        if not ev and fl:
            print(f"\n=== 原空周 session {sess['session_id']} 现在被草堆填上({len(fl)}篇干扰) ===")
            print(f"  [{fl[0].get('type')}] {fl[0]['content'][:140]}")
            break

    OUT.write_text(json.dumps({"corpus": corpus, "leak_log": leak}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"\n✓ {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
