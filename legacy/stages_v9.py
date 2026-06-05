"""
pipeline.stages_v9 — V9 在 V8 主路上加三件控质量件(redesign_v9.md):
  ① Stage E 多步 exploration(DocDancer 式 search/read 工具 + ReAct 循环出深题)
  ② Stage F 闸(b) 硬判别器(题喂"无语料/单文档"baseline,答中→reject;保有效性+证伪记忆必要性)
  ③ Stage F 闸(c) judge self-consistency 多票

Stage 0/B/C/D 复用 stages_v8(refine/能力规划/FactGraph/多文档 corpus 不变)。
"""
from __future__ import annotations
from collections import Counter
import json

# 复用 V8 不变的部分
from .stages_v8 import (  # noqa: F401
    stage_0_refine, stage_b_capability_plan, stage_c_fact_graph, stage_d_corpus,
    _fuzzy_in, _answers_match, _judge, _norm,
)
import config


# ─────────────────────────────────────────────────────────────────────────────
# Stage E V9 — 多步 exploration(search/read 工具 + ReAct 循环)
# ─────────────────────────────────────────────────────────────────────────────

EXPLORE_SYSTEM = """你是记忆评测出题 agent。用工具【多步探索】给定的多 session 语料,围绕指定能力,
收集【跨多个 session / 多篇文档】的证据;探够了再 synthesize 一道题(answer-first:先据证据定答案,再写问题)。

【工具】每步只输出【一个】 action 的 JSON:
- 搜索:{"thought":"...","tool":"search","arg":"关键词"}  → 返回各文档里含该词的片段(doc_id+session+片段)
- 精读:{"thought":"...","tool":"read","arg":"doc_id"}    → 返回该文档全文
- 出题:{"thought":"...","tool":"synthesize","question":"...","answer":"...","field":"字段名",
         "hops":整数,"evidence_spans":[{"doc_id":"...","span":"逐字原文片段"}]}

【硬约束】
1. 至少先 search/read 2 步、跨到 ≥2 个不同 session 的文档,再 synthesize(强迫跨文档)
2. 题必须【跨 session / 多文档】才能答,单看一篇答不出
3. answer 必须被 evidence_spans 直接支持;evidence_spans 是语料里逐字截取的原文
4. ★ KU/TR 题:【严禁把答案或完整变化轨迹写进问题里】(防泄漏)——只发问,不在题面陈述结论/数值轨迹
5. 能力语义:IE=某 session 某字段值 / MR=跨 session 聚合(最高最低) / TR=变化时机 /
   KU=最新值(旧值多 session 高频,要答最新那个) / ABS=问语料里不存在的字段,answer=INSUFFICIENT_EVIDENCE"""


def _corpus_docs(corpus):
    out = {}
    for sess in corpus.get("sessions", []):
        for doc in sess.get("docs", []):
            out[doc["doc_id"]] = (sess.get("session_id"), doc.get("content", ""))
    return out


def _tool_search(docs, keyword):
    keyword = (keyword or "").strip()
    if not keyword:
        return "需要关键词"
    hits = []
    for doc_id, (sess, content) in docs.items():
        idx = content.find(keyword)
        if idx >= 0:
            hits.append({"doc_id": doc_id, "session": sess,
                         "snippet": content[max(0, idx - 8):idx + len(keyword) + 28]})
    return hits[:8] or f"无文档含 '{keyword}'"


def _tool_read(docs, doc_id):
    if doc_id in docs:
        return docs[doc_id][1]
    return f"无此 doc_id;可选:{list(docs)[:8]}"


def _explore_step(cap, graph, history, steps_left, force=False):
    hist_str = "\n".join(
        f"[{h['action'].get('tool')}({h['action'].get('arg','')})] → {str(h['obs'])[:280]}"
        for h in history) or "(空,先 search)"
    if force or steps_left <= 1:
        instr = "★ 现在必须出题(tool=synthesize),基于已收集证据。"
    else:
        instr = f"还可探索 {steps_left} 步;跨到 ≥2 个 session 后就 synthesize。"
    user = (f"【能力】{cap}\n【演化线索】{json.dumps(graph.get('evolutions', []), ensure_ascii=False)}\n"
            f"【不存在字段(ABS 用)】{graph.get('absent_fields', [])}\n"
            f"【探索历史】\n{hist_str}\n\n{instr} 只输出一个 action 的 JSON。")
    try:
        return config.chat_json(
            [{"role": "system", "content": EXPLORE_SYSTEM}, {"role": "user", "content": user}],
            temperature=0.5, max_tokens=2048)
    except Exception as e:
        return {"tool": "error", "reason": str(e)}


def stage_e_explore_synthesize(corpus, graph, plan, max_steps=5, verbose=True):
    docs = _corpus_docs(corpus)
    all_q = []
    for item in plan.get("items", []):
        cap, n = item.get("capability"), item.get("n", 0)
        for _qi in range(n):
            history, final = [], None
            for step in range(max_steps):
                action = _explore_step(cap, graph, history, max_steps - step)
                tool = action.get("tool")
                if tool == "synthesize":
                    final = action
                    break
                if tool == "search":
                    obs = _tool_search(docs, action.get("arg", ""))
                elif tool == "read":
                    obs = _tool_read(docs, action.get("arg", ""))
                else:
                    obs = "未知 action(请用 search/read/synthesize)"
                history.append({"action": action, "obs": obs})
            if final is None:
                final = _explore_step(cap, graph, history, 0, force=True)
            if final and final.get("tool") == "synthesize":
                final["capability"] = cap
                final["explore_steps"] = len(history)
                all_q.append(final)
        if verbose:
            print(f"[v9-explore] {cap}: 目标 {n} 题")
    return all_q


# ─────────────────────────────────────────────────────────────────────────────
# Stage F V9 — grounding(复用)+ ★硬判别器 + judge 多票
# ─────────────────────────────────────────────────────────────────────────────

TEACHER_SYSTEM = """你是答题助手。只依据【给定上下文】回答;上下文里没有就答"不知道"。
答案极简(一个词/人名/数值/短语),不要解释。"""


def _teacher_answer(question, context):
    ctx = context if context else "(无任何上下文)"
    user = f"【上下文】\n{ctx}\n\n【问题】{question}\n\n极简回答(没有就答'不知道'):"
    try:
        return (config.chat(
            [{"role": "system", "content": TEACHER_SYSTEM}, {"role": "user", "content": user}],
            temperature=0.0, max_tokens=2048) or "").strip()   # ★ reasoning 模型:长上下文需足预算,否则正文吐空
    except Exception:
        return "[error]"


def _memory_necessity(q, docs):
    """硬判别器:无语料 / 单文档 baseline 若能答中 → 伪记忆题。
    返回 'pass' | 'fail_commonsense' | 'fail_single_doc'。"""
    answer = q.get("answer", "")
    if q.get("capability") == "ABS":
        return "pass"  # 拒答题不走此闸(它考的是"不编造")
    # 模式 1:无语料(常识/题面泄漏可答?)
    a_blank = _teacher_answer(q.get("question"), "")
    if "不知道" not in a_blank and _answers_match(a_blank, answer):
        return "fail_commonsense"
    # 模式 2:单文档(只给 evidence 第一篇)
    spans = q.get("evidence_spans", [])
    if spans:
        did = spans[0].get("doc_id")
        single = docs.get(did, (None, ""))[1]
        if single:
            a_single = _teacher_answer(q.get("question"), single)
            if "不知道" not in a_single and _answers_match(a_single, answer):
                return "fail_single_doc"
    return "pass"


def _judge_voted(q, graph, k=3):
    """judge 跑 k 次,多数表决(治单 judge 两头摆)。"""
    npass = 0
    for _ in range(k):
        v = _judge(q, graph)
        if v.get("verdict") == "pass":
            npass += 1
    return {"verdict": "pass" if npass > k // 2 else "reject", "votes": f"{npass}/{k}"}


def stage_f_validate_v9(raw_questions, corpus, graph, verbose=True):
    doc_text, docs = {}, _corpus_docs(corpus)
    for did, (_s, c) in docs.items():
        doc_text[did] = c
    passed, rejects = [], []
    for q in raw_questions:
        cap = q.get("capability")
        # 闸 (a) grounding
        if cap != "ABS":
            spans = q.get("evidence_spans", [])
            if not spans:
                rejects.append({"q": q.get("question"), "gate": "grounding", "reason": "无 evidence"})
                continue
            ok = True
            for ev in spans:
                hit, score = _fuzzy_in(ev.get("span", ""), doc_text.get(ev.get("doc_id"), ""))
                if not hit:
                    ok = False
                    rejects.append({"q": q.get("question"), "gate": "grounding",
                                    "reason": f"evidence 不在语料(score={score:.2f})"})
                    break
            if not ok:
                continue
        # 闸 (b) ★ 硬判别器
        nec = _memory_necessity(q, docs)
        q["memory_necessity"] = nec
        if nec != "pass":
            rejects.append({"q": q.get("question"), "gate": "memory_necessity", "reason": nec})
            continue
        # 闸 (c) judge 多票
        v = _judge_voted(q, graph, k=3)
        q["judge"] = v
        if v.get("verdict") != "pass":
            rejects.append({"q": q.get("question"), "gate": "judge", "reason": f"多票 {v.get('votes')}"})
            continue
        q["gt_source"] = "llm+judge+硬判别器"
        passed.append(q)
    if verbose:
        print(f"[v9-validate] 通过 {len(passed)}/{len(raw_questions)}, reject {len(rejects)}")
        print(f"  reject by gate: {dict(Counter(r['gate'] for r in rejects))}")
    return passed, rejects
