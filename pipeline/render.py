"""
pipeline.render —— 文本渲染层(从 run_factory_v2 拆出,行为不变)。
把结构化产物渲染成自然语言文本:世界事实→语料文档(render_corpus + 助手),订单意图→题面(phrase_questions)。
"""
from __future__ import annotations
import json, threading
import config
from pipeline.world_state import _date_of, week_label, _to_num, EXPIRE, DELETE
from pipeline.lines import line_for
from pipeline.prompts import render


LEAK_BANNED = ["当前", "现在", "最新", "目前", "截至目前", "迄今", "至今", "一直", "历来",
               "维持", "保持不变", "累计", "现任", "如今", "始终", "仍为", "仍是", "依旧"]


def _corpus_system(profile) -> str:
    genres = "/".join(profile.get("doc_genres", ["周报", "通报", "邮件"]))
    stopped = profile.get("stopped_phrase", "停止统计")
    noun = profile.get("entity_noun", "实体")
    return render("corpus.system", noun=noun, genres=genres, stopped=stopped, genre0=genres.split("/")[0])


def _filler_system(profile) -> str:
    noun = profile.get("entity_noun", "实体")
    genres = "/".join(profile.get("doc_genres", ["通知", "纪要", "公告"]))
    return render("filler.system", noun=noun)


def _session_facts(ws, s):
    facts = []
    for ent, flds in ws.entities.items():
        for fname, tl in flds.items():
            op = next((o for o in tl.ops if o.session == s), None)
            if op is None:
                continue
            stopped = op.op in (EXPIRE, DELETE)
            facts.append({"entity": ent, "field": fname, "value": None if stopped else op.value, "stopped": stopped})
    return facts


def _tracked_blocklist(ws, profile=None):
    """草堆禁词表 = 实体名 + 字段名 + 所有【人名类字段】的取值(防 filler 撞被追踪的人名)。
    ★人名字段从白皮书 `field_schema.kind=="person"` 取(审计 ★1:删掉 '负责/汇报/经理' 中文子串启发式
    —— 那是 office 味、对非 office 域不可靠:medical 的「主治医师/会诊上级」一个 hint 都不匹配)。域知识只从白皮书来。"""
    out = set(ws.entities) | {f for flds in ws.entities.values() for f in flds}
    person_fields = {f.get("name") for f in (profile or {}).get("field_schema", []) if f.get("kind") == "person"}
    for flds in ws.entities.values():
        for fname, tl in flds.items():
            if fname in person_fields:
                for (_s, _d, v) in tl.set_values():
                    if v and _to_num(v) is None and len(str(v)) >= 2:
                        out.add(str(v))
    return out


def _render_conflict_docs(ws, s, date, tracer):
    """★L5:把 session==s 的小道矛盾值渲染成【低可信来源】文档(权威值由正常信号路径已渲,二者同周并存=语料真出现矛盾)。
    无 ws.conflicts(非 L5 场景)→ 返回 [],对其它场景零副作用。"""
    out = []
    for c in (getattr(ws, "conflicts", None) or []):
        if c.get("session") != s or not c.get("rumor_value"):
            continue
        o = tracer.chat_json("render.conflict",
            [{"role": "system", "content": render("conflict.system")},
             {"role": "user", "content": render("conflict.user", s=week_label(s), date=date, entity=c["entity"],
                                                field=c["field"], value=c["rumor_value"], source=c.get("rumor_source", "小道消息"))}],
            temperature=0.7, max_tokens=8192)
        # ★不套 LEAK_BANNED:矛盾文档天然是"据传【现在/目前】X 是 Y"的当期传闻,撞防剧透词表会被全滤。
        #   (LEAK_BANNED 是给【信号文档】防 KU 剧透的;小道文档是另一类——该说"现在"就说,正是冲突设定。)
        for d in (o.get("docs", []) if isinstance(o, dict) else []):
            if d.get("content"):
                out.append(d)
    return out


def _chunk(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def render_corpus(wp, ws, target_tokens, tracer, corpus, done_weeks, save_cb, log=print):
    profile = wp.get("domain_profile", {})
    sys_sig, sys_fil = _corpus_system(profile), _filler_system(profile)
    blocked = _tracked_blocklist(ws, profile)
    # 估算 filler/周 以达目标 token(~1字≈1token)。周并行后不再 early-stop;filler_per_week 已按目标分摊。
    n_sessions = ws.n_sessions
    filler_per_week = max(8, round(target_tokens / max(1, n_sessions) / 800))   # 每篇≈800字
    by_id = {x["session_id"]: x for x in corpus["sessions"]}
    weeks = [s for s in ws.sessions() if s not in done_weeks]
    lock = threading.Lock()

    def _render_week(s):                                  # ★一周的全部渲染 = 一个并行单元
        date = _date_of(s)
        facts = _session_facts(ws, s)
        bysku = {}
        for f in facts:
            bysku.setdefault(f["entity"], []).append(f)
        sig_groups = list(_chunk(list(bysku.items()), 2))   # ★2 实体/组:文档更聚焦、归属更清晰、逼渲全(6→3→2)
        n_batches = (filler_per_week + 5) // 6

        def _render_sig(grp):                             # ★信号块:渲全 + 渲对 —— 每个 (实体,变更) 必须【实体+值就近共现】才算渲到
            from pipeline.grounding import attributed, STOP_MARKERS   # 渲染期就用出厂接地的同一把尺
            gf = [f for _e, fs in grp for f in fs]
            # 待渲事实:非停用 → 验 (实体,值) 就近;停用 → 验 (实体,停用标记) 同篇
            want_val = [(f["entity"], f["field"], str(f["value"])) for f in gf if f.get("value") and not f.get("stopped")]
            want_stop = [(f["entity"], f["field"]) for f in gf if f.get("stopped")]
            grp_docs, hint = [], ""
            for _att in range(4):                         # 多给几次重渲机会,强制渲全(世界辛苦生成,必须全用上)
                out = tracer.chat_json("render.signal",
                    [{"role": "system", "content": sys_sig},
                     {"role": "user", "content": render("corpus.user", s=week_label(s), date=date, facts=json.dumps(gf, ensure_ascii=False), hint=hint)}],
                    temperature=0.6, max_tokens=8192)
                cand = [d for d in (out.get("docs", []) if isinstance(out, dict) else [])
                        if d.get("content") and not any(b in d["content"] for b in LEAK_BANNED)]
                contents = [d.get("content", "") for d in cand]
                missing = [f"{e}的「{fl}」={v}" for (e, fl, v) in want_val if not attributed(v, e, contents)]   # ★就近归属,非"值出现在某处"
                missing += [f"{e}的「{fl}」自本期停止" for (e, fl) in want_stop
                            if not any((e in c) and any(m in c for m in STOP_MARKERS) for c in contents)]
                grp_docs = cand or grp_docs
                if not missing:
                    break
                hint = (f"\n★ 上一版有这些事实【没写、或没把『该实体名』与『该值』写在同一句/相邻】(每条都必须让对应实体名与值就近出现,不能张冠李戴):"
                        f"{missing}。逐条重写进正文(仍只写本期、不用全局口径词)。")
            return grp_docs

        def _render_fil(ci):                              # 一个草堆批
            want = min(6, filler_per_week - ci * 6)
            if want <= 0:
                return []
            out = tracer.chat_json("render.filler",
                [{"role": "system", "content": sys_fil},
                 {"role": "user", "content": render("filler.user", s=week_label(s), date=date, want=want, blocked=sorted(blocked)[:30])}],
                temperature=0.9, max_tokens=8192)
            return [d for d in (out.get("docs", []) if isinstance(out, dict) else [])
                    if d.get("content") and not any(b in d.get("content", "") for b in blocked)]

        sig_lists = config.pmap(_render_sig, sig_groups, workers=8)              # 周内并发(全局信号量才是真上限)
        fil_lists = config.pmap(_render_fil, list(range(n_batches)), workers=8)
        docs = []                                          # 周内顺序编号,避免 race(doc_id 含 s,跨周不撞)
        for gl in sig_lists:
            for d in gl:
                d["doc_id"] = f"s{s}_sig_{len(docs)}"; docs.append(d)
        for fl in fil_lists:
            for d in fl:
                d.update({"doc_id": f"s{s}_fil_{len(docs)}", "is_filler": True, "fact_refs": []}); docs.append(d)
        for d in _render_conflict_docs(ws, s, date, tracer):      # ★L5:本周小道矛盾文档(非 L5 场景为空)
            d.update({"doc_id": f"s{s}_conf_{len(docs)}", "is_conflict": True, "fact_refs": []}); docs.append(d)
        with lock:                                         # 周乱序完成 → 锁内更新+逐周存盘(断点续渲不丢)
            by_id[s] = {"session_id": s, "date": date, "docs": docs}
            done_weeks.add(s)
            corpus["sessions"] = [by_id[k] for k in sorted(by_id)]
            save_cb()
            ch = sum(len(dd.get("content", "")) for x in corpus["sessions"] for dd in x["docs"])
            log(f"  [周 {s} ✓ {len(done_weeks)}/{n_sessions}] 累计 {sum(len(x['docs']) for x in corpus['sessions'])} 篇 / {ch/1e6:.2f}M 字")
        return s

    config.pmap(_render_week, weeks, workers=max(1, len(weeks)))   # ★周并行;在飞 API 由全局 LLM_CONCURRENCY 兜住
    ch = sum(len(dd.get("content", "")) for x in corpus["sessions"] for dd in x["docs"])
    log(f"  ✓ 渲染完成:{sum(len(x['docs']) for x in corpus['sessions'])} 篇 / {ch/1e6:.2f}M 字(目标 {target_tokens/1e6:.1f}M)")


PHRASE_SYS = render("phrase.system")


def phrase_questions(orders, wp, tracer, log=print) -> list[dict]:
    def _ph(o):                                           # 每条订单独立 → 并发出题
        line = line_for(o.get("line", ""))                # 出题意图/须隐藏 = 各产线自己的 intent()
        if line is None:                                  # 兜底(订单都来自已建线,理论不触发)
            return {**o, "question": ""}
        intent, hide = line.intent(o)
        out = tracer.chat_json("phrase",
            [{"role": "system", "content": PHRASE_SYS},
             {"role": "user", "content": render("phrase.user", intent=intent, hide=hide)}],
            temperature=0.5, max_tokens=2048)
        return {**o, "question": (out.get("question", "") if isinstance(out, dict) else "")}
    raw = config.pmap(_ph, orders, workers=8)
    qs = [q for q in raw if q.get("question", "").strip()]    # 丢并发下偶发的空题面
    dropped = len(raw) - len(qs)
    by_line = {}
    for q in qs:
        by_line[q.get("line", "?")] = by_line.get(q.get("line", "?"), 0) + 1
    log(f"  ④ 出题:{len(qs)} 题已润色(丢空 {dropped};桥实体/答案不进题面);by_line {by_line}")
    return qs

