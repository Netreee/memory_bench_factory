"""
增量重渲(closed_loop §10.1)离线自检 —— 锁住 build_world augment + render_corpus delta 的【无 LLM 路径】。
有 LLM 的部分(augment 真生成新实体 / delta 真渲新实体)靠端到端跑 + 盲审验(no-mock,不在此造假 tracer)。
这里只证:① 不需新实体时 augment 零 LLM 调用 + 旧世界不动;② delta 遇无事实实体早退、旧 docs 原样;③ 全量路径未被破坏。

跑:./venv/bin/python tests/incremental_render_selftest.py
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.world_state import WorldState, Timeline, Op, SET, _date_of, name_collisions
from pipeline.world_gen import build_world
from pipeline.render import render_corpus

checks: list[tuple[bool, str]] = []


def ck(name, cond):
    checks.append((bool(cond), name))


def _ws(n_ent=2, n_sess=4):
    e = {f"E{i}部": {"f": Timeline([Op(s, _date_of(s), SET, f"v{i}_{s}", None) for s in range(n_sess)])}
         for i in range(n_ent)}
    return WorldState(e, n_sessions=n_sess)


class _BoomTracer:                       # 任何 LLM 调用都炸 —— 证明 no-op 路径零调用
    def chat_json(self, *a, **k):
        raise AssertionError("不该调用 LLM(no-op 路径)")


# ── ① build_world augment:existing 已达 target → 0 新实体、零 LLM、返回既有同对象 ──
ws = _ws(2, 4)
wp = {"domain_profile": {"entity_noun": "部门"}, "shared_world_spec": {"entities": {"count": 2}, "timeline": {"n_sessions": 4}}}
out = build_world(wp, tracer=_BoomTracer(), log=lambda *a: None, existing=ws)
ck("augment:已达 target → 零 LLM 调用(BoomTracer 没炸)", True)   # 跑到这=没炸
ck("augment:返回既有同对象", out is ws)
ck("augment:实体集不变", sorted(out.entities) == ["E0部", "E1部"])
ck("augment:无表面塌缩", name_collisions(out) == [])

# ── ② render_corpus delta:only_entities 指向【无事实】实体 → 每周早退、零 LLM、旧 docs 原样 ──
corpus = {"sessions": [{"session_id": s, "date": _date_of(s),
                        "docs": [{"doc_id": f"s{s}_sig_0", "content": f"E0部本期 v0_{s}"}]} for s in range(4)]}
done = set(range(4))
before = [len(x["docs"]) for x in corpus["sessions"]]
render_corpus({"domain_profile": {}}, ws, 0, tracer=_BoomTracer(), corpus=corpus, done_weeks=done,
              save_cb=lambda: None, log=lambda *a: None, only_entities={"GHOST"})
after = [len(x["docs"]) for x in corpus["sessions"]]
ck("delta:无事实新实体 → 零 LLM(BoomTracer 没炸)", True)
ck("delta:每周 docs 数不变(纯追加、无事实不动)", before == after)
ck("delta:旧 doc_id 原样保留", corpus["sessions"][0]["docs"][0]["doc_id"] == "s0_sig_0")

# ── ③ delta 遍历【所有周】(非只未完成周):done 全满时全量路径会跳过,delta 仍逐周走(只是无事实→不加) ──
#    用一个【有事实】的实体证明 weeks 选择是 all:把 E1部 当"新实体"(它有事实)→ delta 会试图渲它(需 LLM)。
#    这里不真渲(避免 LLM),只验"weeks=all"的选择逻辑:done 全满 + only_entities 给【有事实实体】→ 会进入渲染(故用 Boom 反证它【确实尝试】)。
ws2 = _ws(2, 3)
corpus2 = {"sessions": [{"session_id": s, "date": _date_of(s), "docs": []} for s in range(3)]}
boomed = False
try:
    render_corpus({"domain_profile": {}}, ws2, 0, tracer=_BoomTracer(), corpus=corpus2, done_weeks={0, 1, 2},
                  save_cb=lambda: None, log=lambda *a: None, only_entities={"E1部"})   # E1部 有事实 → 该试图渲 → Boom
except AssertionError:
    boomed = True
ck("delta:done 全满仍遍历所有周(有事实实体确被尝试渲染,证明 weeks=all)", boomed)

npass = sum(1 for ok, _ in checks if ok)
for ok, name in checks:
    if not ok:
        print(f"  ✗ {name}")
print(f"[incremental_render self-test] {npass}/{len(checks)} PASS")
sys.exit(0 if npass == len(checks) else 1)
