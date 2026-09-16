"""
pipeline.render —— 文本渲染层(从 run_factory_v2 拆出,行为不变)。
把结构化产物渲染成自然语言文本:世界事实→语料文档(render_corpus + 助手),订单意图→题面(phrase_questions)。
"""
from __future__ import annotations
import json, re, threading, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # 允许 `python pipeline/render.py` 直跑(找到根目录 config)
import config
from pipeline.world_state import _date_of, week_label, _to_num, _dicts, EXPIRE, DELETE
from pipeline.lines import line_for
from pipeline.prompts import render
from pipeline.story import replay_story_ledger, review_narrative_supportedness


LEAK_BANNED = ["当前", "现在", "最新", "目前", "截至目前", "迄今", "至今", "一直", "历来",
               "维持", "保持不变", "累计", "现任", "如今", "始终", "仍为", "仍是", "依旧"]

# 单篇正文不需要 JSON 容器；保留 16384 预算以避免 reasoning 挤空正文。
FILLER_TEXT_MAX_TOKENS = 16_384

# filler 是无关草堆，没有生成凭据形态 token 的业务理由。这里只拦常见、足够长的
# 机器凭据前缀，不尝试做复杂“秘密检测”，避免把普通连字符文本误判。
_CREDENTIAL_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(?:sk-[A-Za-z0-9_-]{16,}|AKIA[A-Z0-9]{16}|"
    r"gh[pousr]_[A-Za-z0-9]{20,})(?![A-Za-z0-9_-])")

# 盲判别器"不确定/读不出"措辞(判别器明确表达"读不出"→ 该 atom 未忠实渲染)。
_DISC_UNSURE = ["不确定", "无法确定", "读不出", "读不到", "不清楚", "未提及", "未提到",
                "没有提到", "没有提及", "无法判断", "无从", "看不出", "歧义", "不明确",
                "信息不足", "无此", "查无", "找不到", "未找到", "无法读出"]


def _dim_norm(s) -> str:
    """★量纲严格归一(死钉④):去空格、全角数字归一并剥成对引号，保留 %/单位/小数点。
    与 world_state._norm / eval.judge._norm 的关键区别:那两个 .rstrip('%。.') 会把
    "78%"→"78" 抹平双量纲;本函数【保留 %】,使 _dim_norm("78%")!="0.78"、!="78"。"""
    if s is None:
        return ""
    t = str(s).strip().replace(" ", "").replace("　", "")
    t = t.translate(str.maketrans("０１２３４５６７８９％．", "0123456789%."))
    quote_pairs = {"\"": "\"", "'": "'", "“": "”", "‘": "’", "「": "」", "『": "』"}
    while len(t) >= 2 and quote_pairs.get(t[0]) == t[-1]:
        t = t[1:-1].strip()
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


def _discriminate_many(docs, queries: list[dict], tracer) -> dict[str, str]:
    """派一个盲读者一次回答同组文档的全部 ``entity/field`` 查询。

    查询不含真值；返回值只在调用方与冻结真值逐项严格对账。调用层已经耗尽
    网络/解析重试时立即失败，不能把服务错误伪装成“读不出”后重新渲染正文。
    """
    if not queries:
        return {}
    out = tracer.chat_json(
        "render.discriminate",
        [{"role": "system", "content": render("discriminate.system")},
         {"role": "user", "content": render(
             "discriminate.user",
             docs="\n\n".join(d for d in docs if d),
             queries=json.dumps(queries, ensure_ascii=False))}],
        temperature=0.0, max_tokens=2048, model=config.DISCRIMINATOR_MODEL)
    if not isinstance(out, dict) or "__error__" in out:
        detail = out.get("__error__", "非 JSON object") if isinstance(out, dict) else "非 JSON object"
        raise RuntimeError(f"render.discriminate 调用失败:{detail}")
    rows = out.get("answers")
    expected = [str(query.get("key")) for query in queries]
    if not isinstance(rows, list) or any(
            not isinstance(row, dict)
            or row.get("key") is None
            or row.get("answer") is None
            for row in (rows if isinstance(rows, list) else [])):
        raise RuntimeError("render.discriminate 协议失败:answers 必须是完整对象数组")
    keys = [str(row["key"]) for row in rows]
    if len(keys) != len(set(keys)) or set(keys) != set(expected):
        raise RuntimeError(
            f"render.discriminate 协议失败:期望 keys={expected},实际 keys={keys}")
    return {str(row["key"]): str(row["answer"]) for row in rows}


def _discriminator_recovers(docs, entity, field, true_value, tracer):
    """★渲染链第一步核心忠实检:盲读者据 docs 能否唯一、按对量纲还原 (entity,field) 的世界真值?
    返回 (recovered: bool, read_answer: str)。
    - recovered=True  ⟺ 判别器读出的值与 true_value【量纲严格相等】(_strict_eq)= 忠实渲染。
    - recovered=False ⟺ 读不出/读成"不确定"/对不上(含双量纲 78%↔0.78、多跳歧义)= 未忠实渲染 → 进 missing。
    ★死钉①③:true_value 只在【本函数代码侧】用于对账,绝不进判别器 messages(判别器只收 docs+实体+字段)。"""
    ans = _discriminate_many(
        docs, [{"key": "q0", "entity": entity, "field": field}], tracer).get("q0", "")
    return (_strict_eq(ans, str(true_value)), ans)


def _corpus_system(profile, blueprint=None, style_spec=None) -> str:
    """构造信号文档提示词，并把白皮书写作规格作为唯一风格约束传入。"""
    genres = "/".join(profile.get("doc_genres", ["周报", "通报", "邮件"]))
    stopped = profile.get("stopped_phrase", "停止统计")
    noun = profile.get("entity_noun", "实体")
    types = (blueprint or {}).get("entity_types") or []
    legend = "、".join(f"{t.get('id')}={t.get('noun')}" for t in types if t.get("id")) or f"legacy={noun}"
    time_unit = ((blueprint or {}).get("temporal_model") or {}).get("unit", "week")
    if isinstance(style_spec, dict):
        style_text = json.dumps(style_spec, ensure_ascii=False)
    elif style_spec:
        style_text = str(style_spec)
    else:
        style_text = "未另行指定；采用该领域真实文档的自然写法，篇幅以完整承载本组事实为准。"
    return render("corpus.system", noun=noun, genres=genres, stopped=stopped,
                  genre0=genres.split("/")[0], type_legend=legend, time_unit=time_unit,
                  style_spec=style_text)


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


def _event_is_narrated(event: dict, content: str) -> bool:
    """事件的参与者和全部 effect 三元组同篇出现，才允许挂该事件 provenance。"""
    participants = {str(x) for x in (event.get("participants") or {}).values() if x}
    effects = [effect for effect in (event.get("effects") or []) if isinstance(effect, dict)]

    def _effect_visible(effect: dict) -> bool:
        entity = str(effect.get("entity") or "").strip()
        field = str(effect.get("field") or "").strip()
        value = effect.get("set", effect.get("value"))
        return bool(entity and field and value is not None
                    and entity in content and field in content and str(value) in content)

    return bool(participants and effects
                and all(name in content for name in participants)
                and all(_effect_visible(effect) for effect in effects))


def _missing_event_narratives(events, contents) -> list[str]:
    """机械检查每个领域事件的参与者和 effect 三元组是否完整进入同一篇文档。"""
    missing = []
    for event in events:
        label = str(event.get("label") or "").strip()
        participants = sorted({str(x) for x in (event.get("participants") or {}).values() if x})
        effects = [effect for effect in (event.get("effects") or []) if isinstance(effect, dict)]
        if not label or not participants or not effects:
            missing.append(
                f"事件 {event.get('id') or event.get('type')} 缺 label/participants/effects，无法验叙事")
            continue
        if not any(_event_is_narrated(event, content) for content in contents):
            missing.append(
                f"事件「{label}」必须把参与者 {participants} 与全部 effect 三元组写在同一篇文档中")
    return missing


def _tracked_blocklist(ws, profile=None):
    """草堆禁词表 = 实体专名 + 所有【人名类字段】的取值。

    通用字段词（如“状态”“工具”）不是答案泄漏：没有对应实体专名时无法指向
    benchmark 事实。把它们列为禁词会与“保持同领域”形成不可满足约束。
    ★人名字段从白皮书 `field_schema.kind=="person"` 取(审计 ★1:删掉 '负责/汇报/经理' 中文子串启发式
    —— 那是 office 味、对非 office 域不可靠:medical 的「主治医师/会诊上级」一个 hint 都不匹配)。域知识只从白皮书来。"""
    out = set(ws.entities)
    person_fields = {f.get("name") for f in _dicts((profile or {}).get("field_schema", [])) if f.get("kind") == "person"}
    for flds in ws.entities.values():
        for fname, tl in flds.items():
            if fname in person_fields:
                for (_s, _d, v) in tl.set_values():
                    if v and _to_num(v) is None and len(str(v)) >= 2:
                        out.add(str(v))
    return out


def _accept_filler_text(out, blocked) -> list[dict]:
    """验收单篇 filler 正文；空响应或撞冻结专名时直接失败。"""
    if not isinstance(out, str) or not out.strip():
        detail = (out.get("__error__", f"类型={type(out).__name__}")
                  if isinstance(out, dict) else f"类型={type(out).__name__}")
        raise RuntimeError(f"render.filler 调用/协议失败:{detail}")
    content = out.strip()
    leaks = sorted({str(term) for term in blocked if term and str(term) in content})
    if leaks:
        raise RuntimeError(f"render.filler 命中冻结专名:{leaks}")
    if _CREDENTIAL_TOKEN_RE.search(content):
        raise RuntimeError("render.filler 命中凭据形态 token")
    return [{"type": "背景干扰文档", "content": content}]


def _canonical_fact_refs(content: str, facts: list[dict], events: list[dict]) -> list[str]:
    """从正文与冻结世界反推规范 ``entity.field`` / event-id 引用。"""
    refs: list[str] = []
    for fact in facts:
        entity = str(fact.get("entity") or "")
        field = str(fact.get("field") or "")
        value = fact.get("value")
        value_visible = value not in (None, "") and str(value) in content
        stopped_visible = (fact.get("stopped")
                           and any(word in content for word in ("停止", "不再", "终止", "暂停")))
        if entity and field and entity in content and (value_visible or stopped_visible):
            refs.append(f"{entity}.{field}")
    refs.extend(
        event.get("id") for event in events
        if event.get("id") and _event_is_narrated(event, content)
    )
    return list(dict.fromkeys(ref for ref in refs if ref))


def _sanitize_corpus(corpus: dict, ws, profile=None) -> dict:
    """收口语料元数据，尤其处理扩世界后的增量一致性。

    - 所有信号文档的 fact_refs 都从同 session 冻结事实与正文重算；模型自报引用
      不是真源。无法反推则删掉该文档。
    - 扩容后新实体名可能撞上旧 filler，此时删掉撞词 filler，不让草堆变证据。
    """
    blocked = {str(x) for x in _tracked_blocklist(ws, profile) if x}
    events_by_session = {}
    for event in getattr(ws, "events", None) or []:
        events_by_session.setdefault(event.get("session"), []).append(event)
    stats = {"canonicalized_refs": 0, "dropped_unref": 0,
             "dropped_filler_leaks": 0, "dropped_filler_credentials": 0}
    for session in corpus.get("sessions", []):
        sid = session.get("session_id")
        facts = _session_facts(ws, sid)
        kept = []
        for doc in session.get("docs", []):
            content = str(doc.get("content") or "")
            if doc.get("is_filler") is True:
                if _CREDENTIAL_TOKEN_RE.search(content):
                    stats["dropped_filler_credentials"] += 1
                    continue
                if any(term in content for term in blocked):
                    stats["dropped_filler_leaks"] += 1
                    continue
                doc["fact_refs"] = []
            elif "_sig_" in str(doc.get("doc_id", "")):
                doc["fact_refs"] = _canonical_fact_refs(
                    content, facts, events_by_session.get(sid, []))
                if not doc["fact_refs"]:
                    stats["dropped_unref"] += 1
                    continue
                stats["canonicalized_refs"] += 1
            kept.append(doc)
        session["docs"] = kept
    return stats


def _render_conflict_docs(ws, s, date, _tracer):
    """把冻结的 L5 小道值直接写成低可信文档，确保实体和值逐字可验。"""
    out = []
    for c in (getattr(ws, "conflicts", None) or []):
        if c.get("session") != s or not c.get("rumor_value"):
            continue
        entity = str(c["entity"])
        field = str(c["field"])
        rumor = str(c["rumor_value"])
        source = str(c.get("rumor_source") or "小道消息")
        authority = str(c.get("authoritative_source") or "官方记录")
        content = (
            f"【{source}·未经核实】{date}（第{week_label(s)}{ws.period_unit()}），"
            f"有人声称“{entity}”的“{field}”是“{rumor}”。"
            f"这条说法没有可核验的正式记录，也尚未得到{authority}确认；"
            "现仅按待核实传闻留存，不应当作正式结论。"
        )
        out.append({"type": "待核实传闻", "content": content})
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


def _story_signal_groups(bysku: dict, events: list[dict]) -> list[list[tuple[str, list[dict]]]]:
    """把事件 effect 事实与同一期普通状态拆组，避免整张角色表拖累主线场景。"""
    effect_keys = {
        (effect.get("entity"), effect.get("field"))
        for event in events for effect in (event.get("effects") or [])
        if isinstance(effect, dict)
    }
    story_group, background = [], []
    for entity, facts in bysku.items():
        event_facts = [fact for fact in facts
                       if (fact.get("entity"), fact.get("field")) in effect_keys]
        other_facts = [fact for fact in facts
                       if (fact.get("entity"), fact.get("field")) not in effect_keys]
        if event_facts:
            story_group.append((entity, event_facts))
        if other_facts:
            background.append((entity, other_facts))
    return ([story_group] if story_group else []) + list(_chunk(background, 2))


def _story_context_for_group(ledger, scenes, session, event_ids, event_by_id):
    """只传当前幕作用与上一幕 canonical events，不把自由摘要当事实喂回正文。"""
    if not ledger:
        return ""

    def _rank(scene):
        order = scene.get("order", 0)
        return scene.get("session", -1), order if isinstance(order, int) and not isinstance(order, bool) else 0

    event_ids = list(dict.fromkeys(x for x in event_ids if x))
    event_id_set = set(event_ids)
    current = [scene for scene in scenes
               if scene.get("session") == session
               and event_id_set.intersection(scene.get("event_refs") or [])]
    current.sort(key=lambda scene: (*_rank(scene), str(scene.get("scene_id") or "")))
    anchor = _rank(current[0]) if current else (session, -1)
    prior = [scene for scene in scenes
             if _rank(scene) < anchor]
    previous = max(prior, key=_rank, default=None)
    previous_events = []
    for event_ref in (previous.get("event_refs") or []) if previous else []:
        event = event_by_id.get(event_ref)
        if event:
            previous_events.append({key: event.get(key) for key in (
                "id", "label", "session", "participants", "effects")})
    payload = {
        "protagonist_ref": ledger.get("protagonist_ref"),
        "previous_scene": ({"scene_id": previous.get("scene_id"),
                            "canonical_events": previous_events}
                           if previous else None),
        "current_scenes": [
            {"scene_id": scene.get("scene_id"),
             "event_refs": [ref for ref in scene.get("event_refs", []) if ref in event_id_set],
             "dramatic_function": scene.get("dramatic_function")}
            for scene in current
        ],
    }
    context = ("\n【game Story Ledger（只用于组织本期行文）】"
               + json.dumps(payload, ensure_ascii=False)
               + "\n★previous_scene 只含已经发生的 canonical events；不得写出任何未来 scene。"
                 "事件参与者、结果、字段值仍只以本期 domain_events/facts 为准，"
                 "不得补造死亡、掉落、获得、阵营变化或其他动态真值。")
    return context


def _attach_story_provenance(docs, events, scenes) -> None:
    """按单篇实际承载的事件写 provenance，避免把整组 refs 复制给每篇。"""
    event_to_scene = {
        event_ref: scene.get("scene_id")
        for scene in scenes
        for event_ref in (scene.get("event_refs") or [])
    }
    for doc in docs:
        content = str(doc.get("content") or "")
        refs = [event.get("id") for event in events
                if event.get("id") and _event_is_narrated(event, content)]
        doc["event_refs"] = refs
        doc["scene_refs"] = list(dict.fromkeys(
            event_to_scene[ref] for ref in refs if ref in event_to_scene))


def render_corpus(wp, ws, target_tokens, tracer, corpus, done_weeks, save_cb, log=print,
                  only_entities=None, only_entity_sessions=None):
    """only_entities=None:全量渲(每周全实体+filler)。
    only_entities=set:★增量 delta(§10.1)——【只渲这些新实体的 signal】并【追加】到已有周 docs,
    ``only_entity_sessions`` 精确补渲被新关系/事件改变的旧实体周；不重灌 filler。"""
    story_ledger = getattr(ws, "narrative", None) or {}
    story_scenes = replay_story_ledger(ws, story_ledger) if story_ledger else []
    profile = wp.get("domain_profile", {})
    blueprint = getattr(ws, "world_blueprint", None) or wp.get("world_blueprint") or {}
    temporal = blueprint.get("temporal_model") or {}
    # blueprint 保存稳定机器枚举（chapter/week），正文、日志和 reviewer 必须共享
    # 同一个人类可读单位（章/周）；否则会生成“第5章”却授权“第5chapter”。
    time_unit = ws.period_unit()
    step_days = int(temporal.get("step_days", 7) or 7)
    sys_sig = _corpus_system(profile, blueprint, wp.get("style_spec"))
    sys_fil = _filler_system(profile, blueprint)
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
        labeled_events = [
            {**event, "label": (event_decls.get(event.get("type")) or {}).get(
                "label", event.get("type", ""))}
            for event in (getattr(ws, "events", None) or [])
        ]
        event_by_id = {event.get("id"): event for event in labeled_events if event.get("id")}
        session_events = [event for event in labeled_events if event.get("session") == s]
        if delta_mode:                                    # ★delta:新实体全程 + 旧实体受结构变化的精确 session
            facts = [f for f in facts if (f["entity"] in only_entities
                                          or (f["entity"], s) in only_entity_sessions)]
            if not facts:
                return s                                  # 新实体本周无事实 → 不加 doc
        bysku = {}
        for f in facts:
            bysku.setdefault(f["entity"], []).append(f)
        if story_ledger:
            sig_groups = _story_signal_groups(bysku, session_events)
        else:
            sig_groups = list(_chunk(list(bysku.items()), 2))   # 非剧情场景保持原两实体一组
        n_batches = filler_per_week

        def _render_sig(grp):                             # ★信号块:渲全 + 渲对 —— 盲判别器据渲文能否唯一还原 (实体,字段) 才算渲到
            from pipeline.grounding import STOP_MARKERS    # ★只借停用标记(STOP_MARKERS);忠实检不再用 §G 的 attributed(死钉②不同尺)
            gf = [f for _e, fs in grp for f in fs]
            group_entities = {f["entity"] for f in gf}
            if story_ledger:
                group_fact_keys = {(f.get("entity"), f.get("field")) for f in gf}
                group_events = [
                    event for event in session_events
                    if any((effect.get("entity"), effect.get("field")) in group_fact_keys
                           for effect in (event.get("effects") or []) if isinstance(effect, dict))
                ]
            else:
                group_events = [e for e in session_events
                                if group_entities.intersection((e.get("participants") or {}).values())]
            story_context = _story_context_for_group(
                story_ledger, story_scenes, s, [e.get("id") for e in group_events], event_by_id)
            # 待渲事实:非停用 → 派盲判别器读 (实体,字段) 的值,代码量纲严格对账;停用 → 验 (实体,停用标记) 同篇
            want_val = [(f["entity"], f["field"], str(f["value"])) for f in gf if f.get("value") and not f.get("stopped")]
            want_stop = [(f["entity"], f["field"]) for f in gf if f.get("stopped")]

            def _discriminate(contents):
                """★盲判别器忠实检(死钉①③):对每个 want_val atom 派一个盲读者只读 contents 答值,
                代码量纲严格对账;对每个 want_stop atom 验停用标记同篇。
                返回 (miss_val, miss_stop, miss_event)，供 missing/hint 管道复用。"""
                # 同一组文档只让盲读者读一次；true_value 仍只在代码对账侧，绝不进 prompt。
                queries = [
                    {"key": f"q{index}", "entity": entity, "field": field}
                    for index, (entity, field, _value) in enumerate(want_val)
                ]
                answers = _discriminate_many(contents, queries, tracer)
                miss_val = []
                for index, (entity, field, value) in enumerate(want_val):
                    answer = answers.get(f"q{index}", "")
                    if _strict_eq(answer, str(value)):
                        continue
                    read = "不确定" if _is_unsure(answer) else answer
                    miss_val.append(
                        f"{entity}的「{field}」(应承载值={value}):"
                        f"盲读者据文档读出的是『{read}』,与应承载的值不一致/读不出")
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

            grp_docs, hint, left = [], "", []
            for _att in range(4):                         # 多给几次重渲机会,强制渲全(世界辛苦生成,必须全用上)
                out = tracer.chat_json("render.signal",
                    [{"role": "system", "content": sys_sig},
                     {"role": "user", "content": render(
                         "corpus.user", s=week_label(s), time_unit=time_unit, date=date,
                         facts=json.dumps(gf, ensure_ascii=False),
                         events=json.dumps(group_events, ensure_ascii=False),
                         story_context=story_context, hint=hint)}],
                    temperature=0.6 if _att == 0 else 0.2, max_tokens=8192)
                cand, leak_notes = [], []
                for d in _dicts(out.get("docs") if isinstance(out, dict) else []):
                    if not d.get("content"):
                        continue
                    hits = _leaks(d["content"])
                    if hits:                              # 犯禁不再静默丢:记下死因,进诚实反馈(它驮的事实会出现在 missing 里)
                        leak_notes.append(f"《{(d.get('title') or d.get('type') or '无题')}》因使用全局口径词{hits}被废弃")
                    else:
                        # signal/filler 身份由代码决定，模型不能用额外元数据让已验收
                        # 的事件文档在 sanitize 阶段被当草堆删除。
                        clean = {key: d[key] for key in ("title", "type", "content", "fact_refs")
                                 if key in d}
                        clean["is_filler"] = False
                        cand.append(clean)
                contents = [d.get("content", "") for d in cand]
                miss_val, miss_stop, miss_event = _discriminate(contents)
                missing = miss_val + miss_stop + miss_event
                unsupported = []
                if not missing and story_ledger:
                    unsupported = review_narrative_supportedness(
                        tracer,
                        canon={"session": s, "facts": gf, "domain_events": group_events,
                               "period_label": f"第{week_label(s)}{time_unit}",
                               "document_date": date, "time_unit": time_unit,
                               "allowed_document_scaffolding": ["日志记录", "档案登记", "通报提及"],
                               "allowed_past_context": story_context},
                        candidate=cand,
                        scope=f"game corpus session {s}")
                left = missing + [f"无依据剧情断言:{item}" for item in unsupported]
                grp_docs = cand
                if not left:
                    break
                # ★hint 如实(老版把"写了但犯禁被废"误报成"没写"→ 重试不收敛):缺什么、为什么缺,分开说
                hint = ""
                if missing:
                    hint += (f"\n★ 这些事实在上一版【没有合格呈现】"
                             f"(实体名与值必须就近、值逐字照抄):{missing}。")
                if miss_event:
                    exact_effects = [
                        {"entity": effect.get("entity"), "field": effect.get("field"),
                         "set": effect.get("set", effect.get("value"))}
                        for event in group_events for effect in (event.get("effects") or [])
                        if isinstance(effect, dict)
                    ]
                    hint += ("\n★ 只修上述事件证据：同一篇中明确写动作，并逐字写出这些"
                             f" entity/field/set，禁止同义替换：{json.dumps(exact_effects, ensure_ascii=False)}。")
                if unsupported:
                    hint += (f"\n★ 上一版含 canonical 之外的断言:{unsupported}。"
                             "删除这些断言，只用给定 facts/events 与已发生上下文重写。")
                if leak_notes:
                    hint += (f"\n★ 另:上一版 {leak_notes}——重写时把其中事实写进正文,但【删掉这些全局口径词】"
                             f"(注意:字段名/实体名/事实值本身含这些字的照常写,不算犯禁)。")
                hint += "逐条重写进正文(仍只写本期)。"
            else:
                fallback_count.append(len(left))          # ★机械验收落点(语义改为"弃段计数"):汇总进末尾日志
                ents = sorted({m.split("的「")[0] for m in left})
                log(f"  ⚠fail-loud弃段[{time_unit}{week_label(s)}]:{len(left)} 个 atom 多轮重渲后盲读者仍不可还原,弃段不入库({ents})")
                if story_ledger and group_events:
                    raise RuntimeError(
                        f"game narrative 渲染失败:{time_unit}{week_label(s)} 仍有 {len(left)} 个未通过项")
                return []
            if story_ledger:
                _attach_story_provenance(grp_docs, group_events, story_scenes)
            return grp_docs

        filler_failures: list[str] = []

        def _render_fil(_ci):                             # 一次只生成一篇纯正文
            out = tracer.chat_text("render.filler",
                [{"role": "system", "content": sys_fil},
                 {"role": "user", "content": render("filler.user", s=week_label(s), time_unit=time_unit,
                                                      date=date)}],
                temperature=0.9, max_tokens=FILLER_TEXT_MAX_TOKENS)
            try:
                return _accept_filler_text(out, blocked)
            except RuntimeError as error:
                # filler 只提供草堆密度，不承载任何 gold。单篇协议失败显式记账并跳过；
                # 最终仍以总语料字符下限 fail-closed，不重试也不伪造替代正文。
                filler_failures.append(str(error))
                return []

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
                if filler_failures:
                    log(f"  ⚠{time_unit}{week_label(s)} filler 缺失 {len(filler_failures)}/{n_batches}:"
                        f"{filler_failures[:2]}")
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
            log(f"  [{time_unit} {week_label(s)} ✓{tag} {len(done_weeks)}/{n_sessions}] 累计 {sum(len(x['docs']) for x in corpus['sessions'])} 篇 / {ch/1e6:.2f}M 字")
        return s

    config.pmap(_render_week, weeks, workers=max(1, len(weeks)))   # ★周并行;在飞 API 由全局 LLM_CONCURRENCY 兜住
    sanitized = _sanitize_corpus(corpus, ws, profile)
    if story_ledger:
        covered = {
            event_ref
            for session in corpus.get("sessions", [])
            for doc in session.get("docs", [])
            for event_ref in (doc.get("event_refs") or [])
        }
        expected = {str(event.get("id")) for event in (getattr(ws, "events", None) or [])
                    if isinstance(event, dict) and event.get("id")}
        missing_events = sorted(expected - covered)
        if missing_events:
            # 清掉缺证据事件所在周的 checkpoint，人工续跑时会真正重渲，而非
            # 反复读取同一份坏断点。
            missing_sessions = {
                event.get("session") for event in (getattr(ws, "events", None) or [])
                if isinstance(event, dict) and str(event.get("id")) in missing_events
            }
            done_weeks.difference_update(missing_sessions)
            corpus["sessions"] = [session for session in corpus.get("sessions", [])
                                  if session.get("session_id") not in missing_sessions]
            save_cb()
            raise RuntimeError(f"game narrative 缺 canonical event 正文证据:{missing_events}")
    if any(sanitized.values()):
        save_cb()
        log(f"  ✓ 语料收口:规范化 fact_refs {sanitized['canonicalized_refs']} 篇 / "
            f"弃无引用信号 {sanitized['dropped_unref']} 篇 / "
            f"清理扩容后撞词 filler {sanitized['dropped_filler_leaks']} 篇 / "
            f"清理凭据形态 filler {sanitized['dropped_filler_credentials']} 篇")
    ch = sum(len(dd.get("content", "")) for x in corpus["sessions"] for dd in x["docs"])
    if ch < target_tokens:
        save_cb()
        raise RuntimeError(
            f"语料字符不足:{ch}/{target_tokens}；filler 可单篇缺失，但总规模合同不允许欠账")
    fb = f";⚠fail-loud弃段 {len(fallback_count)} 处/{sum(fallback_count)} 个 atom 未忠实渲染(验收要求趋零)" if fallback_count else ";弃段 0(✓)"
    log(f"  ✓ 渲染完成:{sum(len(x['docs']) for x in corpus['sessions'])} 篇 / {ch/1e6:.2f}M 字(目标 {target_tokens/1e6:.1f}M){fb}")


PHRASE_SYS = render("phrase.system")


def phrase_questions(orders, wp, tracer, log=print) -> list[dict]:
    def _ph(o):                                           # 每条订单独立 → 并发出题
        line = line_for(o.get("line", ""))                # 出题意图/须隐藏 = 各产线自己的 intent()
        if line is None:                                  # 兜底(订单都来自已建线,理论不触发)
            return {**o, "question": "", "_phrase_fallback": False}
        intent, hide = line.intent(o)
        # ★确定性出题 bypass(L9 闭选项 MC):选项串必须逐字保真、LLM 润色会打乱选项/丢 gold → 破坏纯代码 EM。
        #   直接用 intent 原文作题面(它已是完整可答的 MC 题,含 held-out x* + 全部选项)。
        if getattr(line, "deterministic_phrasing", False):
            return {**o, "question": intent, "_phrase_fallback": False}
        out = tracer.chat_json("phrase",
            [{"role": "system", "content": PHRASE_SYS},
             {"role": "user", "content": render("phrase.user", intent=intent, hide=hide)}],
            temperature=0.5, max_tokens=2048)
        q = out.get("question", "") if isinstance(out, dict) else ""
        q = q if isinstance(q, str) else ""
        fell_back = not q.strip()
        if fell_back:
            # intent 是产线代码生成、已被良定义闸验证过的完整可答题面；润色模型只负责
            # 表达，不拥有订单生杀权。协议失败时直接保留真源，不丢掉已验证的供给。
            q = intent
        # ★主语保真兜底(Q49 悬空代词根治):phrase 偶尔把主语专名改成"他/该案"丢了指代。实体名核(前4字)
        #   若整个没在题面出现 → 退回 intent 原文(它必含实体名、是完整可答问题)。比"禁代词"软规则多一道硬保证。
        ent = (o.get("entity") or "").strip()
        if q and ent and ent[:4] not in q:
            q = intent
            fell_back = True
        return {**o, "question": q, "_phrase_fallback": fell_back}
    raw = config.pmap(_ph, orders, workers=8)
    fallback_count = sum(bool(q.pop("_phrase_fallback", False)) for q in raw)
    qs = [q for q in raw if q.get("question", "").strip()]    # 丢并发下偶发的空题面
    dropped = len(raw) - len(qs)
    by_line = {}
    for q in qs:
        by_line[q.get("line", "?")] = by_line.get(q.get("line", "?"), 0) + 1
    log(f"  ④ 出题:{len(qs)} 题完成(原意图保真 {fallback_count};丢空 {dropped};桥实体/答案不进题面);by_line {by_line}")
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
                        return {"answers": [{"key": "q0", "answer": m.group(1)}]}
                # 负责人歧义:文档里出现两个不同负责人 → 盲读者答"不确定"
                if "负责人" in docs:
                    names = set(_re.findall(r"负责人[为是:]?\s*([一-龥]{2,3})", docs))
                    if len(names) == 1:
                        return {"answers": [{"key": "q0", "answer": names.pop()}]}
                    return {"answers": [{"key": "q0", "answer": "不确定"}]}    # 多负责人歧义 / 读不出
                # K=V 句 "X本期「Y」为Z":抠 Z
                m = _re.search(r"为([0-9.]+%?)", docs)
                if m:
                    return {"answers": [{"key": "q0", "answer": m.group(1)}]}
                return {"answers": [{"key": "q0", "answer": "不确定"}]}
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
    sample_user = render("discriminate.user", docs="文档正文",
                         queries='[{"key":"q0","entity":"某实体","field":"某字段"}]')
    ck("死钉③:user 模板只含 docs/entity/field", "某实体" in sample_user and "某字段" in sample_user
       and "真值" not in sample_user and "gt" not in sample_user.lower())

    class _BulkTracer:
        def __init__(self):
            self.calls = 0
            self.models = []

        def chat_json(self, _step, _messages, **_kw):
            self.calls += 1
            self.models.append(_kw.get("model"))
            return {"answers": [{"key": "q0", "answer": "甲"},
                                  {"key": "q1", "answer": "乙"}]}

    bulk_tracer = _BulkTracer()
    bulk_answers = _discriminate_many(
        ["一篇同时承载多个字段的文档"],
        [{"key": "q0", "entity": "实体A", "field": "字段A"},
         {"key": "q1", "entity": "实体B", "field": "字段B"}],
        bulk_tracer)
    ck("同组多字段只调用一次盲读者", bulk_tracer.calls == 1
       and bulk_answers == {"q0": "甲", "q1": "乙"}
       and bulk_tracer.models == [config.DISCRIMINATOR_MODEL])

    class _DuplicateKeyTracer:
        def chat_json(self, _step, _messages, **_kw):
            return {"answers": [{"key": "q0", "answer": "甲"},
                                  {"key": "q0", "answer": "乙"}]}

    try:
        _discriminate_many(
            ["正文"],
            [{"key": "q0", "entity": "实体A", "field": "字段A"},
             {"key": "q1", "entity": "实体B", "field": "字段B"}],
            _DuplicateKeyTracer())
        duplicate_rejected = False
    except RuntimeError:
        duplicate_rejected = True
    ck("批量盲读严格拒绝重复或缺失 key", duplicate_rejected)

    class _NoConflictLLM:
        def chat_json(self, *_args, **_kwargs):
            raise AssertionError("L5 传闻不应调用模型")

    class _ConflictWorld:
        conflicts = [{
            "entity": "旧地址解析工件核对",
            "field": "调用状态",
            "session": 2,
            "rumor_value": "执行中",
            "rumor_source": "内部群聊转述",
            "authoritative_source": "官方通报",
        }]

        @staticmethod
        def period_unit():
            return "轮"

    conflict_docs = _render_conflict_docs(_ConflictWorld(), 2, "2025-01-08", _NoConflictLLM())
    ck("L5 传闻由冻结合同确定性渲染且不调模型", len(conflict_docs) == 1)
    ck("L5 传闻逐字承载实体和值并标明低可信",
       all(token in conflict_docs[0]["content"] for token in
           ("旧地址解析工件核对", "调用状态", "执行中", "未经核实", "官方通报")))

    ck("filler 单篇纯正文通过且由代码固定类型",
       _accept_filler_text("外围执行体完成无关归档任务。", {"冻结实体"})
       == [{"type": "背景干扰文档", "content": "外围执行体完成无关归档任务。"}])
    for name, bad, blocked in (
        ("filler 拒绝 JSON 对象壳", {"content": "外围记录"}, set()),
        ("filler 拒绝空正文", "  ", set()),
        ("filler 拒绝冻结专名泄漏", "冻结实体的状态", {"冻结实体"}),
        ("filler 拒绝凭据形态 token", "外围日志记录 sk-exampleToken123456后轮转", set()),
    ):
        try:
            _accept_filler_text(bad, blocked)
            rejected = False
        except RuntimeError:
            rejected = True
        ck(name, rejected)

    npass = sum(1 for ok, _ in checks if ok)
    for ok, name in checks:
        print(f"  {'✓' if ok else '✗'} {name}")
    print(f"[render self-test] {npass}/{len(checks)} PASS")
    return npass == len(checks)


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(0 if _self_test() else 1)
