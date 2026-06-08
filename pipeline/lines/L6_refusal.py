"""
pipeline.lines.L6_refusal —— L6 抗虚构拒答产线(单能力线:L6_refusal)。设计见 docs/anchors/L6_refusal_design.md。

【它和 L1.ABS 的区别】ABS = 字段【全局缺失】(没人有)→ 干净的"无此项"。
  L6 = 语料里【摆着一个像样的诱饵】,考"宁可说不知道,也别瞎编"(记忆系统最致命的幻觉)。三型(全 code-falsifiable):
    T1 张冠李戴:探针实体 E 缺字段 F,但【兄弟实体 S 有且显眼】(诱去答 S 的值)。
    T2 时间窗外:语料只到第 n 周,问第 (n+2) 周(E 窗内真有 F,更诱去外推)。
    T3 假前提:F 已停统【且其后从未恢复】,却问"恢复后的首值"(诱去答停前值)。
  gold 一律 = 拒答哨兵 INSUFFICIENT(复用 ANSWER_PROTOCOL 的"无此项/查无此记录");型别只进 aux 供分桶。

【为什么近乎零依赖】① 三型诱饵都是世界里的【正常内容】(兄弟字段/窗内值/停前值)→ 渲染器照常渲,render 不改;
  ② gold_scalar 对未知 cap 默认返 None → 本线自己 ground()(骑 ABS 反向判缺席的先例),grounding 核心不改;
  ③ 拒答哨兵已在 ANSWER_PROTOCOL,protocol 不改。

★出生纪律(L6_refusal_design §0):答案侧无周号(拒答无"周答案");T2 的 at_week 进题面【1-based week_label】;
  疑问词走 ans_kind(enumerate 时盖);诱饵实体须【表面可区分】(防近重名真歧义);well_posed 专杀【假拒答】。
"""
from __future__ import annotations
import random
from collections import defaultdict

from pipeline.lines.base import ProductionLine, field_kind, interrogative
from pipeline.world_state import (WorldState, SET, UPDATE, EXPIRE, DELETE, INSUFFICIENT,
                                   _norm, _strip_disambig)

_AT_WEEK_OFFSET = 2          # 窗外问"第 (n_sessions + 2) 周":n=7 → 第9周,不踩"第8周像 0-index"的歧义
_SEED = 20260608


def _surface_distinct(a: str, b: str) -> bool:
    """两实体名【去消歧后】不同 = 表面可区分(否则诱饵≈探针 → 真歧义坏题,见设计 §6 WP1)。"""
    return _strip_disambig(a) != _strip_disambig(b)


def _has_field(ws: WorldState, ent: str, fld: str) -> bool:
    """实体 ent 在世界里是否【曾有】fld 的有效取值(任一 session 的 SET/UPDATE)。
    用于:T1 探针须【从无】此字段(非'有过又停'——那是 FORGET 不是张冠李戴)。"""
    tl = ws.entities.get(ent, {}).get(fld)
    return bool(tl) and any(v for (_s, _d, v) in tl.set_values())


def _stop_no_resume(tl):
    """该字段是否【停统且其后从未恢复】。是 → 返回 (停用 session, 停前值);否 → None。
    T3 假前提的命门:有 EXPIRE/DELETE,且其后无任何 SET/UPDATE(若有 = 恢复了 = 前提成立 = 有答案)。"""
    if tl is None:
        return None
    ops = tl._sorted()
    stop_i = next((i for i, o in enumerate(ops) if o.op in (EXPIRE, DELETE)), None)
    if stop_i is None:
        return None
    if any(o.op in (SET, UPDATE) for o in ops[stop_i + 1:]):    # 其后有恢复 → 不是假前提
        return None
    return (ops[stop_i].session, ops[stop_i].prev)


def _field_owners(ws: WorldState) -> dict:
    """{字段名: {拥有该字段有效值的实体集}}(T1 找'兄弟有、探针无'用)。"""
    owners = defaultdict(set)
    for ent, flds in ws.entities.items():
        for fname, tl in flds.items():
            if any(v for (_s, _d, v) in tl.set_values()):
                owners[fname].add(ent)
    return owners


# ════════════════════════════════════════════════════════════════════════════
# L6 产线
# ════════════════════════════════════════════════════════════════════════════
class RefusalLine(ProductionLine):
    id = "L6_refusal"
    title = "拒答边界"
    memory = "知道记忆边界,诱饵在场也拒答(抗虚构)"
    gt_substrate = "世界结构反向判定(字段在实体上缺席/窗外/停统无恢复)+ 诱饵=世界真实内容"
    implemented = True
    requires: list[str] = []      # T2 窗外恒可行 → 近根线(T1/T3 富类型有则更好,见 feasible)

    def feasible(self, ws, profile: dict) -> tuple[bool, str]:
        """恒可行(T2 窗外任何世界都能产);报告 T1/T3 富类型可用性,便于日志看清这一轮 L6 有没有对抗料。"""
        owners = _field_owners(ws)
        all_fields = {f for flds in ws.entities.values() for f in flds}
        has_t1 = any(ent not in owners[f] for ent in ws.entities for f in all_fields if owners[f])
        has_t3 = any(_stop_no_resume(tl) for flds in ws.entities.values() for tl in flds.values())
        tags = ["T2窗外"] + (["T1张冠李戴"] if has_t1 else []) + (["T3假前提"] if has_t3 else [])
        return True, f"可产拒答型:{'/'.join(tags)}"

    # prepare:无 —— L6 不造新世界,只从既有世界挑拒答机会(诱饵是世界正常内容,渲染器照常渲)

    # ── orders stage:T1/T3(富/对抗)优先,T2(窗外/兜底)填;同(实体,型)只出一题(控冗余、促实体覆盖)──
    def enumerate(self, ws, target: int = 200, wp=None) -> list[dict]:
        profile = (wp or {}).get("domain_profile", {})
        rng = random.Random(_SEED)
        rich = self._enum_misattr(ws, profile) + self._enum_premise(ws, profile)
        weak = self._enum_window(ws, profile)
        rng.shuffle(rich); rng.shuffle(weak)
        out, seen = [], set()
        for o in rich + weak:
            key = (o["entity"], o["aux"]["refusal_type"])
            if key in seen:
                continue
            seen.add(key); out.append(o)
            if len(out) >= target:
                break
        return out

    def _order(self, ent, fld, rtype, ans_kind, lure, evidence, reason, probe=None):
        aux = {"refusal_type": rtype, "ans_kind": ans_kind, "lure": lure, "reason": reason}
        if probe:
            aux["probe"] = probe
        return {"line": self.id, "capability": "L6_refusal", "entity": ent, "field": fld,
                "gt": INSUFFICIENT, "evidence_sessions": sorted(set(evidence)), "aux": aux}

    def _enum_misattr(self, ws, profile) -> list[dict]:
        """T1:探针 E 缺字段 F,兄弟 S(表面可区分)有 F → 诱去答 S 的值。"""
        owners = _field_owners(ws)
        out = []
        for ent in ws.entities:
            for fname, holders in owners.items():
                if ent in holders:                                  # E 自己有 → 非拒答
                    continue
                sibs = sorted(s for s in holders if _surface_distinct(s, ent))
                if not sibs:
                    continue
                S = sibs[0]
                sv = [(s, d, v) for (s, d, v) in ws.entities[S][fname].set_values() if v]
                if not sv:
                    continue
                sess, _d, val = sv[len(sv) // 2]                    # 中段代表值当诱饵
                out.append(self._order(
                    ent, fname, "T1_misattr", field_kind(fname, val, profile),
                    lure={"entity": S, "value": val},
                    evidence=[s for (s, _, _) in sv],
                    reason=f"「{ent}」从无「{fname}」记录;'{val}' 是「{S}」的(张冠李戴诱饵)"))
        return out

    def _enum_premise(self, ws, profile) -> list[dict]:
        """T3:F 停统且其后无恢复 → 问"恢复后首值"(假前提),诱去答停前值。"""
        out = []
        for ent, flds in ws.entities.items():
            for fname, tl in flds.items():
                stop = _stop_no_resume(tl)
                if stop is None:
                    continue
                stop_sess, prestop_val = stop
                if not prestop_val:
                    continue
                out.append(self._order(
                    ent, fname, "T3_premise", field_kind(fname, prestop_val, profile),
                    lure={"entity": ent, "value": prestop_val},
                    evidence=[stop_sess] + [s for (s, _, v) in tl.set_values() if _norm(v) == _norm(prestop_val)],
                    reason=f"「{ent}」的「{fname}」在第{stop_sess}周停统、其后从未恢复(假前提)"))
        return out

    def _enum_window(self, ws, profile) -> list[dict]:
        """T2:语料只到第 n 周,问第 (n+OFFSET) 周(E 窗内真有 F → 更诱去外推)。"""
        n = ws.n_sessions or 0
        if n <= 0:
            return []
        at_week = n + _AT_WEEK_OFFSET                               # 1-based:n=7 → 第9周
        out = []
        for ent, flds in ws.entities.items():
            for fname, tl in flds.items():
                sv = [(s, d, v) for (s, d, v) in tl.set_values() if v]
                if not sv:
                    continue
                out.append(self._order(
                    ent, fname, "T2_window", field_kind(fname, sv[-1][2], profile),
                    lure={"entity": ent, "value": sv[len(sv) // 2][2]},
                    evidence=[s for (s, _, _) in sv],
                    reason=f"语料只到第{n}周,问第{at_week}周(窗外)",
                    probe={"at_week": at_week}))
        return out

    # ── ★护城河:每型重新验"确实答不出"。成立→拒答哨兵;不成立→返非哨兵(well_posed 据此 drop)──
    def gt(self, ws, order: dict):
        aux = order.get("aux") or {}
        t, ent, fld = aux.get("refusal_type"), order.get("entity"), order.get("field")
        if t == "T1_misattr":
            return INSUFFICIENT if not _has_field(ws, ent, fld) else "__HAS_FIELD__"
        if t == "T2_window":
            at_week = (aux.get("probe") or {}).get("at_week")
            n = ws.n_sessions or 0
            return INSUFFICIENT if (isinstance(at_week, int) and at_week > n) else "__IN_WINDOW__"
        if t == "T3_premise":
            return INSUFFICIENT if _stop_no_resume(ws.entities.get(ent, {}).get(fld)) is not None else "__RESUMED__"
        return "__UNKNOWN_TYPE__"

    # ── ★边 A 闸:良定义 well_posed —— 专杀【假拒答】(标了拒答其实有合法答案)──
    def well_posed(self, order: dict, ws) -> tuple:
        aux = order.get("aux") or {}
        t, ent, fld = aux.get("refusal_type"), order.get("entity"), order.get("field")

        # W0 gold 必须是拒答哨兵 + gt() 重算一致(护城河:订单与世界脱钩则 drop)
        if _norm(order.get("gt")) != _norm(INSUFFICIENT):
            return ("drop", f"L6 类型错配:gt 应为拒答哨兵 {INSUFFICIENT},得 {order.get('gt')!r}")
        if _norm(self.gt(ws, order)) != _norm(INSUFFICIENT):
            return ("drop", "假拒答:gt() 重算【确实有答案】(拒答前提不成立)")

        if t == "T1_misattr":
            if _has_field(ws, ent, fld):                            # 探针其实有 → 非拒答(gt 已挡,双保险给可读 reason)
                return ("drop", f"假拒答:「{ent}」其实有「{fld}」记录(非缺失)")
            le = (aux.get("lure") or {}).get("entity")
            if not _has_field(ws, le, fld):
                return ("drop", f"诱饵不实:「{le}」并无「{fld}」(诱不动,退化成 ABS)")
            if not _surface_distinct(le, ent):                      # ★坑#7:诱饵≈探针 → 真歧义坏题
                return ("drop", f"诱饵与探针近重名(「{le}」≈「{ent}」)→ 真歧义,非良构拒答")
            return ("well_posed", "")

        if t == "T2_window":
            at_week = (aux.get("probe") or {}).get("at_week")
            n = ws.n_sessions or 0
            if not (isinstance(at_week, int) and at_week > n):
                return ("drop", f"非窗外:at_week={at_week} 未超过语料 {n} 周")
            if not _has_field(ws, ent, fld):                        # 窗内本就无此字段 = 退化成 ABS,非"窗外"
                return ("drop", f"窗外退化:「{ent}」窗内本无「{fld}」(应归 ABS)")
            return ("well_posed", "")

        if t == "T3_premise":
            if _stop_no_resume(ws.entities.get(ent, {}).get(fld)) is None:
                return ("drop", f"前提其实成立:「{ent}」的「{fld}」非'停统无恢复'(有答案)")
            return ("well_posed", "")

        return ("drop", f"未知拒答型 '{t}'(fail-closed)")

    # ── questions stage:长得像普通题(这就是诱饵);疑问词走 ans_kind;诱饵留语料,题面不藏 ──
    def intent(self, order: dict) -> tuple[str, list]:
        aux = order.get("aux") or {}
        t, ent, fld = aux.get("refusal_type"), order.get("entity", ""), order.get("field", "")
        q = interrogative(aux.get("ans_kind"))
        if t == "T2_window":
            at_week = (aux.get("probe") or {}).get("at_week")
            s = f"第 {at_week} 周,{ent} 的「{fld}」{q}"               # ★at_week 已是 1-based week
        elif t == "T3_premise":
            s = f"{ent} 的「{fld}」在恢复统计之后的首个值{q}"
        else:  # T1_misattr(也是兜底)
            s = f"{ent} 的「{fld}」{q}"
        return s, []          # 拒答题不藏证据:诱饵【要】在语料里;藏的是"不可答",本就不写题面

    # ── ★命门3 接地:验【诱饵在场】(题够诱)+【真答案不误接探针】(拒答成立)。骑 ABS 反向判先例 ──
    def ground(self, order, evidence_docs, all_signal_text=""):
        from pipeline.grounding import attributed
        from pipeline.grounding import STOP_MARKERS
        aux = order.get("aux") or {}
        t, ent = aux.get("refusal_type"), order.get("entity", "")
        docs = [d["content"] for d in evidence_docs]
        lure = aux.get("lure") or {}
        lv, le = lure.get("value"), lure.get("entity")

        if not attributed(lv, le, docs):                            # 诱饵未渲 → 题不够诱,退化
            return ("drop", f"诱饵未渲:'{lv}' 未就近「{le}」(题不够诱)")

        if t == "T1_misattr":
            if attributed(lv, ent, docs):                           # ★诱饵值意外就近探针 = 语料把它接给了 ent = 假拒答
                return ("drop", f"假拒答:诱饵值 '{lv}' 在语料里就近探针「{ent}」(意外可答)")
            return ("grounded", f"诱饵 '{lv}' 就近「{le}」、未误接「{ent}」(拒答成立)")
        if t == "T2_window":
            return ("grounded", f"窗内值 '{lv}' 在场(题诱人);窗外第{(aux.get('probe') or {}).get('at_week')}周语料结构性无")
        if t == "T3_premise":
            if not any(m in _norm(x) for x in docs for m in STOP_MARKERS):
                return ("drop", "停用标记未现(读者看不出'已停统',假前提不成立)")
            return ("grounded", f"停前值 '{lv}' 在 + 停用标记在('恢复后'是假前提)")
        return ("drop", f"未知拒答型 '{t}'(fail-closed)")


# 自检:python -m pipeline.lines.L6_refusal
if __name__ == "__main__":
    import sys
    from pipeline.world_state import assemble_world
    line = RefusalLine()
    checks: list[tuple[bool, str]] = []

    def ck(name, cond):
        checks.append((bool(cond), name))

    # 世界:E1工程部 有缺陷率、无负责人;E2数据部 有负责人=李四(T1诱饵);
    #       E3风控部 的"季度KPI"在 s2 停统、其后无恢复(T3);n_sessions=5(s0..s4 → 周1..5)
    table = {"entities": [
        {"name": "工程部", "fields": {"P0缺陷率": {"type": "evolving",
            "trajectory": [{"session": 0, "value": "2.5"}, {"session": 2, "value": "1.8"}]}}},
        {"name": "数据部", "fields": {"负责人": {"type": "evolving",
            "trajectory": [{"session": 0, "value": "李四"}, {"session": 3, "value": "王强"}]}}},
        {"name": "风控部", "fields": {"季度KPI": {"type": "evolving",
            "trajectory": [{"session": 0, "value": "90"}, {"session": 2, "value": None}]}}},  # s2 EXPIRE 无恢复
    ], "n_sessions": 5}
    profile = {"field_schema": [{"name": "负责人", "kind": "person"},
                                {"name": "P0缺陷率", "kind": "numeric"}, {"name": "季度KPI", "kind": "numeric"}]}
    ws, _ = assemble_world(table)

    # ── feasible / enumerate ──
    ok, why = line.feasible(ws, profile)
    ck("feasible 恒 True", ok is True)
    ck("feasible 报告含 T1/T2/T3", all(x in why for x in ["T1", "T2", "T3"]))

    orders = line.enumerate(ws, target=50, wp={"domain_profile": profile})
    by_type = defaultdict(list)
    for o in orders:
        by_type[o["aux"]["refusal_type"]].append(o)
    ck("产出三型都有", all(by_type[t] for t in ["T1_misattr", "T2_window", "T3_premise"]))
    ck("所有 gt 都是拒答哨兵", all(o["gt"] == INSUFFICIENT for o in orders))
    ck("所有订单 cap=L6_refusal", all(o["capability"] == "L6_refusal" for o in orders))

    # T1:应有"工程部的负责人"(工程部无负责人、数据部有=李四)
    t1 = next((o for o in by_type["T1_misattr"] if o["entity"] == "工程部" and o["field"] == "负责人"), None)
    ck("T1 命中:问工程部的负责人(它没有)", t1 is not None)
    lure_val = t1["aux"]["lure"]["value"] if t1 else None      # 中段取值(数据部负责人之一)
    ck("T1 诱饵实体=数据部、值是其负责人之一", t1 and t1["aux"]["lure"]["entity"] == "数据部" and lure_val in {"李四", "王强"})
    ck("T1 疑问词=是谁(person)", t1 and "是谁" in line.intent(t1)[0])
    ck("T1 题面不剧透'不可答'(长得像普通题)", t1 and lure_val not in line.intent(t1)[0] and line.intent(t1)[1] == [])

    # T2:at_week = 1-based = n_sessions+2;题面"第N周"(★不是裸 session,验 off-by-one 不复发)
    t2 = by_type["T2_window"][0]
    _exp_week = ws.n_sessions + _AT_WEEK_OFFSET
    ck(f"T2 at_week = n_sessions+2 (={_exp_week})", t2["aux"]["probe"]["at_week"] == _exp_week)
    ck("T2 题面是 1-based 周号(非裸 session)", f"第 {_exp_week} 周" in line.intent(t2)[0])

    # T3:风控部.季度KPI
    t3 = next((o for o in by_type["T3_premise"] if o["entity"] == "风控部"), None)
    ck("T3 命中:风控部.季度KPI(停统无恢复)", t3 is not None)
    ck("T3 诱饵=停前值90", t3 and t3["aux"]["lure"]["value"] == "90")

    # ── 护城河:gt() 重算 == 拒答哨兵 ──
    ck("校验闸:gt() 重算全 == 拒答哨兵", all(line.gt(ws, o) == INSUFFICIENT for o in orders))

    # ════════════════════════════════════════════════════════════════════════
    # ★边 A 闸 well_posed:WP 必过;IP 各触发对应 drop(专杀假拒答 + 近重名 + 退化)
    # ════════════════════════════════════════════════════════════════════════
    ck("WP-T1 well-posed", line.well_posed(t1, ws) == ("well_posed", ""))
    ck("WP-T2 well-posed", line.well_posed(t2, ws) == ("well_posed", ""))
    ck("WP-T3 well-posed", line.well_posed(t3, ws) == ("well_posed", ""))

    # IP1 假拒答(T1):探针其实有该字段 → drop。用"数据部的负责人"(数据部真有负责人)伪造一道 T1
    ip_fake = line._order("数据部", "负责人", "T1_misattr", "person",
                          lure={"entity": "工程部", "value": "李四"}, evidence=[0], reason="伪造")
    st, why = line.well_posed(ip_fake, ws)
    ck("IP1 假拒答(探针其实有该字段)→drop", st == "drop")

    # IP2 诱饵近重名(T1):探针"数据部"、诱饵"数据部"(同名)→ 真歧义 drop
    #   构造:探针缺某字段、诱饵实体名去消歧后==探针。用"工程部"做探针、"工程部 "(尾空格)做诱饵名模拟近重名。
    coll_table = {"entities": [
        {"name": "甲部", "fields": {"营收": {"type": "stable", "value": "100"}}},
        {"name": "甲部x", "fields": {"负责人": {"type": "stable", "value": "钱七"}}},
    ], "n_sessions": 3}
    coll_ws, _ = assemble_world(coll_table)
    # 让两名去消歧后相同(模拟近重名):monkeypatch 不便,这里直接验 _surface_distinct 行为 + well_posed 会拦
    ip_coll = line._order("甲部", "负责人", "T1_misattr", "person",
                          lure={"entity": "甲部", "value": "钱七"}, evidence=[0], reason="近重名")
    # 注:lure.entity=="甲部"==ent → _surface_distinct False → 应 drop(诱饵==探针自身,既不实也歧义)
    ck("IP2 诱饵==探针自身→drop", line.well_posed(ip_coll, coll_ws)[0] == "drop")

    # IP3 非窗外(T2):at_week ≤ n → drop
    ip_inwin = line._order("数据部", "负责人", "T2_window", "person",
                           lure={"entity": "数据部", "value": "李四"}, evidence=[0],
                           reason="非窗外", probe={"at_week": 3})    # 3 ≤ 5
    ck("IP3 at_week≤n(非窗外)→drop", line.well_posed(ip_inwin, ws)[0] == "drop")

    # IP4 前提其实成立(T3):字段停后又恢复 → drop
    resume_table = {"entities": [{"name": "甲", "fields": {"f": {"type": "evolving",
        "trajectory": [{"session": 0, "value": "a"}, {"session": 1, "value": None}, {"session": 3, "value": "b"}]}}}],
        "n_sessions": 5}   # s1 停、s3 又 SET=b → 恢复了
    resume_ws, _ = assemble_world(resume_table)
    ip_resume = line._order("甲", "f", "T3_premise", "text",
                            lure={"entity": "甲", "value": "a"}, evidence=[0], reason="其实恢复了")
    ck("IP4 停后又恢复(前提成立)→drop", line.well_posed(ip_resume, resume_ws)[0] == "drop")

    # ── ground():诱饵在+真答案不误接 → grounded;诱饵未渲 → drop;诱饵误接探针 → 假拒答 drop ──
    docs_ok = [{"session": 0, "content": f"数据部本周负责人为{lure_val}。", "is_conflict": False},
               {"session": 0, "content": "工程部本周P0缺陷率2.5。", "is_conflict": False}]
    ck("ground T1:诱饵在(数据部)+未误接工程部 → grounded",
       line.ground(t1, docs_ok)[0] == "grounded")
    docs_misattr = [{"session": 0, "content": f"工程部本周负责人为{lure_val}。", "is_conflict": False}]  # 诱饵误接探针
    ck("ground T1:诱饵误接探针工程部 → 假拒答 drop",
       line.ground(t1, docs_misattr)[0] == "drop")
    docs_no_lure = [{"session": 0, "content": "某无关内容。", "is_conflict": False}]
    ck("ground T1:诱饵根本没渲 → drop", line.ground(t1, docs_no_lure)[0] == "drop")
    docs_t3 = [{"session": 0, "content": "风控部季度KPI为90。", "is_conflict": False},
               {"session": 2, "content": "风控部季度KPI本周起停止统计。", "is_conflict": False}]
    ck("ground T3:停前值90在+停用标记在 → grounded", line.ground(t3, docs_t3)[0] == "grounded")

    npass = sum(1 for ok, _ in checks if ok)
    for ok, name in checks:
        if not ok:
            print(f"  ✗ {name}")
    print(f"[L6_refusal self-test] {npass}/{len(checks)} PASS")
    sys.exit(0 if npass == len(checks) else 1)
