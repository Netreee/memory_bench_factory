"""
pipeline.stages_v8 — V8 各阶段实现(LLM 当主角,裁判在校验层)。

照 docs/anchors/redesign_v8.md。数据用 dict 贯穿(轻量)。
核心:Stage E answer-first 出题(LLM 主角自由出题,带 evidence_spans);
      Stage F 三道闸校验(grounding 代码 + 可执行验证代码 + judge LLM),
      其中可执行验证同时是"结构当裁判"的可证伪实验(冲突日志)。
"""
from __future__ import annotations
from difflib import SequenceMatcher
from pathlib import Path
import json
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config


# ─────────────────────────────────────────────────────────────────────────────
# 工具
# ─────────────────────────────────────────────────────────────────────────────

def _to_num(v):
    if v is None:
        return None
    m = re.search(r"-?\d+\.?\d*", str(v))
    return float(m.group()) if m else None


def _norm(s):
    return re.sub(r"[\s，,。.、;:；：%％]", "", str(s or "").strip().lower())


def _answers_match(a, b):
    na, nb = _norm(a), _norm(b)
    return bool(na) and bool(nb) and (na == nb or na in nb or nb in na)


def _fuzzy_in(span, text, thr=0.85):
    """evidence span 是否(模糊)出现在 text 里。子串优先,否则滑窗 SequenceMatcher。"""
    span = (span or "").strip()
    if not span:
        return False, 0.0
    if span in text:
        return True, 1.0
    L = len(span)
    best = 0.0
    step = max(1, L // 4)
    for i in range(0, max(1, len(text) - L + 1), step):
        window = text[i:i + L + 8]
        r = SequenceMatcher(None, span, window).ratio()
        if r > best:
            best = r
        if best >= thr:
            return True, best
    return best >= thr, best


# ─────────────────────────────────────────────────────────────────────────────
# Stage 0 — Refine(模糊场景 → 结构化 spec)  [LLM]
# ─────────────────────────────────────────────────────────────────────────────

REFINE_SYSTEM = """你是场景分析师。给定一个【模糊的场景描述】+【few-shot 文档样本】,\
产出一个结构化 spec,供后续自动生成 memory 评测 benchmark 用。

【要做】
1. description_refined:一句清晰、具体的场景描述
2. key_fields:本场景值得【长期追踪】的关键字段;每个标 type(stable=整段不变 / evolving=跨时间演化)
   + value_type(人名/数值/百分比/状态/专名/日期…)
3. subject:记忆主体(如 project / customer / individual / contract_matter …)
4. perspective:评测视角(如 cross_line_leader / line_manager / first_person / audit_compliance …)

【原则】只依据输入,不编造领域外设定;key_fields 要能支撑"随时间演化、需要记忆"的题。

【输出严格 JSON】(不要 markdown 包裹)
{"description_refined":"...","key_fields":[{"name":"...","type":"stable|evolving","value_type":"..."}],
 "subject":"...","perspective":"..."}"""


def stage_0_refine(description, corpus_samples):
    samples = "\n".join(
        f"[doc{i}] type={(d.get('metadata') or {}).get('doc_type','?')} "
        f"date={(d.get('metadata') or {}).get('date','?')}\n  {(d.get('content') or '')[:220]}"
        for i, d in enumerate(corpus_samples)
    )
    user = (f"【模糊场景描述】\n{description}\n\n"
            f"【few-shot 文档样本】({len(corpus_samples)} 篇,无 QA 示例)\n{samples}\n\n"
            f"产出结构化 spec,严格 JSON。")
    return config.chat_json(
        [{"role": "system", "content": REFINE_SYSTEM}, {"role": "user", "content": user}],
        temperature=0.4, max_tokens=2048)


# ─────────────────────────────────────────────────────────────────────────────
# Stage B — CapabilityPlan(LLM 规划能力配额,取代硬编码 profile)  [LLM]
# ─────────────────────────────────────────────────────────────────────────────

PLAN_SYSTEM = """你是 memory benchmark 规划师。给定场景维度,规划本 benchmark 覆盖哪些【记忆能力】、各几题。

【能力清单】
红海五件套(必覆盖):
  IE 信息抽取(单点回填) / MR 多 session 综合(跨期聚合) / TR 时序推理(变化时机/排序) /
  KU 知识更新(识别最新值,而非旧值) / ABS 拒答(问场景里不存在的字段)
蓝海(按场景酌情加):CONFLICT 冲突消解 / FORGET 遗忘 / ATTRIB 失败归因

【原则】按本场景特点分配(如周报场景 KU/TR/MR 偏重);每项给 rationale;sum(n)=target_size。

【输出严格 JSON】{"items":[{"capability":"KU","n":3,"rationale":"..."}],"total":N}"""


def stage_b_capability_plan(spec, dims, target_size):
    user = (f"【场景】{spec.get('description_refined')}\n"
            f"【维度】I={dims.I_ingest_channels}, S={dims.S_memory_subject}, "
            f"V={dims.V_perspective}, T={dims.T_temporal_pattern}\n"
            f"【key_fields】{json.dumps(spec.get('key_fields', []), ensure_ascii=False)}\n"
            f"【target_size】{target_size}\n\n规划能力配额,严格 JSON。")
    return config.chat_json(
        [{"role": "system", "content": PLAN_SYSTEM}, {"role": "user", "content": user}],
        temperature=0.5, max_tokens=2048)


# ─────────────────────────────────────────────────────────────────────────────
# Stage C — FactGraph(时序事实演化图,gt 裁判参照系)  [LLM]
# ─────────────────────────────────────────────────────────────────────────────

FACTGRAPH_SYSTEM = """你是 memory benchmark 的 ground-truth 世界设计师。为本场景设计一个\
【时序事实演化图】,作为 benchmark 的标准答案参照系(供机器机械核对 gt)。

【要定义】
- entities:实体
- fields:字段(stable/evolving),与 key_fields 对应
- sessions:N 个时间步,每个带 date + state(该 session 所有字段的当前值)
- evolutions:演化事件(某 evolving 字段在某 session 从 from 变 to)
- absent_fields:本场景里【根本不存在】的字段(给 ABS 拒答题用)

【硬约束】
1. 值要自然、合理、是该场景正常值(★ 种子扰动:不要照搬 few-shot 样本里的具体人名/数值,换一套新的)
2. evolving 字段要有真实演化轨迹(旧值持续几个 session、再更新为新值)
3. 数值字段给真实数字轨迹;人名/状态字段给合理演化

【输出严格 JSON】(不要 markdown 包裹)
{"main_theme":"...",
 "entities":[{"id":"...","name":"...","type":"..."}],
 "fields":[{"name":"...","type":"stable|evolving","value_type":"..."}],
 "sessions":[{"session_id":0,"date":"YYYY-MM-DD","state":{"字段名":"值"}}],
 "evolutions":[{"field":"...","from":"...","to":"...","at_session":int,"type":"update|retract"}],
 "absent_fields":["..."]}"""


def stage_c_fact_graph(spec, dims, plan, n_sessions=5):
    user = (f"【场景】{spec.get('description_refined')}\n"
            f"【key_fields】{json.dumps(spec.get('key_fields', []), ensure_ascii=False)}\n"
            f"【能力计划】{json.dumps(plan.get('items', []), ensure_ascii=False)}\n"
            f"【session 数】{n_sessions}(session_id 0..{n_sessions-1})\n\n"
            f"设计时序事实演化图,严格 JSON。")
    return config.chat_json(
        [{"role": "system", "content": FACTGRAPH_SYSTEM}, {"role": "user", "content": user}],
        temperature=0.6, max_tokens=4096)


# ─────────────────────────────────────────────────────────────────────────────
# Stage D — Corpus(从 FactGraph 长出多 session 多文档语料)  [LLM]
# ─────────────────────────────────────────────────────────────────────────────

CORPUS_SYSTEM = """你是语料合成专家。基于事实图某 session 的 state,合成 2-3 篇【异质文档】\
(如 周报=数值指标 / 通报=人事状态 / 邮件=沟通),把该 session 的字段值自然叙述进去、\
信息【分散】到多篇(不要一篇全包)。

【硬约束】
1. 每篇标 fact_refs:本篇叙述了哪些字段(字段名列表)
2. 正文必须含该 session 的【日期锚点】(如"2025-04-17 周报"/"截至 4 月 17 日")
3. evolving 字段只写【当前 session 的值】,严禁回顾历史值或展望未来值
4. 风格自然,像该场景真实文档

【输出严格 JSON】{"docs":[{"doc_id":"...","doc_type":"...","content":"...","fact_refs":["字段名"]}]}"""


def stage_d_corpus(graph, verbose=True):
    theme = graph.get("main_theme", "")
    sessions_out = []
    for sess in graph.get("sessions", []):
        sid, date = sess.get("session_id"), sess.get("date")
        user = (f"【主题】{theme}\n"
                f"【session {sid} 日期 {date} 的 state(必须 100% 体现)】\n"
                f"{json.dumps(sess.get('state', {}), ensure_ascii=False, indent=1)}\n\n"
                f"合成 2-3 篇异质文档,信息分散,每篇标 fact_refs + 含日期锚点。严格 JSON。")
        try:
            data = config.chat_json(
                [{"role": "system", "content": CORPUS_SYSTEM}, {"role": "user", "content": user}],
                temperature=0.7, max_tokens=3072)
            docs = data.get("docs", [])
        except Exception as e:
            print(f"[v8-corpus] session {sid} 异常: {e}")
            docs = []
        # 补 doc_id 前缀防撞
        for j, d in enumerate(docs):
            d["doc_id"] = f"s{sid}_{d.get('doc_id', j)}"
        sessions_out.append({"session_id": sid, "date": date, "docs": docs})
        if verbose:
            print(f"[v8-corpus] session {sid}: {len(docs)} 篇文档")
    return {"sessions": sessions_out}


# ─────────────────────────────────────────────────────────────────────────────
# Stage E — Answer-first 出题(LLM 主角)  [LLM]  ★ V8 核心
# ─────────────────────────────────────────────────────────────────────────────

ANSWERFIRST_SYSTEM = """你是记忆评测出题专家。探索给定的【多 session 语料】,针对指定【能力】出题。

【★ answer-first 流程(必须遵守)】
1. 先在语料里【定位证据】:找出支撑答案的原文片段(evidence_spans:doc_id + 原文片段)
2. 据证据【确定答案】answer
3. 再据答案【写自然口语问题】question

【硬约束】
1. 问题必须【依赖记忆】:跨 session / 追踪字段演化 / 多文档聚合 —— 单看一篇文档答不出来
2. answer 必须被 evidence_spans 【直接支持】,不能靠常识猜
3. evidence_spans 必须是语料里的【原文片段】(逐字截取,供机器核对),标明来自哪个 doc_id
4. field 标注本题问的是哪个字段(便于机器核对 gt)
5. ABS 拒答题:问语料里【不存在】的字段,answer 填 "INSUFFICIENT_EVIDENCE",evidence_spans 留空

【能力语义】IE=某 session 某字段值 / MR=跨 session 聚合(最高最低) / TR=变化时机 /
KU=最新值(注意旧值在多 session 高频,要答最新那个) / ABS=拒答

【输出严格 JSON】{"questions":[{"question":"...","answer":"...","field":"...","capability":"...",
 "hops":int,"evidence_spans":[{"doc_id":"...","span":"逐字原文"}],"reasoning":"..."}]}"""


def stage_e_answer_first(corpus, graph, plan, verbose=True):
    corpus_text = ""
    for sess in corpus.get("sessions", []):
        for doc in sess.get("docs", []):
            corpus_text += (f"[doc_id={doc['doc_id']}] (session{sess['session_id']} "
                            f"{sess['date']} {doc.get('doc_type','')})\n{doc.get('content','')}\n\n")
    evo = json.dumps(graph.get("evolutions", []), ensure_ascii=False)
    absent = json.dumps(graph.get("absent_fields", []), ensure_ascii=False)
    all_q = []
    for item in plan.get("items", []):
        cap, n = item.get("capability"), item.get("n", 0)
        if n <= 0:
            continue
        user = (f"【多 session 语料】\n{corpus_text}\n"
                f"【演化线索(供出 KU/TR 题)】{evo}\n"
                f"【不存在的字段(供出 ABS 题)】{absent}\n\n"
                f"【本次出题能力】{cap},出 {n} 道。严格按 answer-first,严格 JSON。")
        try:
            data = config.chat_json(
                [{"role": "system", "content": ANSWERFIRST_SYSTEM}, {"role": "user", "content": user}],
                temperature=0.6, max_tokens=4096)
            qs = data.get("questions", [])
        except Exception as e:
            print(f"[v8-answerfirst] cap={cap} 异常: {e}")
            qs = []
        for q in qs:
            q["capability"] = cap
            all_q.append(q)
        if verbose:
            print(f"[v8-answerfirst] {cap}: 出 {len(qs)} 题")
    return all_q


# ─────────────────────────────────────────────────────────────────────────────
# Stage F — 三道校验闸(裁判)  [代码 grounding + 代码可执行 + LLM judge]
# ─────────────────────────────────────────────────────────────────────────────

def _gt_from_graph(graph, capability, field):
    """从 FactGraph 代码确定性算 gt(KU/TR/MR)。返回 (gt, applicable)。"""
    if not field:
        return None, False
    sessions = sorted(graph.get("sessions", []), key=lambda s: s.get("session_id", 0))
    series = [(s["session_id"], s.get("date"), s["state"].get(field))
              for s in sessions if field in s.get("state", {})]
    if not series:
        return None, False
    if capability == "KU":
        return series[-1][2], True
    if capability == "TR":
        for i in range(1, len(series)):
            if str(series[i][2]) != str(series[i - 1][2]):
                return f"session{series[i][0]}({series[i][1]})", True
        return None, False
    if capability == "MR":
        nums = [(sid, _to_num(v)) for sid, _, v in series if _to_num(v) is not None]
        if len(nums) >= 2:
            mx = max(nums, key=lambda x: x[1])
            return f"session{mx[0]}", True  # 默认最高;最低题靠 judge 兜
    return None, False


JUDGE_SYSTEM = """你是 memory 出题质检官,同时负责【对照真实值轨迹核对答案】。
给定 question / evidence / answer + 该字段在各 session 的【真实值轨迹】(来自 ground-truth 演化图)。

【判定三项】
1. answer_correct:对照真实值轨迹,answer 是否正确。
   - 'KU 最新值'看轨迹最后一个 session;'TR 变化时机'看值发生变化的 session;
     'MR 最高/最低'看轨迹极值;'IE 某期值'看对应 session。
   - ★ 格式不同但语义相同算【对】(如 '2.3%' = 'session1 的 2.3%' = '2025-01-13 那周')。
2. needs_memory:本题是否需要跨 session / 追踪演化 / 多文档信息。
   - ★ KU/TR/MR 本质都需要记忆;不要因为"证据有点歧义/没写死最早"就否定——
     只要答案能从语料+轨迹确定,就算需要记忆。单看一篇文档就能答的,才判 false。
3. well_formed:题自包含、无歧义。★ 场景内的专有名词(部门名/项目名等)【不算】外部知识。

【ABS 特例】若题问的是轨迹/语料里【不存在】的字段、answer 是拒答(INSUFFICIENT_EVIDENCE 等),
则 answer_correct=true、needs_memory=true(它考的是"不编造")。

verdict=pass 当且仅当 answer_correct 且 needs_memory 且 well_formed。
【输出严格 JSON】{"answer_correct":bool,"needs_memory":bool,"well_formed":bool,
 "verdict":"pass|reject","reason":"一句话"}"""


def _field_trajectory(graph, field):
    """该字段在各 session 的真实值轨迹(给 judge 当对照真相,而非代码硬判)。"""
    out = []
    for s in sorted(graph.get("sessions", []), key=lambda x: x.get("session_id", 0)):
        if field and field in s.get("state", {}):
            out.append({"session": s.get("session_id"), "date": s.get("date"),
                        "value": s["state"][field]})
    return out


def _judge(q, graph):
    traj = _field_trajectory(graph, q.get("field"))
    user = (f"【问题】{q.get('question')}\n【能力】{q.get('capability')}\n"
            f"【evidence】{json.dumps(q.get('evidence_spans', []), ensure_ascii=False)}\n"
            f"【answer】{q.get('answer')}\n"
            f"【该字段真实值轨迹(对照真相)】{json.dumps(traj, ensure_ascii=False)}\n"
            f"【场景里不存在的字段(ABS 判定用)】{graph.get('absent_fields', [])}\n\n"
            f"对照轨迹判三项,严格 JSON。")
    try:
        return config.chat_json(
            [{"role": "system", "content": JUDGE_SYSTEM}, {"role": "user", "content": user}],
            temperature=0.0, max_tokens=1024)
    except Exception as e:
        return {"verdict": "reject", "reason": f"judge异常{type(e).__name__}"}


def stage_f_validate(raw_questions, corpus, graph, use_judge=True, verbose=True):
    """三道闸。返回 (passed, reject_log, conflict_log)。"""
    doc_text = {}
    for sess in corpus.get("sessions", []):
        for doc in sess.get("docs", []):
            doc_text[doc["doc_id"]] = doc.get("content", "")
    passed, rejects, conflicts = [], [], []
    for q in raw_questions:
        cap = q.get("capability")
        # 闸 (a) grounding(ABS 题无 evidence,跳过 grounding)
        if cap != "ABS":
            spans = q.get("evidence_spans", [])
            if not spans:
                rejects.append({"q": q.get("question"), "gate": "grounding", "reason": "无 evidence"})
                continue
            ok_all = True
            for ev in spans:
                hit, score = _fuzzy_in(ev.get("span", ""), doc_text.get(ev.get("doc_id"), ""))
                if not hit:
                    ok_all = False
                    rejects.append({"q": q.get("question"), "gate": "grounding",
                                    "reason": f"evidence 不在语料(doc={ev.get('doc_id')}, score={score:.2f})"})
                    break
            if not ok_all:
                continue
        # 闸 (b) 已弃用"代码结构当裁判"(全面拥抱 LLM);fact_graph 改作 judge 的真值参考(见 _judge)
        # 闸 (c) judge(LLM 读真实值轨迹综合判定;校准后:别误杀 TR/ABS、别误放单文档、专名不算外部知识)
        q["gt_source"] = "llm+judge"
        if use_judge:
            v = _judge(q, graph)
            q["judge"] = v
            if v.get("verdict") != "pass":
                rejects.append({"q": q.get("question"), "gate": "judge", "reason": v.get("reason")})
                continue
        passed.append(q)
    if verbose:
        print(f"[v8-validate] 通过 {len(passed)}/{len(raw_questions)}, reject {len(rejects)} "
              f"(裁判=grounding+judge;代码结构裁判已弃用)")
    return passed, rejects, conflicts
