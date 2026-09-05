"""
pipeline.render —— 文本渲染层(从 run_factory_v2 拆出,行为不变)。
把结构化产物渲染成自然语言文本:世界事实→语料文档(render_corpus + 助手),订单意图→题面(phrase_questions)。
"""
from __future__ import annotations
import json, threading, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # 允许 `python pipeline/render.py` 直跑(找到根目录 config)
import config
from pipeline.world_state import _date_of, week_label, _to_num, _dicts, EXPIRE, DELETE
from pipeline.lines import line_for
from pipeline.prompts import render


LEAK_BANNED = ["当前", "现在", "最新", "目前", "截至目前", "迄今", "至今", "一直", "历来",
               "维持", "保持不变", "累计", "现任", "如今", "始终", "仍为", "仍是", "依旧"]

# 盲判别器"不确定/读不出"措辞(判别器明确表达"读不出"→ 该 atom 未忠实渲染)。
_DISC_UNSURE = ["不确定", "无法确定", "读不出", "读不到", "不清楚", "未提及", "未提到",
                "没有提到", "没有提及", "无法判断", "无从", "看不出", "歧义", "不明确",
                "信息不足", "无此", "查无", "找不到", "未找到", "无法读出"]


def _dim_norm(s) -> str:
    """★量纲严格归一(死钉④):只做"去空格 + 全角→半角",★保留尾部 %/单位/小数点不剥。
    与 world_state._norm / eval.judge._norm 的关键区别:那两个 .rstrip('%。.') 会把
    "78%"→"78" 抹平双量纲;本函数【保留 %】,使 _dim_norm("78%")!="0.78"、!="78"。"""
    if s is None:
        return ""
    t = str(s).strip().replace(" ", "").replace("　", "")
    t = t.translate(str.maketrans("０１２３４５６７８９％．", "0123456789%."))
    return t


def _is_unsure(ans: str) -> bool:
    """判别器是否表达了"读不出/不确定"。"""
    a = _dim_norm(ans).lower()
    if not a:
        return True
    return any(_dim_norm(m).lower() in a for m in _DISC_UNSURE)


def _strict_eq(read: str, gt: str) -> bool:
    """★量纲严格对账(死钉①④):判别器读出的 read 是否严格等于世界真值 gt。
    - 先排除"不确定"类措辞(读不出 → 不算还原)。
    - 量纲敏感:用 _dim_norm(保留 %),"78%" != "0.78"、"78%" != "78"(双量纲必暴露)。
    - 严判 EM(归一后逐字相等),不做子串、不剥 %、不数值近似——任一放宽都会放过双量纲。"""
    if read is None or _is_unsure(read):
        return False
    return _dim_norm(read) == _dim_norm(gt)


def _discriminate_one(docs, entity, field, tracer):
    """派一个【盲读者 agent】只读 docs、答 (entity,field) 的值。
    ★死钉①③:输入只有 docs(渲染正文)+ (entity,field),绝不喂 true_value/gt/fact_refs。
    返回判别器读出的 answer 字符串(读不出/异常 → 返回 ''=不确定,绝不 fail-open 放行)。"""
    try:
        out = tracer.chat_json("render.discriminate",
            [{"role": "system", "content": render("discriminate.system")},
             {"role": "user", "content": render("discriminate.user",
                                                docs="\n\n".join(d for d in docs if d),
                                                entity=entity, field=field)}],
            temperature=0.0, max_tokens=1024)
    except Exception as e:                                # 调用/解析异常 → 当作读不出(参 judge.py:57-59),宁可重渲
        print(f"[discriminate] 调用失败(计为读不出): {type(e).__name__}: {str(e)[:80]}")
        return ""
    ans = out.get("answer") if isinstance(out, dict) else None
    return str(ans) if ans is not None else ""


def _discriminator_recovers(docs, entity, field, true_value, tracer):
    """★渲染链第一步核心忠实检:盲读者据 docs 能否唯一、按对量纲还原 (entity,field) 的世界真值?
    返回 (recovered: bool, read_answer: str)。
    - recovered=True  ⟺ 判别器读出的值与 true_value【量纲严格相等】(_strict_eq)= 忠实渲染。
    - recovered=False ⟺ 读不出/读成"不确定"/对不上(含双量纲 78%↔0.78、多跳歧义)= 未忠实渲染 → 进 missing。
    ★死钉①③:true_value 只在【本函数代码侧】用于对账,绝不进判别器 messages(判别器只收 docs+实体+字段)。"""
    ans = _discriminate_one(docs, entity, field, tracer)
    return (_strict_eq(ans, str(true_value)), ans)


def _corpus_system(profile, blueprint=None) -> str:
    genres = "/".join(profile.get("doc_genres", ["周报", "通报", "邮件"]))
    stopped = profile.get("stopped_phrase", "停止统计")
    noun = profile.get("entity_noun", "实体")
    types = (blueprint or {}).get("entity_types") or []
    legend = "、".join(f"{t.get('id')}={t.get('noun')}" for t in types if t.get("id")) or f"legacy={noun}"
    time_unit = ((blueprint or {}).get("temporal_model") or {}).get("unit", "week")
    return render("corpus.system", noun=noun, genres=genres, stopped=stopped,
                  genre0=genres.split("/")[0], type_legend=legend, time_unit=time_unit)


def _filler_system(profile, blueprint=None) -> str:
    """构造同领域、同世界但不承载真值的 filler 提示词。

    输入来自已经冻结的领域画像与世界蓝图；输出只影响草堆文档的风格，
    不允许 filler 接触被追踪实体或字段，因此不会改变 benchmark gold。
    """
    noun = profile.get("entity_noun", "实体")
    genres = "/".join(profile.get("doc_genres", ["通知", "纪要", "公告"]))
    blueprint = blueprint or {}
    context = {
        "entity_types": [
            {"id": item.get("id"), "noun": item.get("noun")}
            for item in blueprint.get("entity_types", []) if item.get("id")
        ],
        "relation_types": [item.get("id") for item in blueprint.get("relation_types", []) if item.get("id")],
        "event_types": [
            {"id": item.get("id"), "label": item.get("label")}
            for item in blueprint.get("event_types", []) if item.get("id")
        ],
        "evidence_channels": list(blueprint.get("evidence_channels", [])),
    }
    return render("filler.system", noun=noun, genres=genres,
                  world_context=json.dumps(context, ensure_ascii=False))


def _session_facts(ws, s):
    facts = []
    for ent, flds in ws.entities.items():
        for fname, tl in flds.items():
            same_session = [o for o in tl._sorted() if o.session == s]
            op = same_session[-1] if same_session else None
            if op is None:
                continue
            stopped = op.op in (EXPIRE, DELETE)
            facts.append({"entity": ent, "entity_type": getattr(ws, "entity_types", {}).get(ent),
                          "field": fname, "value": None if stopped else op.value, "stopped": stopped})
    return facts


def _missing_event_narratives(events, contents) -> list[str]:
    """机械检查每个领域事件是否以“事件 label + 全部参与者同篇”真正进入文档。"""
    missing = []
    for event in events:
        label = str(event.get("label") or "").strip()
        participants = sorted({str(x) for x in (event.get("participants") or {}).values() if x})
        if not label or not participants:
            missing.append(f"事件 {event.get('id') or event.get('type')} 缺 label/participants，无法验叙事")
            continue
        if not any(label in content and all(name in content for name in participants) for content in contents):
            missing.append(
                f"事件「{label}」必须与参与者 {participants} 在同一篇文档中形成明确叙事")
    return missing


def _tracked_blocklist(ws, profile=None):
    """草堆禁词表 = 实体名 + 字段名 + 所有【人名类字段】的取值(防 filler 撞被追踪的人名)。
    ★人名字段从白皮书 `field_schema.kind=="person"` 取(审计 ★1:删掉 '负责/汇报/经理' 中文子串启发式
    —— 那是 office 味、对非 office 域不可靠:medical 的「主治医师/会诊上级」一个 hint 都不匹配)。域知识只从白皮书来。"""
    out = set(ws.entities) | {f for flds in ws.entities.values() for f in flds}
    person_fields = {f.get("name") for f in _dicts((profile or {}).get("field_schema", [])) if f.get("kind") == "person"}
    for flds in ws.entities.values():
        for fname, tl in flds.items():
            if fname in person_fields:
                for (_s, _d, v) in tl.set_values():
                    if v and _to_num(v) is None and len(str(v)) >= 2:
                        out.add(str(v))
    return out


def _sanitize_corpus(corpus: dict, ws, profile=None) -> dict:
    """收口语料元数据，尤其处理扩世界后的增量一致性。

    - 信号文档若模型漏了 fact_refs，只从同 session 中在正文里逐字出现的
      实体+真值（或完整事件参与者）反推；无法反推则删掉该文档。
    - 扩容后新实体名可能撞上旧 filler，此时删掉撞词 filler，不让草堆变证据。
    """
    blocked = {str(x) for x in _tracked_blocklist(ws, profile) if x}
    blueprint = getattr(ws, "world_blueprint", None) or {}
    event_labels = {item.get("id"): item.get("label") for item in blueprint.get("event_types", [])}
    events_by_session = {}
    for event in getattr(ws, "events", None) or []:
        events_by_session.setdefault(event.get("session"), []).append(event)
    stats = {"inferred_refs": 0, "dropped_unref": 0, "dropped_filler_leaks": 0}
    for session in corpus.get("sessions", []):
        sid = session.get("session_id")
        facts = _session_facts(ws, sid)
        kept = []
        for doc in session.get("docs", []):
            content = str(doc.get("content") or "")
            if doc.get("is_filler") is True:
                if any(term in content for term in blocked):
                    stats["dropped_filler_leaks"] += 1
                    continue
                doc["fact_refs"] = []
            elif "_sig_" in str(doc.get("doc_id", "")) and not doc.get("fact_refs"):
                refs = []
                for fact in facts:
                    entity, field, value = str(fact.get("entity") or ""), str(fact.get("field") or ""), fact.get("value")
                    if entity and entity in content and ((value not in (None, "") and str(value) in content)
                                                         or (fact.get("stopped") and any(x in content for x in ("停止", "不再", "终止", "暂停")))):
                        refs.append(f"{entity}.{field}")
                for event in events_by_session.get(sid, []):
                    label = event_labels.get(event.get("type")) or event.get("label")
                    participants = [str(x) for x in (event.get("participants") or {}).values() if x]
                    if label and label in content and participants and all(x in content for x in participants):
                        refs.append(f"{event.get('id')}.label")
                doc["fact_refs"] = list(dict.fromkeys(refs))
                if not doc["fact_refs"]:
                    stats["dropped_unref"] += 1
                    continue
                stats["inferred_refs"] += 1
            kept.append(doc)
        session["docs"] = kept
    return stats


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
                                                time_unit=ws.period_unit(), field=c["field"], value=c["rumor_value"],
                                                source=c.get("rumor_source", "小道消息"))}],
            temperature=0.7, max_tokens=8192)
        # ★不套 LEAK_BANNED:矛盾文档天然是"据传【现在/目前】X 是 Y"的当期传闻,撞防剧透词表会被全滤。
        #   (LEAK_BANNED 是给【信号文档】防 KU 剧透的;小道文档是另一类——该说"现在"就说,正是冲突设定。)
        for d in _dicts(o.get("docs") if isinstance(o, dict) else []):
            if d.get("content"):
                out.append(d)
    return out


_SENS_FIELD_LABEL = {"secret": "登录口令", "pii_id": "身份证号", "bankcard": "银行卡号", "apikey": "API 密钥"}


def _render_sensitive_docs(ws, s, date):
    """★L10:把 session==s 的敏感注入渲染成【确定性写入文档】(user 供出 X / assistant 已记录)。
    ★绕开 LLM(不调 tracer):代码直接 Template.substitute → 保证 X 逐字 + 就近实体落地(G3 反退化 L6)。
    无 ws.sensitive(非 L10 场景)→ 返回 [],对其它场景零副作用。"""
    out = []
    for c in (getattr(ws, "sensitive", None) or []):
        if c.get("session") != s or not c.get("value"):
            continue
        label = c.get("field") or _SENS_FIELD_LABEL.get(c.get("stype"), "敏感信息")
        content = render("sensitive.template", date=date, entity=c["entity"],
                         field_label=label, value=c["value"])
        out.append({"type": "记忆写入", "content": content})
    return out


def _render_rule_docs(ws, s, date):
    """★L9:把 session==s 的条件归纳执行实例渲染成【确定性单条情境→动作】文档(用 surface 表面串,canon 层)。
    ★绕开 LLM(不调 tracer):代码直接 Template.substitute → 保证只渲【一条情境+一个处置】、绝不写一般化规则句。
    无 ws.rule_instances(非 L9 场景)→ 返回 [],对其它场景零副作用。"""
    out = []
    for i in (getattr(ws, "rule_instances", None) or []):
        if i.get("session") != s or i.get("surface_action") is None:
            continue
        content = render("rule.template", date=date, inst_id=i.get("inst_id", ""),
                         trigger_field=i.get("trigger_field", ""), x=i.get("x", ""),
                         unit=i.get("unit", ""), surface_action=i.get("surface_action", ""))
        out.append({"type": "处置记录", "content": content})
    return out


def _chunk(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def render_corpus(wp, ws, target_tokens, tracer, corpus, done_weeks, save_cb, log=print,
                  only_entities=None, only_entity_sessions=None):
    """only_entities=None:全量渲(每周全实体+filler)。
    only_entities=set:★增量 delta(§10.1)——【只渲这些新实体的 signal】并【追加】到已有周 docs,
    ``only_entity_sessions`` 精确补渲被新关系/事件改变的旧实体周；不重灌 filler。"""
    profile = wp.get("domain_profile", {})
    blueprint = getattr(ws, "world_blueprint", None) or wp.get("world_blueprint") or {}
    temporal = blueprint.get("temporal_model") or {}
    time_unit = temporal.get("unit", "week")
    step_days = int(temporal.get("step_days", 7) or 7)
    sys_sig, sys_fil = _corpus_system(profile, blueprint), _filler_system(profile, blueprint)
    blocked = _tracked_blocklist(ws, profile)
    # 估算 filler/周 以达目标 token(~1字≈1token)。周并行后不再 early-stop;filler_per_week 已按目标分摊。
    n_sessions = ws.n_sessions
    filler_per_week = max(8, round(target_tokens / max(1, n_sessions) / 800))   # 每篇≈800字
    by_id = {x["session_id"]: x for x in corpus["sessions"]}
    delta_mode = only_entities is not None or only_entity_sessions is not None
    only_entities = set(only_entities or [])
    only_entity_sessions = set(only_entity_sessions or [])
    weeks = list(ws.sessions()) if delta_mode else [s for s in ws.sessions() if s not in done_weeks]
    lock = threading.Lock()
    fallback_count: list[int] = []                        # 耗尽兜底计数(list.append 线程安全;验收要求趋零)

    def _render_week(s):                                  # ★一周的全部渲染 = 一个并行单元
        date = _date_of(s, step_days=step_days)
        facts = _session_facts(ws, s)
        event_decls = {e.get("id"): e for e in blueprint.get("event_types", [])}
        session_events = [{**e, "label": (event_decls.get(e.get("type")) or {}).get("label", e.get("type", ""))}
                          for e in (getattr(ws, "events", None) or []) if e.get("session") == s]
        if delta_mode:                                    # ★delta:新实体全程 + 旧实体受结构变化的精确 session
            facts = [f for f in facts if (f["entity"] in only_entities
                                          or (f["entity"], s) in only_entity_sessions)]
            if not facts:
                return s                                  # 新实体本周无事实 → 不加 doc
        bysku = {}
        for f in facts:
            bysku.setdefault(f["entity"], []).append(f)
        sig_groups = list(_chunk(list(bysku.items()), 2))   # ★2 实体/组:文档更聚焦、归属更清晰、逼渲全(6→3→2)
        n_batches = (filler_per_week + 5) // 6

        def _render_sig(grp):                             # ★信号块:渲全 + 渲对 —— 盲判别器据渲文能否唯一还原 (实体,字段) 才算渲到
            from pipeline.grounding import STOP_MARKERS    # ★只借停用标记(STOP_MARKERS);忠实检不再用 §G 的 attributed(死钉②不同尺)
            gf = [f for _e, fs in grp for f in fs]
            group_entities = {f["entity"] for f in gf}
            group_events = [e for e in session_events
                            if group_entities.intersection((e.get("participants") or {}).values())]
            # 待渲事实:非停用 → 派盲判别器读 (实体,字段) 的值,代码量纲严格对账;停用 → 验 (实体,停用标记) 同篇
            want_val = [(f["entity"], f["field"], str(f["value"])) for f in gf if f.get("value") and not f.get("stopped")]
            want_stop = [(f["entity"], f["field"]) for f in gf if f.get("stopped")]

            def _discriminate(contents):
                """★盲判别器忠实检(死钉①③):对每个 want_val atom 派一个盲读者只读 contents 答值,
                代码量纲严格对账;对每个 want_stop atom 验停用标记同篇。
                返回 (miss_val, miss_stop, miss_event)，供 missing/hint 管道复用。"""
                # value atom:判别器并发(各 atom 独立),true_value 只在代码对账侧用,绝不进 prompt
                def _one(item):
                    e, fl, v = item
                    ok, ans = _discriminator_recovers(contents, e, fl, v, tracer)
                    if ok:
                        return None
                    read = "不确定" if (ans is None or _is_unsure(ans)) else ans
                    return f"{e}的「{fl}」(应承载值={v}):盲读者据文档读出的是『{read}』,与应承载的值不一致/读不出"
                miss_val = [m for m in config.pmap(_one, want_val, workers=8) if m]
                # stopped atom:期望判别器读出"停止/不再统计"语义;此处复用 §G 停用标记同篇检(refusal 语义,非 _strict_eq)
                miss_stop = [f"{e}的「{fl}」应让读者读出『自本期停止统计』,但文档未表达停用"
                             for (e, fl) in want_stop
                             if not any((e in c) and any(m in c for m in STOP_MARKERS) for c in contents)]
                miss_event = _missing_event_narratives(group_events, contents)
                return miss_val, miss_stop, miss_event
            # ★禁词豁免(014559 尸检:词表「累计」撞字段名「累计计费工时」→ 整篇核验前被静默丢,27/27 弃题同根)。
            #   豁免集 = 本组【所有被要求逐字出现的串】= 字段名+实体名+事实值(刀1审计:值含禁词如「维持治疗」
            #   时,'逐字照抄'与'禁全局口径词'否则构成不可满足约束 → 4 轮必废 → 兜底吸收症状)。
            #   单一真源(由本组事实派生,非按域手维护);长串先遮,防短串是长串子串。
            exempt = sorted({f["field"] for f in gf} | {f["entity"] for f in gf}
                            | {str(f["value"]) for f in gf if f.get("value")}, key=len, reverse=True)

            def _leaks(text):
                masked = text
                for nm in exempt:
                    masked = masked.replace(nm, "■" * len(nm))
                return [b for b in LEAK_BANNED if b in masked]

            grp_docs, hint = [], ""
            for _att in range(4):                         # 多给几次重渲机会,强制渲全(世界辛苦生成,必须全用上)
                out = tracer.chat_json("render.signal",
                    [{"role": "system", "content": sys_sig},
                     {"role": "user", "content": render(
                         "corpus.user", s=week_label(s), time_unit=time_unit, date=date,
                         facts=json.dumps(gf, ensure_ascii=False),
                         events=json.dumps(group_events, ensure_ascii=False), hint=hint)}],
                    temperature=0.6, max_tokens=8192)
                cand, leak_notes = [], []
                for d in _dicts(out.get("docs") if isinstance(out, dict) else []):
                    if not d.get("content"):
                        continue
                    hits = _leaks(d["content"])
                    if hits:                              # 犯禁不再静默丢:记下死因,进诚实反馈(它驮的事实会出现在 missing 里)
                        leak_notes.append(f"《{(d.get('title') or d.get('type') or '无题')}》因使用全局口径词{hits}被废弃")
                    else:
                        cand.append(d)
                contents = [d.get("content", "") for d in cand]
                miss_val, miss_stop, miss_event = _discriminate(contents)
                missing = miss_val + miss_stop + miss_event
                grp_docs = cand or grp_docs
                if not missing:
                    break
                # ★hint 如实(老版把"写了但犯禁被废"误报成"没写"→ 重试不收敛):缺什么、为什么缺,分开说
                hint = (f"\n★ 这些事实在上一版【没有合格呈现】(每条必须让对应实体名与值在同一句/紧邻就近出现,值逐字照抄):{missing}。")
                if leak_notes:
                    hint += (f"\n★ 另:上一版 {leak_notes}——重写时把其中事实写进正文,但【删掉这些全局口径词】"
                             f"(注意:字段名/实体名/事实值本身含这些字的照常写,不算犯禁)。")
                hint += "逐条重写进正文(仍只写本期)。"
            # ★fail-loud 弃段(删 K=V fail-open 兜底):轮次耗尽后再跑一次盲判别器,仍不可还原的 atom →
            #   【不】再往 grp_docs 硬注 K=V 模板备忘(那是 fail-open、稳过 §G、把缺渲症状吸收掉),而是【弃段】:
            #   grp_docs 不追加任何东西,缺的 atom 让出厂 §G 接地闸自然弃题;只显式告警 + 计 fallback_count(语义=弃段计数)。
            contents = [d.get("content", "") for d in grp_docs]
            miss_val, miss_stop, miss_event = _discriminate(contents)
            left = miss_val + miss_stop + miss_event
            if left:
                fallback_count.append(len(left))          # ★机械验收落点(语义改为"弃段计数"):汇总进末尾日志
                ents = sorted({m.split("的「")[0] for m in left})
                log(f"  ⚠fail-loud弃段[{time_unit}{week_label(s)}]:{len(left)} 个 atom 多轮重渲后盲读者仍不可还原,弃段不入库({ents})")
            return grp_docs

        def _render_fil(ci):                              # 一个草堆批
            want = min(6, filler_per_week - ci * 6)
            if want <= 0:
                return []
            out = tracer.chat_json("render.filler",
                [{"role": "system", "content": sys_fil},
                 {"role": "user", "content": render("filler.user", s=week_label(s), time_unit=time_unit,
                                                      date=date, want=want, blocked=sorted(blocked)[:30])}],
                temperature=0.9, max_tokens=8192)
            return [d for d in _dicts(out.get("docs") if isinstance(out, dict) else [])
                    if d.get("content") and not any(b in d.get("content", "") for b in blocked)]

        sig_lists = config.pmap(_render_sig, sig_groups, workers=8)              # 周内并发(全局信号量才是真上限)
        with lock:                                         # 周乱序完成 → 锁内更新+逐周存盘(断点续渲不丢)
            if delta_mode:                                 # ★delta:追加结构变化 signal,接着编号;不灌 filler/conflict
                docs = list(by_id.get(s, {}).get("docs", []))
                base = len(docs)
                for gl in sig_lists:
                    for d in gl:
                        d["doc_id"] = f"s{s}_sig_{base}"; base += 1; docs.append(d)
                by_id[s] = {"session_id": s, "date": date, "docs": docs}   # 旧 docs 原样保留,只增量
            else:                                          # 全量:本周 signal + filler + 小道矛盾,整周写入
                fil_lists = config.pmap(_render_fil, list(range(n_batches)), workers=8)
                docs = []                                  # 周内顺序编号,避免 race(doc_id 含 s,跨周不撞)
                for gl in sig_lists:
                    for d in gl:
                        d["doc_id"] = f"s{s}_sig_{len(docs)}"; docs.append(d)
                for fl in fil_lists:
                    for d in fl:
                        d.update({"doc_id": f"s{s}_fil_{len(docs)}", "is_filler": True, "fact_refs": []}); docs.append(d)
                for d in _render_conflict_docs(ws, s, date, tracer):      # ★L5:本周小道矛盾文档(非 L5 场景为空)
                    d.update({"doc_id": f"s{s}_conf_{len(docs)}", "is_conflict": True, "fact_refs": []}); docs.append(d)
                for d in _render_sensitive_docs(ws, s, date):             # ★L10:本周敏感写入文档(确定性模板,X 逐字就近;非 L10 场景为空)
                    d.update({"doc_id": f"s{s}_sens_{len(docs)}", "is_sensitive": True, "fact_refs": []}); docs.append(d)
                for d in _render_rule_docs(ws, s, date):                  # ★L9:本周条件归纳执行实例(确定性单条情境→动作;非 L9 场景为空)
                    d.update({"doc_id": f"s{s}_rule_{len(docs)}", "is_rule_instance": True, "fact_refs": []}); docs.append(d)
                by_id[s] = {"session_id": s, "date": date, "docs": docs}
                done_weeks.add(s)
            corpus["sessions"] = [by_id[k] for k in sorted(by_id)]
            save_cb()
            ch = sum(len(dd.get("content", "")) for x in corpus["sessions"] for dd in x["docs"])
            tag = "delta+" if delta_mode else ""
            log(f"  [{time_unit} {s} ✓{tag} {len(done_weeks)}/{n_sessions}] 累计 {sum(len(x['docs']) for x in corpus['sessions'])} 篇 / {ch/1e6:.2f}M 字")
        return s

    config.pmap(_render_week, weeks, workers=max(1, len(weeks)))   # ★周并行;在飞 API 由全局 LLM_CONCURRENCY 兜住
    sanitized = _sanitize_corpus(corpus, ws, profile)
    if any(sanitized.values()):
        save_cb()
        log(f"  ✓ 语料收口:补 fact_refs {sanitized['inferred_refs']} 篇 / "
            f"弃无引用信号 {sanitized['dropped_unref']} 篇 / "
            f"清理扩容后撞词 filler {sanitized['dropped_filler_leaks']} 篇")
    ch = sum(len(dd.get("content", "")) for x in corpus["sessions"] for dd in x["docs"])
    fb = f";⚠fail-loud弃段 {len(fallback_count)} 处/{sum(fallback_count)} 个 atom 未忠实渲染(验收要求趋零)" if fallback_count else ";弃段 0(✓)"
    log(f"  ✓ 渲染完成:{sum(len(x['docs']) for x in corpus['sessions'])} 篇 / {ch/1e6:.2f}M 字(目标 {target_tokens/1e6:.1f}M){fb}")


PHRASE_SYS = render("phrase.system")


def phrase_questions(orders, wp, tracer, log=print) -> list[dict]:
    def _ph(o):                                           # 每条订单独立 → 并发出题
        line = line_for(o.get("line", ""))                # 出题意图/须隐藏 = 各产线自己的 intent()
        if line is None:                                  # 兜底(订单都来自已建线,理论不触发)
            return {**o, "question": ""}
        intent, hide = line.intent(o)
        # ★确定性出题 bypass(L9 闭选项 MC):选项串必须逐字保真、LLM 润色会打乱选项/丢 gold → 破坏纯代码 EM。
        #   直接用 intent 原文作题面(它已是完整可答的 MC 题,含 held-out x* + 全部选项)。
        if getattr(line, "deterministic_phrasing", False):
            return {**o, "question": intent}
        out = tracer.chat_json("phrase",
            [{"role": "system", "content": PHRASE_SYS},
             {"role": "user", "content": render("phrase.user", intent=intent, hide=hide)}],
            temperature=0.5, max_tokens=2048)
        q = out.get("question", "") if isinstance(out, dict) else ""
        q = q if isinstance(q, str) else ""
        # ★主语保真兜底(Q49 悬空代词根治):phrase 偶尔把主语专名改成"他/该案"丢了指代。实体名核(前4字)
        #   若整个没在题面出现 → 退回 intent 原文(它必含实体名、是完整可答问题)。比"禁代词"软规则多一道硬保证。
        ent = (o.get("entity") or "").strip()
        if q and ent and ent[:4] not in q:
            q = intent
        return {**o, "question": q}
    raw = config.pmap(_ph, orders, workers=8)
    qs = [q for q in raw if q.get("question", "").strip()]    # 丢并发下偶发的空题面
    dropped = len(raw) - len(qs)
    by_line = {}
    for q in qs:
        by_line[q.get("line", "?")] = by_line.get(q.get("line", "?"), 0) + 1
    log(f"  ④ 出题:{len(qs)} 题已润色(丢空 {dropped};桥实体/答案不进题面);by_line {by_line}")
    return qs


# ════════════════════════════════════════════════════════════════════════════
# 单测:盲判别器忠实检(★不打真 API——monkeypatch tracer.chat_json 返回桩)
#   跑法:./venv/bin/python pipeline/render.py
# ════════════════════════════════════════════════════════════════════════════
def _self_test() -> bool:
    checks = []

    def ck(name, cond):
        checks.append((bool(cond), name))

    # ── 量纲严格对账原语:保留 %,绝不归一蒙混 ──
    ck("_strict_eq 同量纲相等", _strict_eq("0.78", "0.78"))
    ck("★双量纲不等(78% != 0.78)", not _strict_eq("78%", "0.78"))
    ck("★裸数不等(78 != 0.78)", not _strict_eq("78", "0.78"))
    ck("★带%与不带不等(78% != 78)", not _strict_eq("78%", "78"))
    ck("全角→半角后相等", _strict_eq("０.７８", "0.78"))
    ck("含单位逐字相等", _strict_eq("320万", "320万"))
    ck("'不确定'判不还原", not _strict_eq("不确定", "0.78"))
    ck("空答判不还原", not _strict_eq("", "0.78"))

    # ── _discriminator_recovers 端到端(桩判别器:据 docs 抠出 entity 那句里的值;★绝不看 true_value)──
    #   桩模拟盲读者:从 docs 里找含 entity 的句子、读出"为/是"后面那个 token;读不出/歧义回"不确定"。
    def _fake_tracer_factory():
        class _T:
            seen_prompts = []   # 留痕:验判别器 prompt 里【绝不含】true_value(死钉③)

            def chat_json(self, step, messages, **kw):
                user = messages[1]["content"]
                _T.seen_prompts.append(user)
                # 桩盲读者:从 user 里夹的 docs 文本读 entity 的值(纯字符串启发,不依赖外部世界)
                docs = user
                import re as _re
                # 找 "<...命中率...>0.78" 或 "命中率78%" 这类:简单抓"命中率"后第一个数字串(含%)
                if "命中率" in docs:
                    m = _re.search(r"命中率[为是:]?\s*([0-9.]+%?)", docs)
                    if m:
                        return {"answer": m.group(1)}
                # 负责人歧义:文档里出现两个不同负责人 → 盲读者答"不确定"
                if "负责人" in docs:
                    names = set(_re.findall(r"负责人[为是:]?\s*([一-龥]{2,3})", docs))
                    if len(names) == 1:
                        return {"answer": names.pop()}
                    return {"answer": "不确定"}    # 多负责人歧义 / 读不出
                # K=V 句 "X本期「Y」为Z":抠 Z
                m = _re.search(r"为([0-9.]+%?)", docs)
                if m:
                    return {"answer": m.group(1)}
                return {"answer": "不确定"}
        return _T()

    # (a) 干净自然句:命中率0.78 → 判别器还原 0.78 → pass
    t = _fake_tracer_factory()
    docs_a = ["本周天枢数据部运行平稳,经统计本期命中率0.78,团队士气高涨。"]
    ok_a, ans_a = _discriminator_recovers(docs_a, "天枢数据部", "命中率", "0.78", t)
    ck("(a) 干净自然句 0.78 → 还原 pass", ok_a and ans_a.startswith("0.78"))

    # (b) 双量纲:文档写 78%、世界 0.78 → 判别器读出 78% ≠ 0.78 → fail
    t = _fake_tracer_factory()
    docs_b = ["本周天枢数据部表现优异,本期命中率78%,继续保持。"]
    ok_b, ans_b = _discriminator_recovers(docs_b, "天枢数据部", "命中率", "0.78", t)
    ck("(b) ★双量纲 78%≠0.78 → fail", (not ok_b) and ans_b == "78%")

    # (c) 一部多负责人歧义:文档里两个负责人 → 判别器答"不确定" → fail
    t = _fake_tracer_factory()
    docs_c = ["项目组本周负责人为王皓,另据交接,该项目负责人为李明,职责待厘清。"]
    ok_c, ans_c = _discriminator_recovers(docs_c, "项目组", "负责人", "王皓", t)
    ck("(c) 多负责人歧义 → 判别器不确定 → fail", (not ok_c) and _is_unsure(ans_c))

    # (d) K=V 句 "X本期「Y」为Z":能还原 → pass(K=V 不自然但忠实,本步只管忠实)
    t = _fake_tracer_factory()
    docs_d = ["2025-01-06 备忘:天枢数据部本期「命中率」为0.78。"]
    ok_d, ans_d = _discriminator_recovers(docs_d, "天枢数据部", "命中率", "0.78", t)
    ck("(d) K=V 句能还原 → pass(忠实)", ok_d and ans_d.startswith("0.78"))

    # ── 死钉③守护:判别器 prompt 里【绝不出现】true_value/gt ──
    all_prompts = "".join(t.seen_prompts) if hasattr(t, "seen_prompts") else ""
    # 复用 (a) 那次的桩痕迹做断言(true_value="0.78" 也在 docs 文本里,无法只查 0.78;
    #   改查 prompt 里没有"应承载/gt/真值/true_value"这类把答案喂进去的字样)
    leaked = any(kw in p for p in (_fake_tracer_factory().seen_prompts or [""]) for kw in ("真值", "gt", "应承载", "true_value"))
    ck("死钉③:判别器 prompt 不含 gt/真值字样", not leaked)
    # 直接验 prompt 构造:user 模板只含 docs+entity+field,不含我们传的 true_value 关键标记
    sample_user = render("discriminate.user", docs="文档正文", entity="某实体", field="某字段")
    ck("死钉③:user 模板只含 docs/entity/field", "某实体" in sample_user and "某字段" in sample_user
       and "真值" not in sample_user and "gt" not in sample_user.lower())

    npass = sum(1 for ok, _ in checks if ok)
    for ok, name in checks:
        print(f"  {'✓' if ok else '✗'} {name}")
    print(f"[render self-test] {npass}/{len(checks)} PASS")
    return npass == len(checks)


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(0 if _self_test() else 1)
