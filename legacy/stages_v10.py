"""
pipeline.stages_v10 — V10 需要 LLM 的阶段(redesign_v10.md)。

gt 地基已在纯代码层:world_state(状态机 + 7 能力 gt) + order_gen(点菜)。
本文件放「LLM 当素材生成者」的阶段:
  Stage C:LLM 填【多实体世界表】→ 代码 assemble_world 成 WorldState(gt 仍归代码)
Stage 0/A/B 复用 stages_v8 / stage_a_dimensions。Stage D/E/F 见 T4/T5。
"""
from __future__ import annotations
from collections import Counter
import json
import math
import random as _random
import statistics

from .stages_v8 import stage_0_refine, stage_b_capability_plan, _answers_match, _norm  # noqa: F401
from .stages_v9 import _teacher_answer, TEACHER_SYSTEM
from .world_state import (
    assemble_world, WorldState, _date_of, _to_num, INVALID, INSUFFICIENT, EXPIRE, DELETE,
)
import config


# ─────────────────────────────────────────────────────────────────────────────
# Stage C V10 — LLM 填多实体世界表 → 代码 assemble 成 WorldState
# ─────────────────────────────────────────────────────────────────────────────

WORLD_SYSTEM = """你是 memory benchmark 的 ground-truth 世界设计师。为给定场景设计一张\
【多实体、随时间演化的真值世界表】。代码会据此【机械算标准答案】,所以你只负责【填表】——\
★ 严禁写问题、严禁写答案、严禁写"最新/当前/截至…仍为"这类结论性措辞。

【要设计】
1. entities:多个实体(如多个部门/项目/客户/合同),彼此可有依赖
2. 每个 entity 若干 field,每个 field 标 type:
   - "stable":整段不变 → 给单一 "value"
   - "evolving":随 session 演化 → 给 "trajectory"(列出关键 session 的取值)
     · ★ evolving 字段必须真的变:trajectory 至少 2 个【不同】的值
     · ★【数值字段】轨迹必须【非单调】:做出峰/谷/平台/反弹(如 2.5%→1.8%→2.8%→2.1%),
       最大或最小值要落在【非首非尾】的某周,严禁一路单调升/降(否则 max/min 永在端点,题太易)
     · 旧值可持续几个 session(同值重复列出即可),再更新为新值,模拟"演化"
     · 想考"遗忘"时,把 trajectory 末尾某 session 的 value 设为 null(表示该字段此后停止/失效)
3. cascades:实体间级联(A 的某字段在某 session 变成 X → 触发 B 的某字段变成 Y)
   · ★ 效果值 Y 必须显式写在 effect.set 里(预声明,保证可解)
4. absent_fields:本场景【根本不存在】的字段(给拒答题用)
   · ★ 不得与上面任何 entity 的 field 重名

【硬约束】
1. 种子扰动:不要照搬 few-shot 样本里的具体人名/数值,换一套【新的虚构值】(防训练集泄漏)
2. 数值字段给真实合理的数字轨迹;人名/状态字段给合理演化
3. session 一律用 0..N-1 的整数,【不要写日期】(代码统一配日历)
4. ★至少 2 个 evolving 字段以 null 结尾(分属【不同实体】)——喂"遗忘 FORGET / 停统计前最后值 PREEXPIRE"两类题;至少 1 条 cascade
5. ★每个数值 evolving 字段的极值(峰或谷)尽量落在中段某周(配合非单调约束),让"最大/最小值在哪周"这类题必须扫全程

【输出严格 JSON】(不要 markdown 包裹)
{"main_theme":"...",
 "entities":[
   {"name":"AI工程部","type":"department","fields":{
      "P0缺陷率":{"type":"evolving","value_type":"百分比",
                  "trajectory":[{"session":0,"value":"2.5%"},{"session":1,"value":"2.5%"},
                                {"session":2,"value":"1.8%"},{"session":4,"value":null}]},
      "负责人":{"type":"evolving","value_type":"人名",
                "trajectory":[{"session":0,"value":"张三"},{"session":3,"value":"李四"}]},
      "部门代号":{"type":"stable","value":"ENG"}}}],
 "cascades":[{"trigger":{"entity":"AI工程部","field":"负责人","becomes":"李四","session":3},
              "effect":{"entity":"AI工程部","field":"汇报对象","set":"CTO"}}],
 "absent_fields":["季度营收","客户满意度"]}"""


def stage_c_world_v10(spec, dims, plan, n_entities=3, n_sessions=6, verbose=True):
    """LLM 填世界表 → assemble_world。返回 (WorldState, table(LLM 原始), issues)。"""
    user = (f"【场景】{spec.get('description_refined')}\n"
            f"【key_fields(参考,可扩展)】{json.dumps(spec.get('key_fields', []), ensure_ascii=False)}\n"
            f"【能力计划(决定世界要支撑哪些题)】{json.dumps(plan.get('items', []), ensure_ascii=False)}\n"
            f"【实体数】≥{n_entities} 个;【session 数】{n_sessions}(session_id 用 0..{n_sessions - 1})\n\n"
            f"设计多实体真值世界表,严格 JSON。")
    table = config.chat_json(
        [{"role": "system", "content": WORLD_SYSTEM}, {"role": "user", "content": user}],
        temperature=0.6, max_tokens=8192)      # ★ 多实体世界表大 + reasoning,4096 会截断
    ws, issues = assemble_world(table)
    if verbose:
        nfields = sum(len(f) for f in ws.entities.values())
        print(f"  实体={len(ws.entities)}, 字段={nfields}, n_sessions={ws.n_sessions}, "
              f"级联={len(ws.cascades)}, absent={ws.absent_fields}")
        for ename, flds in ws.entities.items():
            for fname, tl in flds.items():
                traj = " → ".join(f"{o.op}:{o.value if o.value is not None else '∅'}@s{o.session}" for o in tl._sorted())
                print(f"    [{ename}] {fname}: {traj}")
        if issues:
            print(f"  ⚠ 校验 issues: {issues}")
    return ws, table, issues


# ─────────────────────────────────────────────────────────────────────────────
# Stage D V10 — 渲染:WorldState → 多 session 多文档(★ session-local 防剧透)
# ─────────────────────────────────────────────────────────────────────────────

# 全局口径黑名单:出现这些词 = 读一篇就知道"最新值",破坏跨期记忆考核(V9 剧透根因)
LEAK_BANNED = ["当前", "现在", "最新", "目前", "截至目前", "迄今", "至今", "一直", "历来",
               "维持", "保持不变", "累计", "现任", "如今", "始终", "仍为", "仍是", "依旧"]

CORPUS_SYSTEM_V10 = """你是职场语料合成专家。给你【某一周(session)各部门的当期字段值】,\
合成 1-2 篇【详尽】异质文档(周报/通报/邮件),把这些值自然叙述进去、写厚写满。

【硬约束(防剧透,极重要)】
1. ★ 只写【本期快照值】。严禁出现"当前/现在/最新/目前/截至目前/迄今/一直/历来/维持/累计/现任/仍为"\
这类【全局口径】词——它们会让人读一篇就知道最新值,破坏跨期记忆考核。
2. ★ 严禁回顾历史值、严禁展望未来、严禁叙述"由X变更为Y"的变化过程。每篇只陈述这一周的状态。
3. 正文必须含【本期日期锚点】(如"2025-01-20 当周"/"本周(1月20日)")。
4. 若某字段本期标注 stopped,自然写明"该指标自本期起暂停统计"(★ 不要写它过去的数值)。
5. 风格自然,像真实职场文档;信息【分散】到多篇(数值类归周报、人事类归通报/邮件),不要一篇全包。
6. ★ 每篇写得【详尽充实】(1500-2500 字),像真实的长周报/详细邮件/完整通报:围绕本期给定字段展开背景叙述(成因、处置、影响、下一步、相关方反应等),细节写厚。
   但★【绝不编造其他未给定的字段/指标/人事】,也不给它们数值——只把【给定的facts】写丰满。

【输出严格 JSON】(不要 markdown 包裹)
{"docs":[{"type":"周报|通报|邮件","content":"...","fact_refs":["实体.字段", ...]}]}"""


def _session_facts(ws: WorldState, s: int) -> list[dict]:
    """★ mention-on-change:第 s 周【只渲染本周发生变更(有 op)的字段】, 之后不复述。
    这样"现在 X 是多少"必须跨周回忆最近一次变更(根治弱 KU/IE)。"""
    facts = []
    for ent, flds in ws.entities.items():
        for fname, tl in flds.items():
            op_here = next((o for o in tl.ops if o.session == s), None)
            if op_here is None:
                continue                         # 本周该字段无变更 → 不提
            stopped = op_here.op in (EXPIRE, DELETE)
            facts.append({"entity": ent, "field": fname,
                          "value": None if stopped else op_here.value, "stopped": stopped})
    return facts


def stage_d_corpus_v10(ws: WorldState, verbose=True):
    """WorldState → corpus{sessions:[{session_id,date,docs:[{doc_id,type,content,fact_refs}]}]} + leak_log。"""
    sessions_out, leak_log = [], []
    for s in ws.sessions():
        facts = _session_facts(ws, s)
        if not facts:
            continue
        date = _date_of(s)
        base = (f"【第 {s} 周 / {date}】各部门当期字段值(只许写这些、只写本期):\n"
                f"{json.dumps(facts, ensure_ascii=False)}\n\n合成 1-2 篇【详尽】文档(每篇 1500-2500 字),严格 JSON。")
        docs, hint = [], ""
        for _attempt in range(2):                 # ★ 命中剧透禁词 → 点名重写,最多 1 次重试(闸把活干完)
            try:
                out = config.chat_json(
                    [{"role": "system", "content": CORPUS_SYSTEM_V10},
                     {"role": "user", "content": base + hint}], temperature=0.6, max_tokens=8192)
            except Exception as e:
                leak_log.append({"session": s, "error": str(e)}); docs = []; break
            docs = out.get("docs", []) if isinstance(out, dict) else []
            allhits = sorted({b for d in docs for b in LEAK_BANNED if b in d.get("content", "")})
            if not allhits:
                break
            hint = (f"\n★ 上一版出现了全局口径禁词 {allhits}(会泄漏'最新值')。重写:只陈述本期数值,"
                    f"绝不用'当前/现在/最新/维持/截至…仍为'这类词。")
        for i, d in enumerate(docs):
            d["doc_id"] = f"s{s}_{d.get('type', 'doc')}_{i}"
            rem = [b for b in LEAK_BANNED if b in d.get("content", "")]
            if rem:                               # 重试后仍漏(罕见)→ 记日志
                leak_log.append({"session": s, "doc_id": d["doc_id"], "banned": rem})
        if docs:
            sessions_out.append({"session_id": s, "date": date, "docs": docs})
    if verbose:
        total = sum(len(x["docs"]) for x in sessions_out)
        print(f"  {len(sessions_out)} sessions / {total} docs;★剧透命中 {len(leak_log)}"
              + (f" → {leak_log}" if leak_log else " (干净)"))
    return {"sessions": sessions_out}, leak_log


# ─────────────────────────────────────────────────────────────────────────────
# Stage D-filler V10 — 草堆/干扰:给每周加杂项干扰文档(不碰被追踪字段),制造"草堆"
#   (导师:要实打实的草堆+干扰,但不要极端大海捞针)。mention-on-change 把事实说一次,
#   filler 把这一次淹没在无关内容里 → 才真考记忆。
# ─────────────────────────────────────────────────────────────────────────────

FILLER_SYSTEM = """你生成职场【杂项干扰文档】,给记忆评测语料制造"草堆"(干扰)。
题材任选且每篇不同:团建/培训通知/系统维护公告/招聘进展/报销考勤政策/会议纪要/节日安排/\
办公环境/工具升级/合规提醒…

【硬约束】
1. ★ 绝对不碰【被追踪字段】(连字段名都不要出现,更不能给数值/状态)——只写跟它们【完全无关】的日常杂事。
2. 可以出现部门名(如 AI工程部),但只写无关杂务。
3. 自然、像真实职场文档,每篇 500-900 字,正文带【本周日期】。

【输出严格 JSON】{"docs":[{"type":"通知|纪要|公告|邮件","content":"..."}]}"""


def _tracked_names(ws: WorldState) -> list[str]:
    return sorted({f for flds in ws.entities.values() for f in flds})


_NAME_FIELD_HINTS = ("负责", "对接", "律师", "医师", "销售", "代理", "主治", "承办", "经理", "对象", "汇报", "负责人")


def _tracked_proper_nouns(ws: WorldState) -> set:
    """实体名 + 【人名/机构类字段】的取值(防 filler 同名串扰,只盯名字、不盯状态/数值)。"""
    out = set(ws.entities)
    for flds in ws.entities.values():
        for fname, tl in flds.items():
            if any(h in fname for h in _NAME_FIELD_HINTS):
                for (_s, _d, v) in tl.set_values():
                    if v and _to_num(v) is None and len(str(v)) >= 2:
                        out.add(str(v))
    return out


FILLER_PALETTE_SYSTEM = """你给一个【记忆评测语料】生成"草堆/干扰文档"的【领域配置】。
按给定场景,产出本领域真实会出现的【杂事题材】+ 一批【与该领域风格匹配的虚构人名/机构名】(供干扰文档用)。
★ 干扰文档要像【这个领域】的真文档(律所就律所味、医院就医院味),别串成科技公司味。
只输出 JSON:{"filler_topics":["..."],"person_names":["..."],"org_names":["..."]}"""


def stage_d0_filler_palette(spec, verbose=True) -> dict:
    """每场景一次:LLM 产出领域匹配的 filler 题材 + 虚构人名/机构名池(治 filler 出戏 + 串扰)。"""
    desc = spec.get("description_refined", "") if isinstance(spec, dict) else str(spec)
    try:
        out = config.chat_json(
            [{"role": "system", "content": FILLER_PALETTE_SYSTEM},
             {"role": "user", "content": f"【场景】{desc}\n产出本领域 filler 配置(题材≥10、人名≥8、机构名≥5),严格 JSON。"}],
            temperature=0.6, max_tokens=2048)
        if isinstance(out, dict):
            return {"filler_topics": out.get("filler_topics", []),
                    "person_names": out.get("person_names", []), "org_names": out.get("org_names", [])}
    except Exception:
        pass
    return {"filler_topics": [], "person_names": [], "org_names": []}


def stage_d_add_filler_v10(ws: WorldState, corpus, n_per_session=3, palette=None, verbose=True):
    """给每周(含无变更的空周)加 n 篇干扰文档;碰到被追踪字段名/实体名的一律丢(防泄漏+串扰)。原地改 corpus。"""
    blocked = set(_tracked_names(ws)) | _tracked_proper_nouns(ws)   # ★ 字段名 ∪ 实体/人名
    palette = palette or {}
    topics = palette.get("filler_topics") or []
    names = [n for n in (palette.get("person_names", []) + palette.get("org_names", [])) if n not in blocked]
    palette_hint = (f"\n★ 只用本领域题材:{topics}\n★ 人名/机构名只从这里选:{names}" if (topics or names) else "")
    by_id = {s["session_id"]: s for s in corpus.get("sessions", [])}
    added, dropped = 0, 0
    for s in ws.sessions():
        sess = by_id.get(s) or {"session_id": s, "date": _date_of(s), "docs": []}
        by_id[s] = sess
        user = (f"【第 {s} 周 / {_date_of(s)}】生成 {n_per_session} 篇互不相同的杂项干扰文档。{palette_hint}\n"
                f"★ 严禁出现这些被追踪字段/实体名:{sorted(blocked)[:40]}\n严格 JSON。")
        try:
            out = config.chat_json(
                [{"role": "system", "content": FILLER_SYSTEM}, {"role": "user", "content": user}],
                temperature=0.85, max_tokens=8192)
        except Exception:
            continue
        for i, d in enumerate(out.get("docs", []) if isinstance(out, dict) else []):
            c = d.get("content", "")
            if not c or any(fn in c for fn in blocked):    # 碰了追踪字段 → 丢(防泄漏)
                dropped += 1
                continue
            d.update({"doc_id": f"s{s}_filler_{i}", "is_filler": True, "fact_refs": []})
            sess["docs"].append(d)
            added += 1
    corpus["sessions"] = [by_id[s] for s in sorted(by_id)]
    if verbose:
        total = sum(len(x["docs"]) for x in corpus["sessions"])
        chars = sum(len(dd.get("content", "")) for x in corpus["sessions"] for dd in x["docs"])
        print(f"  ★filler +{added} 篇(丢 {dropped} 碰字段);现 {len(corpus['sessions'])} 周 / "
              f"{total} 篇 / {chars} 字(干扰占比 {added}/{total})")
    return corpus


# ─────────────────────────────────────────────────────────────────────────────
# Stage E V10 — fact-first 直接合成(gt 来自订单,LLM 只写不泄漏的跨周问题)
# ─────────────────────────────────────────────────────────────────────────────

def _order_answer(o) -> str:
    """订单 → 规范答案字符串(展示 + 硬判别器匹配用)。"""
    cap, gt = o.capability, o.gt
    if cap == "ABS":
        return "无法回答:场景中不存在该字段(INSUFFICIENT_EVIDENCE)"
    if cap == "KU":
        return str(gt)
    if cap == "MR":
        return f"{gt.get('value')}(第{gt.get('session')}周)"
    if cap == "TR":
        return f"第{gt.get('session')}周({gt.get('date')})"
    if cap == "IE":
        return str(gt.get("value"))               # 时点切片:答案=那一周的值
    if cap == "CONFLICT":
        return "不一致:各期报告对该字段的记录存在差异"
    if cap == "FORGET":
        return "该指标已暂停统计 / 不再有效"
    if cap == "ORDER":
        return " → ".join(_order_event_label(e) for e in gt)        # 时序答案
    if cap == "DURATION":
        return f"{gt.get('weeks')}周"
    if cap == "PREEXPIRE":
        return str(gt)                                              # 停统计前最后值
    return str(gt)


def _order_event_label(e: dict) -> str:
    return f"{e['field']}停统计" if e.get("op") in ("EXPIRE", "DELETE") else f"{e['field']}变为{e.get('value')}"


QUESTION_SYSTEM_V10 = """你是 memory 评测出题人。给你一道题的【已确定答案】+【涉及哪些周(session)】\
+【能力类型】+【相关文档片段】,你写一道【必须读齐这些周、题面绝不泄漏答案】的自然问题。

【按能力的写法】
- IE 定位:问"在哪几周,某部门的某字段等于某个【特定值】?"(该特定值可写进题面,问的是"哪几周")
- MR 聚合:问"在全部记录周内,某部门某字段最高/最低是多少?"
- TR 时序:问"某部门某字段是【在哪一周】发生变化的?" ★ 只问"哪一周",不得写出变化前/后的值
- KU 最新:问"截至最新一期记录,某部门某字段是多少?" ★ 不得暗示是第几周
- CONFLICT:问"各周关于某部门某字段的记录是否一致?"
- FORGET:问"截至最新一期,某部门某字段是多少?" ★ 题面不得提"暂停/停止"(答案才是"已暂停")
- ABS 拒答:问"某部门的【某个不存在的字段】是多少?"

【硬约束】
1. ★ 题面绝不出现答案本身,也不出现能直接推出答案的关键值/周次
2. 问题自然、口语、像真人问
3. 只输出 JSON,不要 markdown

【输出】{"question":"..."}"""


def _docs_by_session(corpus):
    return {s["session_id"]: "\n".join(d.get("content", "") for d in s.get("docs", []))
            for s in corpus.get("sessions", [])}


def stage_e_synthesize_v10(orders, corpus, verbose=True):
    by_sess = _docs_by_session(corpus)
    raw = []
    for o in orders:
        ans = _order_answer(o)
        ev = o.evidence_sessions or []
        snippets = "\n".join(f"[第{s}周] {by_sess.get(s, '')[:500]}" for s in ev[:4]) or "(ABS 无证据)"
        field_hint = f"{o.entity}.{o.field}" if o.entity else o.field
        extra = ""
        if o.capability == "IE":
            wk = o.aux.get("at_week")
            extra = (f"\n【IE 决策时点复盘】问'复盘第 {wk} 周那次决策——【当时】{field_hint} 是多少?'"
                     f"(回忆过去某一周的值,制造'当时 vs 现在'对抗)。★答案是那一周的值,不写进题面;不要用'哪几周等于某值'句式。")
        elif o.capability == "KU":
            extra = (f"\n【KU 最新值:问'截至最新一期(第 {o.aux.get('at_week')} 周), {field_hint} 是多少'。"
                     f"★答案别写进题面,不得暗示是第几次变更后的值】")
        elif o.capability == "TR":
            extra = (f"\n【TR 首次变化:问'{field_hint} 首次发生变化是在哪一周'。"
                     f"★只问哪一周,不得写出变化前/后的具体值】")
        elif o.capability == "MR":
            extra = f"\n【MR 求】{'最高' if o.aux.get('agg') == 'max' else '最低'}值"
        elif o.capability == "ORDER":
            evs = "、".join(_order_event_label(e) for e in o.gt)
            extra = (f"\n【ORDER 排序】问'把以下几件事按【发生先后】排序:{evs}'。"
                     f"★给事件、不给时间;答案是有序列表;题面绝不写任何周次/日期。")
        elif o.capability == "DURATION":
            extra = (f"\n【DURATION 存续跨度】问'{field_hint} 的【{o.aux.get('value')}】这个值,从它出现那期算起,"
                     f"一共持续了几周才被改成别的?'(★证据里给了从它出现到被改这中间【每一期】周报,期间没再提它即表示没变;"
                     f"据此数持续周数;题面不写任何具体周次/日期)")
        elif o.capability == "PREEXPIRE":
            extra = (f"\n【PREEXPIRE 停前最后值】问'那个【已暂停统计】的 {field_hint},在停掉【前】最后一次是多少?'"
                     f"★题面不写该值、不写'暂停'之外的提示。")
        user = (f"【能力】{o.capability}\n【涉及字段】{field_hint}\n【涉及周】{ev}{extra}\n"
                f"【已确定答案(不可泄漏进题面)】{ans}\n"
                f"【相关文档片段】\n{snippets}\n\n写一道自然问题,严格 JSON。")
        try:
            out = config.chat_json([{"role": "system", "content": QUESTION_SYSTEM_V10},
                                    {"role": "user", "content": user}], temperature=0.6, max_tokens=2048)
            q = (out or {}).get("question", "").strip()
        except Exception:
            q = ""
        if not q:
            continue
        raw.append({"question": q, "answer": ans, "capability": o.capability,
                    "entity": o.entity, "field": o.field, "evidence_sessions": ev,
                    "gt": o.gt, "aux": o.aux})
    if verbose:
        print(f"  raw {len(raw)}/{len(orders)} 题")
    return raw


# ─────────────────────────────────────────────────────────────────────────────
# Stage F V10 — 四闸:grounding(代码) + ★硬判别器(LLM) + judge well_formed(LLM)
#   (gt 由状态机给,judge 不再判 answer_correct;trivial-pass 列为后续 refinement)
# ─────────────────────────────────────────────────────────────────────────────

def _grounding_v10(q, by_sess) -> bool:
    """gt 支撑值是否真出现在【证据周】文档里(防 LLM 渲染丢值/串味)。"""
    cap, gt = q["capability"], q.get("gt")
    text = _norm("\n".join(by_sess.get(s, "") for s in q.get("evidence_sessions", [])))
    if cap == "ABS":
        return True
    if cap == "FORGET":
        raw = "\n".join(by_sess.get(s, "") for s in q.get("evidence_sessions", []))
        return any(k in raw for k in ("暂停", "停止", "不再", "失效"))
    if cap == "KU":
        return _norm(str(gt)) in text
    if cap == "MR":
        return _norm(str(gt.get("value"))) in text
    if cap == "IE":
        return _norm(str(gt.get("value"))) in text       # 时点切片:答案值出现在证据周(题/答错位由"题答自洽"闸统一管)
    if cap == "TR":
        return _norm(str(gt.get("from"))) in text and _norm(str(gt.get("to"))) in text
    if cap == "CONFLICT":
        vals = [v for (_, v) in gt.get("values", [])]
        return len({_norm(v) for v in vals}) >= 2 and sum(1 for v in vals if _norm(v) in text) >= 2
    if cap == "PREEXPIRE":
        return _norm(str(gt)) in text                     # 停前值出现在证据周
    if cap == "DURATION":
        return _norm(str(gt.get("value"))) in text        # 该值出现在证据周
    if cap == "ORDER":
        ev = [e.get("value") for e in gt if e.get("op") not in ("EXPIRE", "DELETE")]
        return sum(1 for v in ev if _norm(str(v)) in text) >= max(1, len(ev) - 1)  # 多数事件值可查
    return True


def _memory_necessity_v10(q, by_sess) -> str:
    """硬判别器:无语料 / 单证据周 baseline 答中 → 伪记忆题。"""
    if q["capability"] == "ABS":
        return "pass"
    ans = q["answer"]
    a0 = _teacher_answer(q["question"], "")                       # 模式1:无语料
    if "不知道" not in a0 and _answers_match(a0, ans):
        return "fail_commonsense"
    ev = q.get("evidence_sessions", [])
    if ev:                                                        # 模式2:只给一个证据周
        a1 = _teacher_answer(q["question"], by_sess.get(ev[-1], ""))
        if "不知道" not in a1 and _answers_match(a1, ans):
            return "fail_single_doc"
    return "pass"


def _oracle_consistency(q, by_sess) -> bool:
    """★题答自洽(通用闸):给【全部证据周文档】+题,强模型作答须与 gt 一致(同 Stage G 的 LLM-judge 口径)。
    对不上 = 题问的根本不是这个 gt(畸形题)→ reject。一根逻辑覆盖所有能力,替掉 per-capability 特判。"""
    if q["capability"] == "ABS":
        return True                                              # ABS 无证据,自洽性由硬判别器/judge 管
    ctx = "\n\n".join(by_sess.get(s, "") for s in q.get("evidence_sessions", []))
    return bool(_judge_grade(q, _teacher_answer(q["question"], ctx)))


JUDGE_WF_SYSTEM = """你判断一道 memory 评测题是否【良构】。只看题面,不评答案对错(答案由代码保证)。
判:well_formed(清晰、能答、像真人问) + leaks(题面是否泄漏了答案或能直接推出答案的关键值/周次)。
只输出 JSON:{"well_formed":true,"leaks":false,"why":"..."}"""


def _judge_wf(q) -> bool:
    try:
        out = config.chat_json(
            [{"role": "system", "content": JUDGE_WF_SYSTEM},
             {"role": "user", "content": f"题:{q['question']}\n(答案,仅你参考、勿外泄):{q['answer']}"}],
            temperature=0.0, max_tokens=1024)      # ★ reasoning 模型给足
        return bool(out.get("well_formed")) and not bool(out.get("leaks"))
    except Exception:
        return True


def _slim(q):
    return {"question": q["question"], "answer": q["answer"], "capability": q["capability"]}


def stage_f_validate_v10(raw, corpus, ws, verbose=True):
    by_sess = _docs_by_session(corpus)
    passed, rejects = [], []
    for q in raw:
        if not _grounding_v10(q, by_sess):
            rejects.append({**_slim(q), "gate": "grounding"}); continue
        if not _oracle_consistency(q, by_sess):                  # ★通用闸:题答自洽(覆盖所有能力)
            rejects.append({**_slim(q), "gate": "oracle_consistency"}); continue
        nec = _memory_necessity_v10(q, by_sess)
        q["memory_necessity"] = nec
        if nec != "pass":
            rejects.append({**_slim(q), "gate": "memory_necessity", "reason": nec}); continue
        if not _judge_wf(q):
            rejects.append({**_slim(q), "gate": "judge_wf"}); continue
        q["gt_source"] = "状态机 state-diff(代码)"
        passed.append(q)
    if verbose:
        print(f"  通过 {len(passed)}/{len(raw)};reject by gate {dict(Counter(r['gate'] for r in rejects))}")
    return passed, rejects


# ─────────────────────────────────────────────────────────────────────────────
# Stage G V10 — 有效性闭环:baseline 作答矩阵 → 难度 + ★区分度 + 模型排名 + FAMA
#   (抄 BenchBench 区分度/BAT 思路 + Memora FAMA;baseline 用"能力不同的上下文消融"当多模型)
# ─────────────────────────────────────────────────────────────────────────────

# 5 个能力梯度不同的 baseline(上下文消融当"模型"):oracle 应全对、no_context 应垫底
BASELINES = ["oracle_full", "recency_last2", "first_half", "random_1", "no_context"]


def _baseline_context(strategy, corpus, rng):
    sess = corpus.get("sessions", [])

    def docs(ss):
        return "\n\n".join(d.get("content", "") for s in ss for d in s.get("docs", []))

    if strategy == "no_context":
        return ""
    if strategy == "recency_last2":
        return docs(sess[-2:])
    if strategy == "first_half":
        return docs(sess[:max(1, len(sess) // 2)])
    if strategy == "random_1":
        return docs([rng.choice(sess)]) if sess else ""
    return docs(sess)                                   # oracle_full


GRADE_SYSTEM = """你判断【模型答案】是否与【标准答案】实质等价(只看意思,不看措辞/格式)。判分规则:
- 日期与周次互通:若标准答案含"第3周(2025-01-27)",模型答"2025年1月27日当周"=对。
- 中文数字/阿拉伯数字互通:"第三周"="第3周";"第0、1周"="2025-01-06和2025-01-13"(对应日期)。
- 数值带不带单位/百分号都算对:"4"="4起"="4次"。
- ★【已停止/遗忘类】标准答案(如"已暂停统计/不再有效"):模型答"已暂停/不再统计/已失效/无法确定/不知道"都算【对】;
  但若给出一个【具体的旧数值】(如"1.8%"),算【错】(用了过期记忆)。
- 拒答类标准答案(INSUFFICIENT/不存在):模型答"不知道/无法回答/不存在/未提及"算对;给了具体值算错。
只输出 JSON:{"equivalent":true 或 false}"""


def _kendall_tau(pred: list, ref: list) -> float:
    """pred/ref 是同一组标签的两个排列;Kendall-τ ∈ [-1,1]。"""
    pos = {x: i for i, x in enumerate(ref)}
    seq = [pos[x] for x in pred if x in pos]
    n = len(seq)
    if n < 2:
        return 0.0
    c = d = 0
    for i in range(n):
        for j in range(i + 1, n):
            c += seq[i] < seq[j]
            d += seq[i] > seq[j]
    return (c - d) / (c + d) if (c + d) else 0.0


_ORDER_EXTRACT_SYS = ("从模型回答里抽取它把给定【事件标签】排成的先后顺序。"
                      '只输出 JSON:{"order":[按模型所述从早到晚排列的事件标签(原样用给定标签)]}')


def _grade_ordering(q, ans: str) -> int:
    """ORDER 判分:抽模型给的顺序 → Kendall-τ vs gt 真值序;τ≥0.5→1(允许个别相邻交换)。"""
    labels = [_order_event_label(e) for e in q["gt"]]          # gt 真值序(早→晚)
    if not ans or ans.strip() in ("", "[error]"):
        return 0
    k = len(labels)
    shown = (labels[k // 2:] + labels[:k // 2]) if k > 2 else labels   # 给 judge 看的乱序,不泄真值序
    try:
        out = config.chat_json(
            [{"role": "system", "content": _ORDER_EXTRACT_SYS},
             {"role": "user", "content": f"事件标签:{shown}\n模型回答:{ans}\n\n模型把它们排成什么先后?"}],
            temperature=0.0, max_tokens=1024)
        pred = out.get("order", []) if isinstance(out, dict) else []
        return int(_kendall_tau(pred, labels) >= 0.5)
    except Exception:
        return 0


def _judge_grade(q, ans: str) -> int:
    """LLM-judge 语义等价判分(治 M5 格式错位:子串匹配冤杀对的答案)。"""
    if q.get("capability") == "ORDER":
        return _grade_ordering(q, ans)
    if not ans or ans.strip() in ("", "[error]"):
        return 0
    try:
        out = config.chat_json(
            [{"role": "system", "content": GRADE_SYSTEM},
             {"role": "user", "content": f"能力:{q['capability']}\n标准答案:{q['answer']}\n"
                                         f"模型答案:{ans}\n\n实质等价吗?"}],
            temperature=0.0, max_tokens=1024)      # ★ reasoning 模型:给足预算,否则 reasoning 吃光→判分吐空→误判
        return int(bool(out.get("equivalent")))
    except Exception:
        return 0


def _point_biserial(item: list[int], total_excl: list[float]) -> float:
    """区分度:本题得分(0/1) 与 (总分−本题) 的点二列相关。负 = 强模型反而错 = 坏题。"""
    n = len(item)
    ones = [total_excl[k] for k in range(n) if item[k] == 1]
    zeros = [total_excl[k] for k in range(n) if item[k] == 0]
    if not ones or not zeros:
        return 0.0
    sd = statistics.pstdev(total_excl)
    if sd == 0:
        return 0.0
    p = len(ones) / n
    return (statistics.mean(ones) - statistics.mean(zeros)) / sd * math.sqrt(p * (1 - p))


def _validity_stats(R, answers, questions, baselines, verbose=True):
    """从作答矩阵 R 算统计(纯代码,无 LLM)——拆出来便于用存盘的 matrix 即时重算。"""
    m = len(questions)
    # ★ 判分地板:无语料模型的 FORGET 不计分(它的"不知道"是无知,不是遗忘)
    def na(b, i):
        return b == "no_context" and questions[i]["capability"] == "FORGET"

    acc = {}                                           # 模型准确率(掩掉 N/A 格)
    for b in baselines:
        cells = [R[b][i] for i in range(m) if not na(b, i)]
        acc[b] = sum(cells) / len(cells) if cells else 0.0
    ranking = sorted(baselines, key=lambda b: acc[b], reverse=True)

    difficulty = []                                    # 每题难度(跨模型平均,掩 N/A)
    for i in range(m):
        cells = [R[b][i] for b in baselines if not na(b, i)]
        difficulty.append(round(sum(cells) / len(cells), 3) if cells else 0.0)

    # 区分度:ABS 不算(它考校准非记忆);总分用掩码后的
    mtotal = {b: sum(R[b][i] for i in range(m) if not na(b, i)) for b in baselines}
    discrimination = []
    for i in range(m):
        if questions[i]["capability"] == "ABS":
            discrimination.append(None); continue
        item = [R[b][i] for b in baselines if not na(b, i)]
        texcl = [mtotal[b] - R[b][i] for b in baselines if not na(b, i)]
        discrimination.append(round(_point_biserial(item, texcl), 3))

    # FAMA(Memora):present=非 FORGET 项、absence=FORGET 项;用 oracle_full 行算(oracle 有上下文)
    orow = R["oracle_full"] if "oracle_full" in R else R[baselines[0]]   # 无全文 oracle 时用最高语境 baseline
    pres = [i for i, q in enumerate(questions) if q["capability"] != "FORGET"]
    absn = [i for i, q in enumerate(questions) if q["capability"] == "FORGET"]
    mpa = sum(orow[i] for i in pres) / len(pres) if pres else 0.0
    faa = sum(orow[i] for i in absn) / len(absn) if absn else 1.0
    lam = len(absn) / (len(pres) + len(absn)) if (pres or absn) else 0.0
    fama = max(0.0, mpa - lam * (1 - faa))

    bad = [i for i in range(m) if discrimination[i] is not None and discrimination[i] < 0]
    out = {
        "matrix": R, "raw_answers": answers, "difficulty": difficulty, "discrimination": discrimination,
        "model_accuracy": {b: round(acc[b], 3) for b in baselines},
        "model_ranking": ranking,
        "fama": {"MPA": round(mpa, 3), "FAA": round(faa, 3), "lambda": round(lam, 3), "FAMA": round(fama, 3)},
        "bad_items": [{"i": i, "q": questions[i]["question"], "disc": discrimination[i]} for i in bad],
        "validity_sane": ranking[-1] == "no_context" and ("oracle_full" not in R or ranking[0] == "oracle_full"),
    }
    if verbose:
        print(f"  模型准确率: {out['model_accuracy']}")
        print(f"  排名(应 oracle 居首 / no_context 垫底): {ranking}  → sane={out['validity_sane']}")
        print(f"  负区分度坏题: {len(bad)} 道" + (f" {[b['i'] for b in out['bad_items']]}" if bad else ""))
        print(f"  FAMA={fama:.3f} (MPA={mpa:.2f} FAA={faa:.2f} λ={lam:.2f}; FORGET 项 {len(absn)})")
    return out


def _robust_answer(question: str, ctx: str) -> str:
    """baseline 作答鲁棒化:空/错答案(长上下文偶发,temp=0 重试还空)→ 升温重试一次,仍空则'不知道'。
    防空答案污染有效性矩阵(空被判错 → 假负区分度,本轮 oracle 就栽在这)。"""
    a = (_teacher_answer(question, ctx) or "").strip()
    if a and a != "[error]":
        return a
    try:
        u = f"【上下文】\n{ctx or '(无任何上下文)'}\n\n【问题】{question}\n\n极简回答(没有就答'不知道'):"
        a2 = (config.chat([{"role": "system", "content": TEACHER_SYSTEM},
                           {"role": "user", "content": u}], temperature=0.4, max_tokens=2048) or "").strip()
        return a2 or "不知道"
    except Exception:
        return "不知道"


def stage_g_validity_v10(questions, corpus, baselines=None, seed=0, verbose=True, full_oracle=True):
    # ★ full_oracle 开关:关掉则去掉"读全文/读半文"的贵 baseline(oracle_full/first_half),
    #   只留 bounded-context 的(recency/random/no_context),让 Stage G 成本【与语料规模无关】。
    if baselines is None:
        baselines = BASELINES if full_oracle else [b for b in BASELINES if b not in ("oracle_full", "first_half")]
    rng = _random.Random(seed)
    answers = {b: [] for b in baselines}               # 原始答案(存盘,以后重判/重算免再跑)
    R = {b: [] for b in baselines}                     # 作答矩阵 R[model][item] ∈ {0,1}
    for b in baselines:
        ctx = _baseline_context(b, corpus, rng)
        for q in questions:
            a = _robust_answer(q["question"], ctx)     # ★ 空答案升温重试(防污染矩阵)
            answers[b].append(a)
            R[b].append(_judge_grade(q, a))            # ★ LLM-judge 判分(治 M5)
    return _validity_stats(R, answers, questions, baselines, verbose)
