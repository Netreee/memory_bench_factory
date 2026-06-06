"""
pipeline.lines.L2_relational —— L2 关系多跳产线(单能力线:L2_multihop)。

世界基质 = 软外键 over 状态机(prepare 把人名字段值提升为人员实体,从 run_factory 搬来);
点菜 = 数据驱动发现 2 跳路径 + 枚举唯一可解、优先跨周的实例;gt = 时序图遍历(gt_multihop)。
桥实体在 aux,出题须隐藏(防单文档抄近路)。
"""
from __future__ import annotations

from pipeline.lines.base import ProductionLine, Order, field_kind, interrogative
from pipeline.world_state import (
    WorldState, Timeline, Op, _date_of, week_label, _norm,
    SET, UPDATE, INVALID, INSUFFICIENT, gt_multihop,
)


# ════════════════════════════════════════════════════════════════════════════
# 点菜枚举(从 order_gen.enumerate_l2_orders / discover_fk_paths 逐字搬迁,行为不变)
# ════════════════════════════════════════════════════════════════════════════
def enumerate_l2_orders(ws: WorldState, paths, at_weeks=None, max_per_path: int = 8) -> list[Order]:
    """★L2 路径枚举:对每条软外键 field-path,为每个合法起点找一个【唯一可解、优先跨周】的 2 跳实例。
    paths:[["负责人","汇报对象"], …](来自白皮书 relation_schema)。我们的字段单值 → 解天然唯一。
    返回 L2_relational 订单(gt=终点答案;aux 带 path/at_week/bridge/cross_week/hops)。桥实体在 aux,出题须隐藏。"""
    out: list[Order] = []
    weeks = list(at_weeks) if at_weeks is not None else list(range(ws.n_sessions or 0))
    for path in paths:
        if len(path) < 2:
            continue
        starts = [e for e, flds in ws.entities.items() if path[0] in flds]   # 有首字段=合法起点
        seen, picked = set(), 0
        for start in starts:
            if picked >= max_per_path:
                break
            best = None                                  # (w, mh, cross);优先 cross_week=True
            for w in weeks:
                mh = gt_multihop(ws, start, path, w)
                if mh.get("answer") in (INSUFFICIENT, INVALID, None):
                    continue
                ev = mh.get("path_evidence", [])
                if len(ev) < 2:                          # 必须真 2 跳(中间桥实体可解)
                    continue
                cross = ev[0]["set_session"] != ev[-1]["set_session"]
                if best is None or (cross and not best[2]):
                    best = (w, mh, cross)
                if cross:
                    break
            if best is None:
                continue
            w, mh, cross = best
            ev = mh["path_evidence"]
            key = (start, tuple(path), mh["answer"])
            if key in seen:
                continue
            seen.add(key)
            out.append(Order("L2_relational", start, "→".join(path), gt=mh["answer"],
                             evidence_sessions=sorted({e["set_session"] for e in ev}),
                             question_date=_date_of(w),
                             aux={"path": path, "at_week": w, "bridge": ev[0]["value"],
                                  "cross_week": cross, "hops": len(path)}))
            picked += 1
    return out


def discover_fk_paths(ws: WorldState, hops: int = 2) -> list[list[str]]:
    """★数据驱动发现软外键路径(不依赖松散的关系描述,直接看世界):
    某字段任一取值若是另一实体的名 = 该字段是一条边。返回去重的 hops 跳【字段路径模板】。"""
    keys = set(ws.entities)

    def fk_targets(ent, fld):                       # 该 (实体,字段) 指向的实体集合
        tl = ws.entities.get(ent, {}).get(fld)
        return {v for (_s, _d, v) in tl.set_values() if v in keys} if tl else set()

    def fk_fields(ent):
        return [f for f in ws.entities.get(ent, {}) if fk_targets(ent, f)]

    patterns = set()
    for x in ws.entities:
        for f1 in fk_fields(x):
            for y in fk_targets(x, f1):
                for f2 in ws.entities.get(y, {}):   # hop2:桥实体 y 的任意字段(末跳=答案)
                    if hops == 2:
                        patterns.add((f1, f2))
    return [list(p) for p in patterns]


# ════════════════════════════════════════════════════════════════════════════
# L2 产线
# ════════════════════════════════════════════════════════════════════════════
class RelationalLine(ProductionLine):
    id = "L2_relational"
    title = "关系多跳"
    memory = "跨实体 N 跳遍历与聚合(A→B→C)"
    gt_substrate = "软外键 + 时序图遍历(gt_multihop)"
    implemented = True
    requires: list[str] = ["person_fields>=2"]   # 需 ≥2 个 person 字段(FK 链:首字段→人员→下一跳字段)

    def feasible(self, ws, profile: dict) -> tuple[bool, str]:
        """数 profile.field_schema 里 kind==person 的字段;<2 则 prepare 必 no-op、产 0 单。
        判定口径与 prepare 完全一致(prepare 内同样要求 len(person_fields)>=2)。"""
        pf = [f["name"] for f in (profile.get("field_schema") or []) if f.get("kind") == "person"]
        if len(pf) >= 2:
            return True, f"person 字段 {len(pf)} 个(「{pf[0]}」→人员→「{pf[1]}」可成链)"
        return False, f"person 字段仅 {len(pf)} 个(<2),无法造软外键链"

    def prepare(self, ws, profile: dict):
        """★世界基质:把【人名类字段】的值提升为【人员实体】,并给人员一个【下一跳字段】(时变,制造跨周2跳)。
        引用完整性由代码保证:dept.负责人 的值 = 真实存在的人员实体名。≥2 个 person 字段才构成链。
        (从 run_factory._augment_relations 搬来;归位到 L2 → 基质按产线激活备料。)"""
        pf = [f["name"] for f in (profile.get("field_schema") or []) if f.get("kind") == "person"]
        if len(pf) < 2:
            return None
        fk_field, next_field = pf[0], pf[1]            # 负责人(dept→person FK) / 汇报对象(person→上级人员)
        from pipeline.world_state import _strip_disambig
        persons, seen_base = [], {_strip_disambig(e) for e in ws.entities}   # ★Fix3:人名也防表面塌缩(主干已被实体/已收人名占用 → 跳)
        for ent, flds in list(ws.entities.items()):
            tl = flds.get(fk_field)
            if not tl:
                continue
            for (_s, _d, v) in tl.set_values():
                v = str(v)
                if v and v not in ws.entities and _strip_disambig(v) not in seen_base:
                    persons.append(v); seen_base.add(_strip_disambig(v))
        persons = sorted(set(persons))
        if len(persons) < 2:                          # 造不出"人→上级人"的链(需 ≥2 个不同人名)
            return None
        # ★下一跳值 = 另一个【真实人员名】,不再写死 CTO/CEO 职级(审计 ★2,根因修复):
        #   ① 域无关(medical 的「会诊上级」= 医生名而非 CTO);② 消别名漂移(gold=名、语料也渲名,不再 CEO↔刘总);
        #   ③ 答案空间多样(不再挤在 5 个 CXO token、防瞎猜)。取前 1/3 作"上级层",余者报给某上级,时变制造跨周 2 跳。
        seniors = persons[:max(1, len(persons) // 3)]
        n, ch = (ws.n_sessions or 10), max(1, (ws.n_sessions or 10) // 2)
        for i, p in enumerate(persons):
            cands = [s for s in seniors if s != p] or [x for x in persons if x != p]
            m1, m2 = cands[i % len(cands)], cands[(i + 1) % len(cands)]
            ops = [Op(0, _date_of(0), SET, m1, None)]
            if m2 != m1:                               # 中段换上级 → 2跳证据落不同周(cross_week)
                ops.append(Op(ch, _date_of(ch), UPDATE, m2, m1))
            ws.entities[p] = {next_field: Timeline(ops)}
        return f"  ★关系增强:+{len(persons)} 人员实体(「{next_field}」=真实人员名·时变),软外键链 「{fk_field}」→人员→「{next_field}」打通"

    def enumerate(self, ws, target: int = 200, wp=None) -> list[dict]:
        paths = discover_fk_paths(ws, hops=2)
        profile = (wp or {}).get("domain_profile", {})
        out = []
        for o in enumerate_l2_orders(ws, paths)[:target]:
            aux = dict(o.aux)
            last = (aux.get("path") or [None])[-1]          # ★Fix2:末跳字段的 kind 决定疑问词(样本值=gt 答案)
            aux.setdefault("ans_kind", field_kind(last, o.gt, profile))
            out.append({"line": self.id, "capability": "L2_multihop", "entity": o.entity,
                        "field": o.field, "gt": o.gt, "evidence_sessions": o.evidence_sessions, "aux": aux})
        return out

    def gt(self, ws, o: dict):
        """护城河:时序软外键图遍历,与 enumerate 烘焙逐字段相等(见自检校验闸)。"""
        aux = o.get("aux") or {}
        return gt_multihop(ws, o["entity"], aux.get("path"), aux.get("at_week"))["answer"]

    def intent(self, o: dict) -> tuple[str, list]:
        ent, fld, aux, gt = o.get("entity", ""), o.get("field", ""), o.get("aux", {}) or {}, o.get("gt")
        p = (aux.get("path") or [fld, ""]) + ["", ""]
        aw = aux.get("at_week")
        # ★§V-A 良定义:时变关系链,题面必须带【周锚】——否则"向谁汇报"逐周多值、gold 不唯一(run0604 实证)。
        #   并明确"那个人本人"(消"部门汇报 vs 负责人汇报"二义)。
        q = interrogative(aux.get("ans_kind"))     # ★Fix2:末跳疑问词由 path[-1] 的 kind 派生(治"管理跨度是谁")
        wk = f"截至第{week_label(aw)}周(以那一周的状态为准)," if aw is not None else ""
        s = (f"{wk}沿一条两步关系链提问:从【{ent}】出发,先找它的「{p[0]}」**所指的那个人**,"
             f"再问【那个人本人】的「{p[1]}」{q}。"
             f"★必须点明'第{week_label(aw)}周'这个时点(时变关系,不带周次答案不唯一);"
             f"只给起点【{ent}】和「{p[0]}→{p[1]}」关系;【绝不点名中间那个人】;答案也不能出现。")
        hide = [str(gt)]
        if aux.get("bridge"):
            hide.append(str(aux["bridge"]))            # L2 桥实体必须隐藏
        return s, hide


    def ground(self, order, evidence_docs, all_signal_text=""):
        """接地(§G.5):末跳答案(gt 纯串)就近【桥实体 aux.bridge】;comparison 题无桥则退化为起点实体。"""
        from pipeline.grounding import gold_scalar, attributed
        s = gold_scalar(order)
        if not s:
            return ("drop", "fail-closed:L2 无待验标量")
        aux = order.get("aux") or {}
        anchor = aux.get("bridge") or order.get("entity", "")
        if attributed(s, anchor, [d["content"] for d in evidence_docs]):
            return ("grounded", f"末值 '{s}' 就近桥实体「{anchor}」")
        return ("drop", f"末值 '{s}' 未就近桥实体「{anchor}」(别名悬空/未渲)")

    def well_posed(self, order: dict, ws) -> tuple:
        """★边 A 闸(题面↔答案【良定义】),与 ground() 对称。设计见 docs/anchors/edge_a/L2_well_posed.md(已 QA 版)。
        断言:order 声明的坐标 (start, path, W) 在世界里【唯一锁死】gold。纯代码、确定性、零 LLM、绝不碰 corpus。
        落地 INV-0/1/2/3(INV-4 桥唯一、INV-5 起点自带末跳 均经实测证伪【撤下】,见下与设计 §6)。"""
        aux = order.get("aux") or {}
        start = order.get("entity", "")
        path = aux.get("path")
        W = aux.get("at_week")
        gold = order.get("gt")

        # ── INV-0 结构前置(path≥2 / hops 自洽 / gold 非空非哨兵)──
        if not isinstance(path, list) or len(path) < 2:
            return ("drop", "ill-formed:path<2 跳(L2 必须真多跳)")
        if aux.get("hops") not in (None, len(path)):
            return ("drop", f"ill-formed:hops({aux.get('hops')})≠path 长({len(path)})")
        if not (isinstance(gold, str) and gold.strip()) or gold in (INSUFFICIENT, INVALID):
            return ("drop", "ill-formed:gold 空/INSUFFICIENT(L2 必须有唯一答案)")

        # ── INV-1 必须带唯一周锚(治"无周锚→横跳多解")──
        weeks = set(ws.sessions())
        if W is None or not isinstance(W, int) or W not in weeks:
            return ("drop", "无周锚:时变关系链不带合法 at_week,逐周多值 gold 不唯一")

        # ── INV-5 撤下(实测 16/16 误杀,connectivity≠ambiguity 同 roles_of)──
        #   题面"X部门负责人的汇报对象"自然解析唯一(=那个负责人的汇报对象,非部门自己的);
        #   起点同名末跳字段不使【本题】二义——要二义得问"X部门的汇报对象"(不带"负责人"=另一道题)。
        #   §V-A 已强制题面带全链("负责人的…"),故"起点自带末跳字段"不构成边 A 病。详见设计 §2 INV-5 + §6。

        # ── INV-2 + INV-3:在【单一 W】重算全链、核 gold(命门2:复用 gt_multihop,与产线 gt() 同口径)──
        mh = gt_multihop(ws, start, path, W)
        if mh.get("answer") in (INSUFFICIENT, INVALID, None) \
                or len(mh.get("path_evidence", [])) < len(path):
            return ("drop", f"链在第{W}周断裂于 {mh.get('broke_at')}(该周此关系不成立)")
        if _norm(mh["answer"]) != _norm(gold):
            return ("drop", f"gold 与世界@第{W}周重算不符:世界='{mh['answer']}' gold='{gold}'(查无实据/锚不统一)")

        # ── INV-4 撤下:桥指代唯一已由【名键结构(无同名实体)+ INV-2 链不断 + INV-3 唯一重算】共同保证 ──
        return ("well_posed", "")


# 自检:python -m pipeline.lines.L2_relational
if __name__ == "__main__":
    import sys
    from pipeline.world_state import assemble_world
    line = RelationalLine()
    checks: list[tuple[bool, str]] = []

    def ck(name, cond):
        checks.append((bool(cond), name))

    rel_table = {"entities": [
        {"name": "搜索部", "fields": {"负责人": {"type": "evolving",
            "trajectory": [{"session": 0, "value": "李娜"}, {"session": 3, "value": "王强"}]}}},
        {"name": "推荐部", "fields": {"负责人": {"type": "stable", "value": "周明"}}},
        {"name": "李娜", "fields": {"汇报对象": {"type": "evolving",
            "trajectory": [{"session": 0, "value": "CTO"}, {"session": 2, "value": "CVP"}]}}},
        {"name": "王强", "fields": {"汇报对象": {"type": "stable", "value": "CEO"}}},
        {"name": "周明", "fields": {"汇报对象": {"type": "stable", "value": "CTO"}}},
    ]}
    relws, _ = assemble_world(rel_table)
    paths = discover_fk_paths(relws)
    l2 = enumerate_l2_orders(relws, [["负责人", "汇报对象"]])
    ck("软外键路径发现含 [负责人,汇报对象]", ["负责人", "汇报对象"] in paths)
    ck("L2 枚举出≥2 订单", len(l2) >= 2)
    ck("L2 全是 2 跳关系题", all(o.capability == "L2_relational" and o.aux.get("hops") == 2 for o in l2))
    ck("L2 答案非空/非 INSUFFICIENT", all(o.gt not in (INSUFFICIENT, INVALID, None) for o in l2))
    ck("L2 aux 带 bridge(桥实体)", all(o.aux.get("bridge") for o in l2))
    ck("L2 至少一题跨周拼接(证据≥2周)", any(len(o.evidence_sessions) >= 2 for o in l2))
    ck("L2 桥实体≠答案(真2跳,非单查)", all(o.aux.get("bridge") != o.gt for o in l2))

    # ★护城河校验闸:gt() 重算 == enumerate 烘焙
    orders = line.enumerate(relws)
    ck("校验闸:gt() 重算 == 烘焙", all(line.gt(relws, o) == o["gt"] for o in orders))
    # ★§V-A 良定义:时变多跳题,题面必须带周锚(否则 gold 不唯一)
    ck("§V-A:L2 题面带周锚(单一真源 week_label=s+1)",
       all(f"第{week_label(o['aux'].get('at_week'))}周" in line.intent(o)[0] for o in orders if o["aux"].get("at_week") is not None))
    # ★Fix2:末跳疑问词由 path[-1] 的 kind 派生(治"管理跨度是谁")
    def _l2q(kind):
        return line.intent({"line": "L2_relational", "capability": "L2_multihop", "entity": "X", "field": "f",
                            "gt": "v", "aux": {"path": ["负责人", "末跳"], "at_week": 0, "ans_kind": kind}})[0]
    ck("Fix2:L2 末跳 number → '是多少'且非'是谁'", "是多少" in _l2q("number") and "是谁" not in _l2q("number"))
    ck("Fix2:L2 末跳 person → '是谁'", "是谁" in _l2q("person"))

    # prepare:office 型 profile(2 person 字段)应增强出人员实体
    base_table = {"entities": [
        {"name": "支付部", "fields": {"负责人": {"type": "evolving",
            "trajectory": [{"session": 0, "value": "陈一"}, {"session": 2, "value": "孙二"}]}}},
    ]}
    bws, _ = assemble_world(base_table)
    note = line.prepare(bws, {"field_schema": [{"name": "负责人", "kind": "person"}, {"name": "汇报对象", "kind": "person"}]})
    ck("prepare 增强出人员实体(陈一/孙二)", "陈一" in bws.entities and "孙二" in bws.entities)
    ck("prepare 人员带【汇报对象】字段", all("汇报对象" in bws.entities[p] for p in ["陈一", "孙二"]))
    ck("prepare <2 person 字段时 no-op", line.prepare(bws, {"field_schema": [{"name": "负责人", "kind": "person"}]}) is None)

    # ════════════════════════════════════════════════════════════════════════
    # ★边 A 闸 well_posed() 自检(设计 §7:well-posed A/B + ill-posed C–H)
    #   造小世界直接拼 Op/Timeline,手搓 order,断言返回值 + reason 命中对应不变量。
    # ════════════════════════════════════════════════════════════════════════
    def WL(*steps):  # (session, op, value, prev) → Timeline
        return Timeline([Op(s, _date_of(s), op, v, p) for (s, op, v, p) in steps])

    def od(entity, path, at_w, gt, bridge=None, hops=2):
        return {"line": "L2_relational", "capability": "L2_multihop", "entity": entity,
                "field": "→".join(path), "gt": gt, "evidence_sessions": [0],
                "aux": {"path": path, "at_week": at_w, "bridge": bridge, "hops": hops}}

    P = ["负责人", "汇报对象"]
    # 公共小世界:部门→负责人→人员→汇报对象;负责人 w3 换人(李娜→王强)。
    # 逐周真值(gt_multihop 实算):w0–2→CTO(经李娜),w3–5→CEO(经王强);李娜 w5 改值在桥换人后休眠。
    base = WorldState({
        "搜索部": {"负责人": WL((0, SET, "李娜", None), (3, UPDATE, "王强", "李娜"))},
        "李娜":   {"汇报对象": WL((0, SET, "CTO", None), (5, UPDATE, "CVP", "CTO"))},
        "王强":   {"汇报对象": WL((0, SET, "CEO", None))},
    }, n_sessions=6)

    # ── well-posed(应 PASS)──
    ck("[wp] A 锚w0 链通·gold=重算·桥唯一 → well_posed",
       line.well_posed(od("搜索部", P, 0, "CTO", "李娜"), base)[0] == "well_posed")
    ck("[wp] B 锚w5 桥已换王强→CEO(单一W一致)→ well_posed",
       line.well_posed(od("搜索部", P, 5, "CEO", "王强"), base)[0] == "well_posed")

    # ── ill-posed(应 drop,且 reason 命中对应不变量)──
    rC = line.well_posed(od("搜索部", P, None, "CTO", "李娜"), base)
    ck("[wp] C 无周锚(Q02/03/05)→ INV-1 drop", rC[0] == "drop" and "无周锚" in rC[1])
    rD = line.well_posed(od("搜索部", P, 0, "COO", "李娜"), base)   # 世界只有 CTO
    ck("[wp] D gold查无实据(Q33 COO)→ INV-3 drop", rD[0] == "drop" and "查无实据" in rD[1])
    rE = line.well_posed(od("搜索部", P, 5, "CTO", "李娜"), base)   # 声明 W=5 却填 w0 旧值
    ck("[wp] E 混锚(Q32:W5 填 w0 旧值)→ INV-3 drop", rE[0] == "drop" and "查无实据" in rE[1])

    # F) 桥"一人多身份"【不再误杀】(原 INV-4 反例):赵一既数据部负责人、又新人甲导师 FK 值
    reuse = WorldState({
        "数据部": {"负责人": WL((0, SET, "赵一", None))},
        "新人甲": {"导师":   WL((0, SET, "赵一", None))},   # 赵一第 2 种 FK 身份(roles=2)
        "赵一":   {"汇报对象": WL((0, SET, "COO", None))},
    }, n_sessions=2)
    ck("[wp] F1 多角色但 gold 对 → 放行(撤 roles_of 不误杀)",
       line.well_posed(od("数据部", P, 0, "COO", "赵一"), reuse)[0] == "well_posed")
    rF2 = line.well_posed(od("数据部", P, 0, "CTO", "赵一"), reuse)  # 同世界但 gold 错
    ck("[wp] F2 多角色 gold 错 → INV-3 drop(Q27 真缺陷在此边)",
       rF2[0] == "drop" and "查无实据" in rF2[1])

    # G) 起点自带末跳字段【不再误杀】(INV-5 已撤):题面"前端部负责人的汇报对象"自然解析唯一(=张三的=CFO),
    #    虽然前端部自己也有汇报对象(李四),但那要问"前端部的汇报对象"=另一道题 → 本题良定义,必须【放行】。
    dual = WorldState({
        "前端部": {"负责人": WL((0, SET, "张三", None)), "汇报对象": WL((0, SET, "李四", None))},  # 部门自带汇报对象=李四
        "张三":   {"汇报对象": WL((0, SET, "CFO", None))},
    }, n_sessions=2)
    rG = line.well_posed(od("前端部", P, 0, "CFO", "张三"), dual)
    ck("[wp] G 起点自带末跳字段 + 题面带'负责人的' → 良定义放行(INV-5 撤,不再误杀)", rG[0] == "well_posed")

    # H) 链断(末跳实体在 W 无该字段)→ INV-2
    broke = WorldState({"销售部": {"负责人": WL((0, SET, "孤儿", None))}}, n_sessions=2)
    rH = line.well_posed(od("销售部", P, 0, "X", "孤儿"), broke)
    ck("[wp] H 链断(孤儿无汇报对象)→ INV-2 drop", rH[0] == "drop" and "断裂" in rH[1])

    npass = sum(1 for ok, _ in checks if ok)
    for ok, name in checks:
        if not ok:
            print(f"  ✗ {name}")
    print(f"[L2_relational self-test] {npass}/{len(checks)} PASS")
    sys.exit(0 if npass == len(checks) else 1)
