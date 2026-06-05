"""
pipeline.world_state — V10 地基:会算账的状态机真值世界(redesign_v10.md Stage C / T1)。

核心思想:每个 entity 的每个 field 一条时间线 ops=[SET/UPDATE/DELETE/EXPIRE];
gt(真答案)在生成时烘焙、由【代码】机械算(单一口径),不再交给 LLM 拍。
- slice(question_date) = Memora 的 state-diff(返回当时有效值 / 已失效)
- 能力 ↔ op 映射(IE/MR/TR/KU/CONFLICT/FORGET/ABS)的 gt 全在本模块
- MEME 的 Cas/Abs/Del 三类 = UPDATE(有替代值)/EXPIRE(无替代值)/DELETE 三种 op

纯代码、无 LLM 依赖,可单测:`./venv/bin/python pipeline/world_state.py` 跑自检。
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any, Optional
import re

# ── op 常量(Memora state-diff 三 op = MEME 三类)──
SET, UPDATE, DELETE, EXPIRE = "SET", "UPDATE", "DELETE", "EXPIRE"
INVALID = "__INVALIDATED__"             # 哨兵:字段已被 DELETE/EXPIRE(该忘了)
INSUFFICIENT = "INSUFFICIENT_EVIDENCE"  # 哨兵:字段从未出现(ABS)


def _to_num(s: Any) -> Optional[float]:
    """从 '2.5%' / '15次' / '1.8' 抽数值,抽不出返回 None。"""
    if s is None:
        return None
    m = re.search(r"-?\d+\.?\d*", str(s))
    return float(m.group()) if m else None


def _norm(s: Any) -> str:
    """口径统一:用于值相等比较(去空格、全角→半角、去尾部 % 。)。"""
    if s is None:
        return ""
    t = str(s).strip().replace(" ", "")
    t = t.translate(str.maketrans("０１２３４５６７８９％．", "0123456789%."))
    return t.rstrip("%。.")


@dataclass
class Op:
    session: int
    date: str                       # "YYYY-MM-DD"(字符串可直接比大小)
    op: str                         # SET / UPDATE / DELETE / EXPIRE
    value: Optional[str] = None     # DELETE/EXPIRE 时为 None
    prev: Optional[str] = None      # 改值前的旧值(TR 用)


@dataclass
class Timeline:
    ops: list[Op] = field(default_factory=list)

    def _sorted(self) -> list[Op]:
        return sorted(self.ops, key=lambda o: (o.date, o.session))

    def _fold(self, stop_key, key_of) -> str:
        """从头按时间放电影,放到 stop_key(含)为止,返回当时有效值。"""
        cur = INSUFFICIENT
        for o in self._sorted():
            if key_of(o) > stop_key:
                break
            if o.op in (SET, UPDATE):
                cur = o.value
            elif o.op in (DELETE, EXPIRE):
                cur = INVALID
        return cur

    def value_at(self, date: str) -> str:
        return self._fold(date, lambda o: o.date)

    def value_at_session(self, session: int) -> str:
        return self._fold(session, lambda o: o.session)

    def latest_valid(self) -> str:
        return self._fold(10 ** 9, lambda o: o.session)

    def set_values(self) -> list[tuple[int, str, str]]:
        """所有出现过的有效取值 [(session, date, value)],按时间序(MR 聚合用)。"""
        return [(o.session, o.date, o.value) for o in self._sorted() if o.op in (SET, UPDATE)]

    def change_ops(self) -> list[Op]:
        """真正发生【值变化】的 op(TR 用):删/过期,或新值≠旧值。"""
        return [o for o in self._sorted()
                if o.op in (DELETE, EXPIRE)
                or (o.prev is not None and _norm(o.value) != _norm(o.prev))]


@dataclass
class WorldState:
    entities: dict[str, dict[str, Timeline]] = field(default_factory=dict)
    cascades: list[dict] = field(default_factory=list)   # DAG 级联(MEME),显式预声明替代值
    absent_fields: list[str] = field(default_factory=list)
    n_sessions: int = 0                                  # session 总数(0 → 从 ops 推)
    conflicts: list[dict] = field(default_factory=list)  # ★L5 跨来源矛盾侧信道(canonical 时间线不动,小道值另渲)

    def timeline(self, entity: str, fld: str) -> Optional[Timeline]:
        return self.entities.get(entity, {}).get(fld)

    def has_field(self, fld: str) -> bool:
        return any(fld in flds for flds in self.entities.values())

    def sessions(self) -> list[int]:
        """全部 session 编号(IE-locate 要扫全程,含值持续但无 op 的 session)。"""
        if self.n_sessions:
            return list(range(self.n_sessions))
        mx = max((o.session for flds in self.entities.values()
                  for tl in flds.values() for o in tl.ops), default=-1)
        return list(range(mx + 1))

    # ── 序列化(每阶段落盘,治 v9 的可回溯性缺口)──
    def to_dict(self) -> dict:
        return {
            "entities": {e: {f: [asdict(o) for o in tl.ops] for f, tl in flds.items()}
                         for e, flds in self.entities.items()},
            "cascades": self.cascades,
            "absent_fields": self.absent_fields,
            "n_sessions": self.n_sessions,
            "conflicts": self.conflicts,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "WorldState":
        ents = {e: {f: Timeline([Op(**o) for o in ops]) for f, ops in flds.items()}
                for e, flds in d.get("entities", {}).items()}
        return cls(ents, d.get("cascades", []), d.get("absent_fields", []), d.get("n_sessions", 0),
                   d.get("conflicts", []))


# ─────────────────────────────────────────────────────────────────────────────
# 能力 ↔ op 映射:gt 全由【代码】机械算(单一口径,argmin/argmax 都对 = 做对的"结构裁判")
# ─────────────────────────────────────────────────────────────────────────────

def gt_ie(ws: WorldState, entity: str, fld: str, session: int) -> str:
    """IE:某 session 当时的值。"""
    tl = ws.timeline(entity, fld)
    return tl.value_at_session(session) if tl else INSUFFICIENT


def gt_ku(ws: WorldState, entity: str, fld: str) -> str:
    """KU:最新有效值(被 EXPIRE/DELETE 则为 INVALID → 走 FORGET 语义)。"""
    tl = ws.timeline(entity, fld)
    return tl.latest_valid() if tl else INSUFFICIENT


def gt_mr(ws: WorldState, entity: str, fld: str, agg: str = "max") -> dict:
    """MR:跨 session 极值 + ★ argmax/argmin 都给(治 v8 漏 argmin 的 bug)。"""
    tl = ws.timeline(entity, fld)
    cand = [(s, d, v, _to_num(v)) for (s, d, v) in (tl.set_values() if tl else []) if _to_num(v) is not None]
    if not cand:
        return {"value": INSUFFICIENT}
    pick = (max if agg == "max" else min)(cand, key=lambda x: x[3])
    return {"value": pick[2], "session": pick[0], "date": pick[1], "agg": agg}


def gt_tr(ws: WorldState, entity: str, fld: str, to_value: Optional[str] = None) -> Any:
    """TR:值变化发生的时刻(to_value=None → 第一次变化;否则变成 to_value 的那次)。"""
    tl = ws.timeline(entity, fld)
    if not tl:
        return INSUFFICIENT
    for o in tl.change_ops():
        if to_value is None or _norm(o.value) == _norm(to_value):
            return {"session": o.session, "date": o.date, "from": o.prev, "to": o.value}
    return INSUFFICIENT


def gt_conflict(ws: WorldState, entity: str, fld: str) -> dict:
    """CONFLICT:同字段跨 session 是否出现过不同值。"""
    vals = (ws.timeline(entity, fld).set_values() if ws.timeline(entity, fld) else [])
    distinct = {_norm(v) for (_, _, v) in vals}
    return {"conflict": len(distinct) > 1, "values": [(s, v) for (s, _, v) in vals]}


def gt_forget(ws: WorldState, entity: str, fld: str, question_date: str) -> Any:
    """FORGET:question_date 切片若已被 EXPIRE/DELETE → forgotten(= Memora forgetting_absence)。"""
    tl = ws.timeline(entity, fld)
    if not tl:
        return INSUFFICIENT
    v = tl.value_at(question_date)
    return {"forgotten": v == INVALID, "value": None if v == INVALID else v}


def gt_absent(ws: WorldState, fld: str) -> Any:
    """ABS:语料里任何 timeline 都没这个字段 → INSUFFICIENT_EVIDENCE。"""
    return {"present": True} if ws.has_field(fld) else INSUFFICIENT


def stable_query_weeks(ws: WorldState, entity: str, fld: str):
    """mention-on-change 下的【可问查询周】:本周该字段【无 op】(没被陈述)、但有有效值、
    且之前发生过 ≥1 次变更。返回 [(query_week N, 值来源 change_week, value)]。
    → 在 N 问"X 是多少"必须回忆最近一次变更(跨周),单看第 N 周答不出。"""
    tl = ws.timeline(entity, fld)
    if not tl:
        return []
    changes = sorted(o.session for o in tl.ops if o.op in (SET, UPDATE))
    op_weeks = {o.session for o in tl.ops}
    out = []
    for N in ws.sessions():
        if N in op_weeks:
            continue                                   # 本周被陈述 → 不算
        v = tl.value_at_session(N)
        if v in (INVALID, INSUFFICIENT):
            continue
        prior = [c for c in changes if c < N]
        if prior:
            out.append((N, prior[-1], v))
    return out


# ── V11 Tier 0 新 gt 函数(纯代码,可单测)──

def gt_event_order(ws: WorldState, entity: str):
    """ORDER:该实体【所有变更事件】(UPDATE/EXPIRE/DELETE)按 (date,session) 排序 = 真值时序。
    返回 [{field,value,session,date,op}](初始 SET 不算"变更",不计)。order_gen 从中采 k 个。"""
    flds = ws.entities.get(entity, {})
    events = []
    for fname, tl in flds.items():
        for o in tl._sorted():
            if o.op in (UPDATE, EXPIRE, DELETE):
                events.append({"field": fname, "value": o.value, "session": o.session,
                               "date": o.date, "op": o.op})
    events.sort(key=lambda e: (e["date"], e["session"]))
    return events


def gt_duration(ws: WorldState, entity: str, fld: str, value: str):
    """DURATION:某 value 从被设到被下一个 op 取代,持续几个 session(weeks)。"""
    tl = ws.timeline(entity, fld)
    if not tl:
        return INSUFFICIENT
    ops = tl._sorted()
    for i, o in enumerate(ops):
        if o.op in (SET, UPDATE) and _norm(o.value) == _norm(value):
            end = ops[i + 1].session if i + 1 < len(ops) else (ws.n_sessions or o.session + 1)
            return {"value": value, "weeks": end - o.session, "start": o.session, "end": end}
    return INSUFFICIENT


def gt_pre_expire(ws: WorldState, entity: str, fld: str):
    """复合 FORGET→IE:已 EXPIRE/DELETE 字段在停掉【前】的最后值(= EXPIRE op 的 prev)。"""
    tl = ws.timeline(entity, fld)
    if not tl:
        return INSUFFICIENT
    for o in tl._sorted():
        if o.op in (EXPIRE, DELETE):
            return {"value": o.prev, "expire_session": o.session}
    return INSUFFICIENT


def gt_multihop(ws: WorldState, start: str, field_path, at_week: int):
    """★L2 关系多跳:沿【软外键】field_path 在 at_week 逐跳遍历状态机。
    非末跳的值 = 下一跳实体名(解外键),末跳值 = 答案。中间断链/取不到 → INSUFFICIENT。
    时序:边随周变(负责人换→汇报关系变),故答案随 at_week 变,gt 由代码切片重算。
    返回 {answer, path_evidence:[{entity,field,value,set_session}], path, at_week}。"""
    cur, evidence = start, []
    for f in field_path:
        tl = ws.timeline(cur, f)
        if tl is None:
            return {"answer": INSUFFICIENT, "path_evidence": evidence, "broke_at": [cur, f], "at_week": at_week}
        val = tl.value_at_session(at_week)
        if val is None or val in (INVALID, INSUFFICIENT):
            return {"answer": INSUFFICIENT, "path_evidence": evidence, "broke_at": [cur, f], "at_week": at_week}
        # 该值在哪一周被设(mention-on-change 落点 = ≤at_week 的最后一次取该值的变更周)
        set_sess = at_week
        for o in tl._sorted():
            if o.session <= at_week and str(o.value) == str(val):
                set_sess = o.session
        evidence.append({"entity": cur, "field": f, "value": val, "set_session": set_sess})
        cur = val   # 下一跳实体 = 本跳值;末跳后 cur = 答案
    return {"answer": cur, "path_evidence": evidence, "path": list(field_path), "at_week": at_week}


def _shape_issues(ws: WorldState):
    """★非单调校验:数值 evolving 字段若 max 和 min 都落在端点(={首,尾})⇒ 单调 ⇒ MR 退化,记 issue。"""
    issues = []
    for ent, flds in ws.entities.items():
        for fname, tl in flds.items():
            nums = [(s, _to_num(v)) for (s, _, v) in tl.set_values() if _to_num(v) is not None]
            if len(nums) >= 3:
                sess = [s for s, _ in nums]
                vals = [n for _, n in nums]
                amax = sess[vals.index(max(vals))]
                amin = sess[vals.index(min(vals))]
                if {amax, amin} <= {sess[0], sess[-1]}:
                    issues.append(f"{ent}.{fname} 数值轨迹单调(极值都在端点), MR 退化")
    return issues


def validate(ws: WorldState, table: dict = None) -> list[dict]:
    """★W.3 世界质量【结构化缺陷清单】(CRITIC 修复轮:算出的缺陷不丢、定向重生成坏字段)。
    每条 = {entity, field, type, detail}。type:
      monotonic     —— 数值轨迹单调(极值落端点),MR 退化;
      fake_evolving —— 声明 evolving 却全程只有 1 个值(且没停用)= 没演化起来。
    table = LLM 原始世界表(用于判 declared type);缺省则跳过 fake_evolving。"""
    decl = {}
    for e in (table or {}).get("entities", []):
        nm = e.get("name") or e.get("id")
        for fn, sp in (e.get("fields") or {}).items():
            decl[(nm, fn)] = sp.get("type") or ("stable" if ("value" in sp and "trajectory" not in sp) else "evolving")
    out: list[dict] = []
    for ent, flds in ws.entities.items():
        for fname, tl in flds.items():
            sv = tl.set_values()
            nums = [(s, _to_num(v)) for (s, _, v) in sv if _to_num(v) is not None]
            distinct = {_norm(v) for (_, _, v) in sv}
            has_stop = any(o.op in (DELETE, EXPIRE) for o in tl.ops)
            if len(nums) >= 3:
                sess = [s for s, _ in nums]
                vals = [n for _, n in nums]
                amax, amin = sess[vals.index(max(vals))], sess[vals.index(min(vals))]
                if {amax, amin} <= {sess[0], sess[-1]}:
                    out.append({"entity": ent, "field": fname, "type": "monotonic",
                                "detail": "数值轨迹单调(极值落首/尾)→ 需让峰或谷落在【非端点】的中间某周"})
            if decl.get((ent, fname)) == "evolving" and len(distinct) < 2 and not has_stop:
                out.append({"entity": ent, "field": fname, "type": "fake_evolving",
                            "detail": "标 evolving 却全程只有 1 个值 → 需给【≥2 个不同值】的演化轨迹"})
    return out


def answer(ws: WorldState, capability: str, **kw) -> Any:
    """能力 → gt 派发(T2 '点菜' 生成器的种子)。"""
    cap = capability.upper()
    if cap == "IE":       return gt_ie(ws, kw["entity"], kw["field"], kw["session"])
    if cap == "KU":       return gt_ku(ws, kw["entity"], kw["field"])
    if cap == "MR":       return gt_mr(ws, kw["entity"], kw["field"], kw.get("agg", "max"))
    if cap == "TR":       return gt_tr(ws, kw["entity"], kw["field"], kw.get("to_value"))
    if cap == "CONFLICT": return gt_conflict(ws, kw["entity"], kw["field"])
    if cap == "FORGET":   return gt_forget(ws, kw["entity"], kw["field"], kw["question_date"])
    if cap == "ABS":      return gt_absent(ws, kw["field"])
    raise ValueError(f"未知能力 {capability}")


# ─────────────────────────────────────────────────────────────────────────────
# assemble_world — LLM 填的「每实体每字段取值表」→ WorldState(代码 diff 成 ops,T3 用)
#   LLM 只填表(素材),代码负责:diff 出 SET/UPDATE/EXPIRE、套级联、配日历、校验。gt 仍归代码。
# ─────────────────────────────────────────────────────────────────────────────

def _date_of(session: int, base: str = "2025-01-06", step_days: int = 7) -> str:
    from datetime import datetime as _d, timedelta as _td
    return (_d.strptime(base, "%Y-%m-%d") + _td(days=session * step_days)).strftime("%Y-%m-%d")


def week_label(session: int) -> int:
    """session 索引(0-based,代码内部口径)→ 人读周次(1-based)。
    ★命门1 单一真源:凡【题面/语料】出现"第N周",N 必须经此函数派生,确保问题与语料同一把尺。
    根治 run#3 盲审 Q01 的 off-by-one + 两套起点——0-based 索引被人/LLM 自发读成 1-based 是病根。
    (at_week / gt / grounding 仍用 0-based 索引;week_label 只在【渲染成文字】那一刻用。)"""
    return session + 1


def _as_int(x, default=0):
    """LLM 偶发把 session 写成字符串("3")/浮点;稳健强转 int,坏值回退 default(防 max()/排序炸)。"""
    try:
        return int(x)
    except (TypeError, ValueError):
        try:
            return int(float(x))
        except (TypeError, ValueError):
            return default


def _traj_to_ops(traj: list[dict], date_of) -> list[Op]:
    """把 [{session,value}] 轨迹 diff 成 ops:首现=SET、变值=UPDATE、value 为空=EXPIRE(停统计)。
    值持续不变则不记 op(由 value_at fold 前向填充,IE-locate 扫全程仍能命中)。"""
    ops: list[Op] = []
    last = None
    for p in sorted(traj, key=lambda x: _as_int(x.get("session"), 0)):
        s, v = _as_int(p.get("session"), 0), p.get("value")
        if v is None or str(v).strip() == "":
            if last is not None:
                ops.append(Op(s, date_of(s), EXPIRE, None, last)); last = None
            continue
        v = str(v)
        if last is None:
            ops.append(Op(s, date_of(s), SET, v, None))
        elif _norm(v) != _norm(last):
            ops.append(Op(s, date_of(s), UPDATE, v, last))
        last = v
    return ops


def assemble_world(table: dict, base: str = "2025-01-06", step_days: int = 7) -> tuple[WorldState, list[str]]:
    """table = LLM 输出的世界表(见 redesign_v10 Stage C schema)。返回 (WorldState, issues)。"""
    date_of = lambda s: _date_of(s, base, step_days)  # noqa: E731
    entities: dict[str, dict[str, Timeline]] = {}
    max_sess = 0

    for ent in table.get("entities", []):
        name = ent.get("name") or ent.get("id")
        if not name:
            continue
        flds: dict[str, Timeline] = {}
        for fname, spec in (ent.get("fields") or {}).items():
            if (spec.get("type") == "stable") or ("value" in spec and "trajectory" not in spec):
                flds[fname] = Timeline([Op(0, date_of(0), SET, str(spec.get("value")), None)])
            else:
                traj = spec.get("trajectory", [])
                for p in traj:
                    max_sess = max(max_sess, _as_int(p.get("session"), 0))
                ops = _traj_to_ops(traj, date_of)
                if ops:
                    flds[fname] = Timeline(ops)
        if flds:
            entities[name] = flds

    # 套级联(DAG):trigger 触发时,在 effect 字段注入预声明的替代值(可解性)
    for c in table.get("cascades", []):
        eff, trig = c.get("effect", {}), c.get("trigger", {})
        e, f, val = eff.get("entity"), eff.get("field"), eff.get("set")
        sess = _as_int(trig.get("session"), None) if trig.get("session") is not None else None
        if not (e and f and val is not None and sess is not None):
            continue
        max_sess = max(max_sess, sess)
        tl = entities.setdefault(e, {}).get(f)
        if tl is not None and _norm(tl.value_at_session(sess)) == _norm(str(val)):
            continue  # LLM 已在轨迹里编码了该效果 → 不重复注入(防重复 op)
        prev = tl.value_at_session(sess - 1) if tl else None
        prev = None if prev in (INVALID, INSUFFICIENT) else prev
        op = Op(sess, date_of(sess), SET if prev is None else UPDATE, str(val), prev)
        entities.setdefault(e, {}).setdefault(f, Timeline([])).ops.append(op)

    absent = list(table.get("absent_fields", []))
    ws = WorldState(entities, table.get("cascades", []), absent, n_sessions=max_sess + 1)

    # 校验(非致命,收集 issues)
    issues: list[str] = []
    present = {f for flds in entities.values() for f in flds}
    for f in list(absent):
        if f in present:                       # 矛盾:声称不存在却出现了 → 从 absent 移除
            issues.append(f"absent_field '{f}' 实际出现,已移除"); absent.remove(f)
    for ename, flds in entities.items():
        for fname, tl in flds.items():
            spec = next((s for e in table.get("entities", []) if (e.get("name") or e.get("id")) == ename
                         for fn, s in (e.get("fields") or {}).items() if fn == fname), {})
            if spec.get("type") == "evolving":
                distinct = {_norm(v) for (_, _, v) in tl.set_values()}
                if len(distinct) < 2 and not any(o.op in (DELETE, EXPIRE) for o in tl.ops):
                    issues.append(f"{ename}.{fname} 标 evolving 但只有 1 个值(演化不成立)")
    ws.absent_fields = absent
    issues += _shape_issues(ws)                          # ★V11:数值单调告警
    return ws, issues


# ─────────────────────────────────────────────────────────────────────────────
# demo 世界(office 周报场景)+ 自检:给定 timeline + date,断言每个能力的 gt 正确
# ─────────────────────────────────────────────────────────────────────────────

_DATES = {0: "2025-01-06", 1: "2025-01-13", 2: "2025-01-20",
          3: "2025-01-27", 4: "2025-02-03", 5: "2025-02-10"}


def build_demo_world() -> WorldState:
    D = _DATES

    def tl(*steps):  # steps: (session, op, value, prev)
        return Timeline([Op(s, D[s], op, val, prev) for (s, op, val, prev) in steps])

    ai = {
        # 变值字段:2.5→1.8→2.8→停统计(EXPIRE)
        "P0缺陷率": tl((0, SET, "2.5%", None), (1, UPDATE, "1.8%", "2.5%"),
                       (3, UPDATE, "2.8%", "1.8%"), (5, EXPIRE, None, "2.8%")),
        # 数值字段:12→15→9(测 MR 的 argmax/argmin)
        "Oncall数": tl((0, SET, "12次", None), (2, UPDATE, "15次", "12次"), (4, UPDATE, "9次", "15次")),
        # 人名字段:张三→李四(测 KU/TR)
        "负责人":   tl((0, SET, "张三", None), (2, UPDATE, "李四", "张三")),
        # 稳定字段:只 SET 一次(CONFLICT=False 对照)
        "部门代号": tl((0, SET, "ENG", None)),
    }
    return WorldState({"AI工程部": ai}, cascades=[], absent_fields=["季度营收", "团队规模"], n_sessions=6)


def demo_table() -> dict:
    """手写的「LLM 世界表」样例:含值持续(s1 没变)、停统计(EXPIRE)、级联,喂 assemble_world 自检。"""
    return {
        "main_theme": "AI 工程团队周报",
        "entities": [
            {"name": "AI工程部", "type": "department", "fields": {
                "P0缺陷率": {"type": "evolving", "trajectory": [
                    {"session": 0, "value": "2.5%"}, {"session": 1, "value": "2.5%"},  # ★ s1 持续不变
                    {"session": 2, "value": "1.8%"}, {"session": 4, "value": None}]},   # ★ s4 停统计
                "负责人": {"type": "evolving", "trajectory": [
                    {"session": 0, "value": "张三"}, {"session": 3, "value": "李四"}]},
                "部门代号": {"type": "stable", "value": "ENG"}}},
            {"name": "数据平台部", "type": "department", "fields": {
                "负责人": {"type": "evolving", "trajectory": [
                    {"session": 0, "value": "王五"}, {"session": 2, "value": "赵六"}]}}},
        ],
        "cascades": [{"trigger": {"entity": "AI工程部", "field": "负责人", "becomes": "李四", "session": 3},
                      "effect": {"entity": "AI工程部", "field": "汇报对象", "set": "CTO"}}],
        "absent_fields": ["季度营收", "P0缺陷率"],   # ★ P0 是矛盾项(实际存在),校验应剔除
    }


def _self_test() -> bool:
    ws = build_demo_world()
    E = "AI工程部"
    checks: list[tuple[bool, str, Any, Any]] = []

    def ck(name, got, want):
        ok = (_norm(got) == _norm(want)) if isinstance(want, str) else (got == want)
        checks.append((ok, name, got, want))

    # IE:某 session 当时值
    ck("IE P0@s0", gt_ie(ws, E, "P0缺陷率", 0), "2.5%")
    ck("IE P0@s3", gt_ie(ws, E, "P0缺陷率", 3), "2.8%")
    ck("IE 负责人@s0", gt_ie(ws, E, "负责人", 0), "张三")
    ck("IE 负责人@s2", gt_ie(ws, E, "负责人", 2), "李四")
    # KU:最新有效值
    ck("KU 负责人", gt_ku(ws, E, "负责人"), "李四")
    ck("KU Oncall", gt_ku(ws, E, "Oncall数"), "9次")
    # MR:极值 + argmax/argmin(★ argmin 是 v8 漏的)
    mx = gt_mr(ws, E, "Oncall数", "max"); ck("MR max Oncall 值", mx["value"], "15次"); ck("MR max argmax", mx["session"], 2)
    mn = gt_mr(ws, E, "Oncall数", "min"); ck("MR min Oncall 值", mn["value"], "9次"); ck("MR min argmin", mn["session"], 4)
    ck("MR min P0 值", gt_mr(ws, E, "P0缺陷率", "min")["value"], "1.8%")
    # TR:变化时机
    ck("TR P0→1.8% 在 s1", gt_tr(ws, E, "P0缺陷率", "1.8%")["session"], 1)
    ck("TR 负责人→李四 在 s2", gt_tr(ws, E, "负责人", "李四")["session"], 2)
    # CONFLICT
    ck("CONFLICT P0 有冲突", gt_conflict(ws, E, "P0缺陷率")["conflict"], True)
    ck("CONFLICT 部门代号 无冲突", gt_conflict(ws, E, "部门代号")["conflict"], False)
    # FORGET:EXPIRE 之后该忘
    ck("FORGET P0 @2-15 已忘", gt_forget(ws, E, "P0缺陷率", "2025-02-15")["forgotten"], True)
    ck("FORGET P0 @1-20 没忘", gt_forget(ws, E, "P0缺陷率", "2025-01-20")["forgotten"], False)
    ck("FORGET P0 @1-20 值", gt_forget(ws, E, "P0缺陷率", "2025-01-20")["value"], "1.8%")
    # ABS:不存在字段
    ck("ABS 季度营收", gt_absent(ws, "季度营收"), INSUFFICIENT)
    ck("ABS P0 存在", gt_absent(ws, "P0缺陷率"), {"present": True})
    # 序列化往返(治可回溯性)
    ck("serialize roundtrip", WorldState.from_dict(ws.to_dict()).timeline(E, "负责人").latest_valid(), "李四")

    # assemble_world:LLM 表 → WorldState(值持续 / EXPIRE / 级联 / absent 矛盾校验)
    ws2, issues = assemble_world(demo_table())
    ck("assemble 2 实体", set(ws2.entities) == {"AI工程部", "数据平台部"}, True)
    ck("assemble n_sessions=5", ws2.n_sessions, 5)
    ck("assemble P0@s1 值持续=2.5%", ws2.timeline("AI工程部", "P0缺陷率").value_at_session(1), "2.5%")
    ck("assemble P0@s2=1.8%", ws2.timeline("AI工程部", "P0缺陷率").value_at_session(2), "1.8%")
    ck("assemble P0 @EXPIRE后 已忘", gt_forget(ws2, "AI工程部", "P0缺陷率", "2025-03-01")["forgotten"], True)
    ck("assemble 级联 汇报对象@s3=CTO", gt_ie(ws2, "AI工程部", "汇报对象", 3), "CTO")
    ck("assemble KU 数据平台部 负责人=赵六", gt_ku(ws2, "数据平台部", "负责人"), "赵六")
    ck("assemble absent 剔除矛盾(P0出),保留季度营收",
       "P0缺陷率" not in ws2.absent_fields and "季度营收" in ws2.absent_fields, True)
    ck("assemble issues 报了 P0 矛盾", any("P0缺陷率" in i for i in issues), True)

    # V11 Tier 0:ORDER / DURATION / pre_expire / 形状
    ev = gt_event_order(ws, E)
    ck("ORDER 事件按时序升序", [e["session"] for e in ev] == sorted(e["session"] for e in ev), True)
    ck("ORDER 首个变更=P0@s1", ev[0]["session"] == 1 and ev[0]["field"] == "P0缺陷率", True)
    ck("DURATION 张三任负责人2周", gt_duration(ws, E, "负责人", "张三")["weeks"], 2)
    ck("pre_expire P0停前=2.8%", gt_pre_expire(ws, E, "P0缺陷率")["value"], "2.8%")
    ck("非单调P0不报单调(2.5→1.8→2.8)", any("P0缺陷率" in i for i in _shape_issues(ws)), False)
    mono = WorldState({"T": {"f": Timeline([Op(0, "2025-01-06", SET, "1"),
                                            Op(1, "2025-01-13", UPDATE, "2", "1"),
                                            Op(2, "2025-01-20", UPDATE, "3", "2")])}}, n_sessions=3)
    ck("单调字段被 flag", len(_shape_issues(mono)) >= 1, True)

    # ── L2 关系多跳:软外键时序图遍历 ──
    rel = WorldState({
        "搜索部": {"负责人": Timeline([Op(0, "2025-01-06", SET, "李娜", None),
                                       Op(3, "2025-01-27", UPDATE, "王强", "李娜")])},
        "李娜":   {"汇报对象": Timeline([Op(0, "2025-01-06", SET, "CTO", None),
                                         Op(2, "2025-01-20", UPDATE, "CVP", "CTO")])},
        "王强":   {"汇报对象": Timeline([Op(0, "2025-01-06", SET, "CEO", None)])},
    }, n_sessions=6)
    P = ["负责人", "汇报对象"]
    ck("L2 2跳@1=CTO(搜索部→李娜→CTO)", gt_multihop(rel, "搜索部", P, 1)["answer"], "CTO")
    ck("L2 2跳@2=CVP(中间实体属性随周变)", gt_multihop(rel, "搜索部", P, 2)["answer"], "CVP")
    ck("L2 2跳@4=CEO(桥实体换人:李娜→王强)", gt_multihop(rel, "搜索部", P, 4)["answer"], "CEO")
    ck("L2 断链字段→INSUFFICIENT", gt_multihop(rel, "搜索部", ["负责人", "查无此字段"], 1)["answer"], INSUFFICIENT)
    ck("L2 悬空外键→INSUFFICIENT", gt_multihop(rel, "搜索部", ["不存在", "汇报对象"], 1)["answer"], INSUFFICIENT)
    mh = gt_multihop(rel, "搜索部", P, 1)
    ck("L2 路径证据2跳", len(mh["path_evidence"]), 2)
    ck("L2 桥实体在证据里(出题须隐藏它)", mh["path_evidence"][0]["value"], "李娜")
    ck("L2 两跳证据落不同周(@2:李娜s0汇报变s2→拼接)", gt_multihop(rel, "搜索部", P, 2)["path_evidence"][1]["set_session"], 2)

    # ── W.3 世界质量 validate():结构化缺陷清单(供 CRITIC 修复轮定向重生成)──
    vd_table = {"entities": [
        {"name": "单调部", "fields": {"指标": {"type": "evolving", "trajectory": [
            {"session": 0, "value": "1"}, {"session": 1, "value": "2"}, {"session": 2, "value": "3"}]}}},
        {"name": "伪演化部", "fields": {"负责人": {"type": "evolving", "trajectory": [
            {"session": 0, "value": "张三"}, {"session": 2, "value": "张三"}]}}},
        {"name": "健康部", "fields": {"指标": {"type": "evolving", "trajectory": [
            {"session": 0, "value": "1"}, {"session": 1, "value": "5"}, {"session": 2, "value": "2"}]}}},
    ]}
    vws, _ = assemble_world(vd_table)
    vt = {(d["entity"], d["type"]) for d in validate(vws, vd_table)}
    ck("validate 抓单调(单调部.指标)", ("单调部", "monotonic") in vt, True)
    ck("validate 抓伪演化(伪演化部.负责人)", ("伪演化部", "fake_evolving") in vt, True)
    ck("validate 放过健康非单调字段(健康部.指标)", ("健康部", "monotonic") in vt, False)

    npass = sum(1 for c in checks if c[0])
    for ok, name, got, want in checks:
        if not ok:
            print(f"  ✗ {name}: got={got!r} want={want!r}")
    print(f"\n[world_state self-test] {npass}/{len(checks)} PASS")
    return npass == len(checks)


if __name__ == "__main__":
    import sys
    sys.exit(0 if _self_test() else 1)
