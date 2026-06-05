"""V10 T4 渲染冒烟:复用 output/v10_stage_c_smoke.json 的世界,渲成多 session 多文档,查防剧透。
gt 在世界里,渲染只负责把【本期快照】写自然(session-local,不剧透最新值)。
跑法:cd memory_bench_factory && ./venv/bin/python tools/v10_stage_d_smoke.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pipeline.world_state import WorldState
from pipeline.stages_v10 import stage_d_corpus_v10

SRC = ROOT / "output" / "v10_stage_c_smoke.json"
OUT = ROOT / "output" / "v10_stage_d_smoke.json"


def main():
    d = json.load(open(SRC, encoding="utf-8"))
    ws = WorldState.from_dict(d["world_state"])
    print(f"复用世界:{len(ws.entities)} 实体, n_sessions={ws.n_sessions}")

    corpus, leak = stage_d_corpus_v10(ws)

    for sess in corpus["sessions"][:3]:
        print(f"\n=== session {sess['session_id']} / {sess['date']} ===")
        for doc in sess["docs"]:
            print(f"  [{doc.get('type')}] refs={doc.get('fact_refs')}")
            print(f"    {doc.get('content', '')[:170]}")

    OUT.write_text(json.dumps({"corpus": corpus, "leak_log": leak}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"\n✓ {OUT} ({OUT.stat().st_size // 1024} KB);★剧透命中 {len(leak)}")


if __name__ == "__main__":
    main()
