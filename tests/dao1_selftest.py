"""刀1 离线自检 —— 锁住 014559 纵览后的六个修复(零 LLM;渲染回路用 scripted tracer)。

覆盖:① render 禁词字段名豁免(「累计」∈「累计计费工时」不再杀 doc)② render 耗尽 fail-loud 弃段
③ _affix_units 单位真源化(裸数补单位/幂等/prev同步/异型不动)④ imprint 后缀保真(万元/% 回贴,混后缀跳过)
⑤ validate illegal_transition(声明字段倒流/出界抓到,未声明往复不碰)⑥ _canonicalize_lines(自编名锁回+丢线补漏+axis回填)
⑦ L4 _choice_field 叠词去重。

跑:./venv/bin/python tests/dao1_selftest.py
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.world_state import WorldState, Timeline, Op, SET, UPDATE, EXPIRE, _date_of, validate
from pipeline.world_gen import _affix_units, imprint_structure
from pipeline.render import render_corpus
from pipeline.central_office import _canonicalize_lines
from pipeline.lines.L4_preference import PreferenceLine

checks: list[tuple[bool, str]] = []
def ck(name, cond): checks.append((bool(cond), name))


def _tl(*sv):                            # sv: (session, value);自动 SET/UPDATE + prev 链
    ops, prev = [], None
    for (s, v) in sv:
        ops.append(Op(s, _date_of(s), SET if prev is None else UPDATE, v, prev)); prev = v
    return Timeline(ops)


# ════════ ① + ② 渲染回路(scripted tracer,零 LLM)════════
class _ScriptedTracer:
    """按脚本回 docs。只对 render.signal 出脚本+记历史(filler/conflict 等其它调用回空,免互相吃响应)。"""
    def __init__(self, script):
        self.script = list(script); self.calls = []
    def chat_json(self, tag, messages, **kw):
        if tag == "render.discriminate":
            text = messages[-1]["content"]
            for value in ("86小时", "维持治疗"):
                if value in text:
                    return {"answer": value}
            return {"answer": "不确定"}
        if tag != "render.signal":
            return {"docs": []}
        self.calls.append(messages[-1]["content"])
        return self.script.pop(0) if self.script else {"docs": []}


def _mini_ws():
    return WorldState({"鼎晟案": {"累计计费工时": _tl((0, "86小时"))}}, n_sessions=1)


def _run_render(tracer):
    ws = _mini_ws()
    corpus = {"sessions": []}
    render_corpus({"domain_profile": {"doc_genres": ["纪要"], "stopped_phrase": "停止计费"}},
                  ws, 0, tracer, corpus, set(), save_cb=lambda: None, log=lambda *a: None)
    return corpus["sessions"][0]["docs"] if corpus["sessions"] else []

# ①:doc 含字段名「累计计费工时」(含禁词「累计」)+ 事实就近 → 必须【一次过】,不被禁词杀
good_doc = {"title": "周度纪要", "type": "纪要", "content": "鼎晟案本期累计计费工时为86小时,推进正常。"}
t1 = _ScriptedTracer([{"docs": [good_doc]}])
docs1 = _run_render(t1)
ck("①豁免:字段名含禁词的合格 doc 一次过(不再静默杀)", len(t1.calls) == 1 and any("86小时" in d["content"] for d in docs1))
ck("①无兜底备忘混入", not any(d.get("is_fallback") for d in docs1))

# ①b:doc 真犯禁(正文用「目前」)→ 重渲,且 hint 必须【如实】说"因全局口径词被废",不再谎报"没写"
bad_doc = {"title": "周度纪要", "type": "纪要", "content": "鼎晟案本期累计计费工时为86小时,目前整体平稳。"}
t2 = _ScriptedTracer([{"docs": [bad_doc]}, {"docs": [good_doc]}])
docs2 = _run_render(t2)
ck("①b 真犯禁 → 第2轮重渲成功", len(t2.calls) == 2 and any("86小时" in d["content"] for d in docs2))
ck("①b hint 如实报死因(含'全局口径词'与『目前』)", "全局口径词" in t2.calls[1] and "目前" in t2.calls[1])

# ②:4 轮全失败 → fail-loud 弃段，不再注入能绕过接地闸的 K=V 模板备忘
t3 = _ScriptedTracer([{"docs": []}] * 4)
docs3 = _run_render(t3)
fb = [d for d in docs3 if d.get("is_fallback")]
ck("②耗尽 fail-loud:4 轮失败后弃段", len(t3.calls) == 4 and docs3 == [])
ck("②弃段不注 K=V 兜底备忘", not fb)

# ════════ ③ _affix_units ════════
ws3 = WorldState({"星耀案": {"争议标的额": _tl((0, "200"), (2, "250")),
                            "风险评分": _tl((0, "4")),
                            "异型": _tl((0, "1.2亿"))}}, n_sessions=4)
prof3 = {"field_schema": [{"name": "争议标的额", "kind": "numeric", "unit": "万元"},
                          {"name": "异型", "kind": "numeric", "unit": "万元"}]}   # 风险评分无 unit
n = _affix_units(ws3, prof3, log=lambda *a: None)
vals = [v for (_s, _d, v) in ws3.entities["星耀案"]["争议标的额"].set_values()]
ck("③裸数补单位", vals == ["200万元", "250万元"])
ck("③prev 同步补", ws3.entities["星耀案"]["争议标的额"].ops[1].prev == "200万元")
ck("③无 unit 字段不动", [v for (_s, _d, v) in ws3.entities["星耀案"]["风险评分"].set_values()] == ["4"])
ck("③异型写法('1.2亿')不动", [v for (_s, _d, v) in ws3.entities["星耀案"]["异型"].set_values()] == ["1.2亿"])
n2 = _affix_units(ws3, prof3, log=lambda *a: None)
ck("③幂等(二次 0 改动)", n2 == 0)

# ════════ ④ imprint 后缀保真 ════════
ws4 = WorldState({
    "甲案": {"标的额": _tl((0, "300万元"), (1, "520万元"), (2, "180万元"), (3, "470万元"), (4, "260万元"), (5, "390万元"))},
    "乙部": {"缺陷率": _tl((0, "2.5%"), (1, "4.1%"), (2, "1.8%"), (3, "3.9%"), (4, "2.2%"), (5, "3.3%"))},
    "混乱": {"字段": _tl((0, "100万元"), (1, "200"), (2, "300万元"), (3, "400"), (4, "500万元"), (5, "600"))},
}, n_sessions=6)
imprint_structure(ws4, log=lambda *a: None, profile={"l7_max_trends": 10}, seed=1)
v_a = [v for (_s, _d, v) in ws4.entities["甲案"]["标的额"].set_values()]
v_b = [v for (_s, _d, v) in ws4.entities["乙部"]["缺陷率"].set_values()]
v_c = [v for (_s, _d, v) in ws4.entities["混乱"]["字段"].set_values()]
ck("④万元后缀回贴(全部带'万元')", all(v.endswith("万元") for v in v_a))
ck("④%后缀回贴(全部带'%')", all(v.endswith("%") for v in v_b))
ck("④混后缀字段跳过(原样不动)", v_c == ["100万元", "200", "300万元", "400", "500万元", "600"])
# ★防 no-op 假绿(审计:原始值本就带后缀,endswith 断言对'imprint 静默没触发'零检出力)→ 验值序确被改写成
#   【L7 分类器认可的趋势】(形状无关:首末净方向清晰 + ∃局部反向);imprint 现轮转 end_reversal/mid_dip/late_surge
from pipeline.world_state import _to_num as _tn
from pipeline.lines.L7_consolidation import _trend_label as _tlbl, _anti_recency as _tar
for nm, seq in (("甲案", [_tn(v) for v in v_a]), ("乙部", [_tn(v) for v in v_b])):
    lab = _tlbl(seq)
    ck(f"④{nm} 确被改写成可裁趋势(首末净方向清晰+∃局部反向,非 no-op,形状无关)",
       lab in ("上升", "下降") and _tar(seq, lab))

# ════════ ⑤ validate illegal_transition ════════
ws5 = WorldState({
    "天成案": {"案件状态": _tl((0, "诉讼中"), (3, "执行中"), (6, "诉讼中"))},     # 倒流
    "出界案": {"案件状态": _tl((0, "立案"), (2, "调解中"))},                      # 出界(不在表)
    "合规案": {"案件状态": _tl((0, "立案"), (2, "诉讼中"), (5, "结案"))},          # 跳级 OK
    "往复者": {"随访方式": _tl((0, "门诊"), (1, "电话"), (2, "门诊"))},            # 未声明,往复合法
}, n_sessions=8)
prof5 = {"state_machines": [{"field": "案件状态", "states": ["立案", "诉讼中", "执行中", "结案"]}]}
defects = validate(ws5, None, prof5)
kinds = {(d["entity"], d["type"]) for d in defects}
ck("⑤倒流抓到", ("天成案", "illegal_transition") in kinds)
ck("⑤出界抓到", ("出界案", "illegal_transition") in kinds)
ck("⑤跳级合法放行", ("合规案", "illegal_transition") not in kinds)
ck("⑤未声明字段往复不碰(opt-in)", not any(e == "往复者" for (e, _t) in kinds))
ck("⑤无 profile 完全跳过", not any(d["type"] == "illegal_transition" for d in validate(ws5, None, None)))

# ════════ ⑥ _canonicalize_lines ════════
draft = {"active_lines": [{"line": "L1_timeline", "weight": 0.5}, {"line": "L4_preference", "weight": 0.8},
                          {"line": "L7_consolidation", "weight": 0.6}],
         "domain_profile": {"preference_axis": {"field": "随访方式", "options": ["门诊", "电话"]},
                            "state_machines": [{"field": "案件状态", "states": ["立案", "结案"]}]}}
wp6 = {"active_lines": [{"line": "L1_temporal_state", "weight": 0.4},      # 自编名 → 锁回 L1
                        {"line": "L4_source_conflict", "weight": 0.9},     # 自编名 → 前缀锁回 L4
                        {"line": "L9_unknown", "weight": 0.1}],            # 不可识别 → 丢
       "domain_profile": {}}                                               # axis/states 被 critic 丢 → 回填
_canonicalize_lines(wp6, draft, log=lambda *a: None)
ids6 = [l["line"] for l in wp6["active_lines"]]
ck("⑥自编名锁回 canonical", "L1_timeline" in ids6 and "L4_preference" in ids6)
ck("⑥丢线补漏(L7 回来)", "L7_consolidation" in ids6)
ck("⑥L9 前缀锁回已注册产线 + 无重复", "L9_unknown" not in str(ids6)
   and "L9_induction" in ids6 and len(ids6) == len(set(ids6)) == 4)
ck("⑥axis/states 回填", wp6["domain_profile"].get("preference_axis") and wp6["domain_profile"].get("state_machines"))
# ⑥c【value_shape 审计 HIGH】critic 删字段级约束 → _canonicalize_lines 从 draft 兜底恢复
draft_vs = {"active_lines": [{"line": "L1_timeline", "weight": 0.5}],
            "domain_profile": {"field_schema": [{"name": "累计工时", "kind": "numeric", "monotonic": "up"},
                                                 {"name": "标的额", "kind": "numeric", "unit": "万"},
                                                 {"name": "评分", "kind": "numeric", "range": [0, 10]}]}}
wp_vs = {"active_lines": [{"line": "L1_timeline", "weight": 0.5}],
         "domain_profile": {"field_schema": [{"name": "累计工时", "kind": "numeric"},        # critic 把 monotonic 删了
                                             {"name": "标的额", "kind": "numeric"},          # unit 删了
                                             {"name": "评分", "kind": "numeric", "range": [0, 10]}]}}  # 这个保住了
_canonicalize_lines(wp_vs, draft_vs, log=lambda *a: None)
fs_vs = {f["name"]: f for f in wp_vs["domain_profile"]["field_schema"]}
ck("⑥c critic 删 monotonic → 从 draft 兜底恢复", fs_vs["累计工时"].get("monotonic") == "up")
ck("⑥c critic 删 unit → 兜底恢复", fs_vs["标的额"].get("unit") == "万")
ck("⑥c critic 保留的 range 不被改动", fs_vs["评分"].get("range") == [0, 10])
# ⑥b 域条件保持:draft 没有的线绝不被塞回(office 无 L4 的情形)
draft_no4 = {"active_lines": [{"line": "L1_timeline", "weight": 0.5}], "domain_profile": {}}
wp6b = {"active_lines": [{"line": "L1_timeline", "weight": 0.5}], "domain_profile": {}}
_canonicalize_lines(wp6b, draft_no4, log=lambda *a: None)
ck("⑥b 域条件:draft 无 L4 → 不塞回", all(l["line"] != "L4_preference" for l in wp6b["active_lines"]))

# ════════ ⑧ 审计修复回归(刀1 diff 对抗审计点名)════════
# ⑧a【高】_trended_fields 过序列化往返(否则闭环 augment 复注翻向)
ws8 = WorldState.from_dict(ws4.to_dict())
ck("⑧a _trended_fields 过 to_dict/from_dict 往返", set(getattr(ws8, "_trended_fields", [])) == set(ws4._trended_fields)
   and len(ws8._trended_fields) >= 2)
# ⑧b【中】豁免集含【值】:值含禁词(「维持治疗」含「维持」)的合格 doc 必须一次过
ws8b = WorldState({"林深": {"用药方案": _tl((0, "维持治疗"))}}, n_sessions=1)
t8 = _ScriptedTracer([{"docs": [{"title": "门诊记录", "type": "记录", "content": "林深本期用药方案为维持治疗。"}]}])
corpus8 = {"sessions": []}
render_corpus({"domain_profile": {"doc_genres": ["记录"], "stopped_phrase": "停止监测"}},
              ws8b, 0, t8, corpus8, set(), save_cb=lambda: None, log=lambda *a: None)
docs8 = corpus8["sessions"][0]["docs"] if corpus8["sessions"] else []
ck("⑧b 值含禁词的合格 doc 一次过(豁免集含值)", len(t8.calls) == 1
   and any("维持治疗" in d["content"] for d in docs8) and not any(d.get("is_fallback") for d in docs8))
# ⑧c【低】纯数字 unit 拒绝(防 ×10 跑飞)
ws8c = WorldState({"甲": {"x": _tl((0, "200"))}}, n_sessions=2)
_affix_units(ws8c, {"field_schema": [{"name": "x", "kind": "numeric", "unit": "0"}]}, log=lambda *a: None)
ck("⑧c 纯数字 unit 被拒(值不动)", [v for (_s, _d, v) in ws8c.entities["甲"]["x"].set_values()] == ["200"])
# ⑧d【中】sm 字段豁免 monotonic(数值可解析状态表不再与 illegal_transition 乒乓)
ws8d = WorldState({"乙": {"阶段": _tl((0, "阶段1"), (2, "阶段2"), (5, "阶段3"))}}, n_sessions=6)
prof8d = {"state_machines": [{"field": "阶段", "states": ["阶段1", "阶段2", "阶段3"]}]}
d8 = validate(ws8d, None, prof8d)
ck("⑧d 合法单向数值状态表:零缺陷(无 monotonic 乒乓)", d8 == [])
# ⑧e【低】states 声明含重复 → 整条作废(不误判倒流)
prof8e = {"state_machines": [{"field": "阶段", "states": ["a", "b", "a"]}]}
ws8e = WorldState({"丙": {"阶段": _tl((0, "a"), (1, "b"), (2, "a"))}}, n_sessions=3)
ck("⑧e 塌缩声明被跳过(零误判)", not any(d["type"] == "illegal_transition" for d in validate(ws8e, None, prof8e)))

# ════════ ⑨ (b)修复审计回归 ════════
from pipeline.world_gen import _affix_units  # noqa (已导入)
# ⑨a【高】字段白名单真源 = field_schema ∪ state_machines.field(漏 sm → 误删状态机字段、C1③ 空转)
def _wl(schema_fields_set, sm_fields):
    """复刻 build_world 白名单口径(真源并 sm)。"""
    src = set(schema_fields_set) | set(sm_fields)
    return src
ck("⑨a 白名单真源并入 state_machines.field", "案件状态" in _wl({"标的额", "主办律师"}, {"案件状态"}))
ck("⑨a 偏好轴字段不并入真源(L4 独家注入,否则双流复活)", "处理策略倾向" not in _wl({"标的额"}, set()))
# ⑨b【高】L7 只对 imprint 种下的字段判趋势:非 imprint 的噪声字段不产软 gold(首末净方向对噪声放行率高)
from pipeline.lines.L7_consolidation import ConsolidationLine as _CL
noise_ws = WorldState({"噪声案": {"杂值": _tl((0, "57"), (1, "56"), (3, "68"), (5, "10"))},   # 纯噪声 4 点
                       "干净案": {"标的额": _tl((0, "100"), (1, "160"), (2, "220"), (3, "280"), (4, "340"), (5, "400"), (6, "460"), (7, "400"))}},
                      n_sessions=8)
noise_ws._trended_fields = [("干净案", "标的额")]                # 只有干净案被 imprint 种过
trend_orders = _CL()._enum_trend(noise_ws)
ck("⑨b L7 只对 imprint 字段出趋势题(噪声字段不产软 gold)",
   {o["entity"] for o in trend_orders} == {"干净案"} and all(o["aux"]["sub"] == "S1_trend" for o in trend_orders))
ck("⑨b 无 _trended_fields → 零趋势题(不扫全字段)", _CL()._enum_trend(WorldState({"x": {"f": _tl((0, "1"), (1, "9"))}}, n_sessions=2)) == [])

# ════════ ⑩ value_shape(累计工时非单调根治):议会声明值形状,代码机械执行 ════════
from pipeline.world_gen import imprint_structure as _imp
# ⑩a validate:声明 monotonic up 的字段逆向 → monotonic_violation;合规单增 → 无缺陷
ws10 = WorldState({
    "甲案": {"累计工时": _tl((0, "10"), (1, "8"), (2, "30"))},     # up 但回落 → 违规
    "乙案": {"累计工时": _tl((0, "10"), (1, "20"), (2, "35"))},    # 合规单增
    "丙案": {"风险评分": _tl((0, "3"), (1, "12"), (2, "5"))},      # 声明 [0,10] 但 12 出界
}, n_sessions=3)
prof10 = {"field_schema": [{"name": "累计工时", "kind": "numeric", "monotonic": "up"},
                           {"name": "风险评分", "kind": "numeric", "range": [0, 10]}]}
d10 = validate(ws10, None, prof10)
kinds10 = {(x["entity"], x["type"]) for x in d10}
ck("⑩a 单调 up 逆向→monotonic_violation", ("甲案", "monotonic_violation") in kinds10)
ck("⑩a 合规单增→无 violation", ("乙案", "monotonic_violation") not in kinds10)
ck("⑩a 值域出界→out_of_range", ("丙案", "out_of_range") in kinds10)
# ⑩b 关键反乒乓:声明单调的字段【豁免】既有 monotonic(MR退化)检查(否则'必须单调'×'不许单调'死锁)
ws10b = WorldState({"丁案": {"累计工时": _tl((0, "10"), (1, "20"), (2, "30"), (3, "40"))}}, n_sessions=4)  # 完美单增=旧monotonic会判退化
d10b = validate(ws10b, None, prof10)
ck("⑩b 单调声明字段豁免旧 monotonic 检查(不乒乓)",
   not any(x["type"] == "monotonic" for x in d10b) and not any(x["type"] == "monotonic_violation" for x in d10b))
# ⑩c imprint 跳过单调字段(不给累计安人造趋势)
ws10c = WorldState({f"案{i}": {"累计工时": _tl(*[(s, str(10 + s)) for s in range(6)]),
                              "自由值": _tl((0, "30"), (1, "50"), (2, "20"), (3, "60"), (4, "25"), (5, "55"))} for i in range(4)},
                   n_sessions=6)
_imp(ws10c, log=lambda *a: None, profile={"field_schema": [{"name": "累计工时", "kind": "numeric", "monotonic": "up"}], "l7_max_trends": 20})
trended_fields = {f for (_e, f) in ws10c._trended_fields}
ck("⑩c imprint 跳过单调字段(累计工时不被注趋势)", "累计工时" not in trended_fields)
ck("⑩c imprint 仍注自由字段", "自由值" in trended_fields)
# ⑩d 声明非法值域([下≥上])作废、不崩
ck("⑩d 非法值域声明作废不崩", isinstance(validate(ws10, None,
   {"field_schema": [{"name": "风险评分", "kind": "numeric", "range": [10, 0]}]}), list))
# ⑩e 无声明→零回归(value_shape 不触发)
ck("⑩e 无 shape 声明→不产 violation/oob", not any(x["type"] in ("monotonic_violation", "out_of_range")
   for x in validate(ws10, None, {"field_schema": [{"name": "累计工时", "kind": "numeric"}]})))

# ════════ ⑪ value_shape 补审修复(千分位假阳假阴 + 覆盖非补缺 + 改名告警)════════
from pipeline.world_state import _to_num as _tnum
# ⑪a【高】_to_num 剥千分位逗号(旧版 '1,050'→1.0 在累计字段造假阳/假阴)
ck("⑪a _to_num('1,050')==1050(剥千分位)", _tnum("1,050") == 1050.0)
ck("⑪a _to_num('12,000小时')==12000", _tnum("12,000小时") == 12000.0)
ck("⑪a _to_num 全角逗号", _tnum("1，050") == 1050.0)
# ⑪a2【终审 HIGH 根治】_magnitude 按中文大数单位归一(剥逗号只关一种写法,万/亿同构轴)
from pipeline.world_state import _magnitude as _mag
ck("⑪a2 _magnitude('9000万')==9e7", _mag("9000万") == 9000 * 1e4)
ck("⑪a2 _magnitude('1.2亿')==1.2e8", _mag("1.2亿") == 1.2e8)
ck("⑪a2 量级可比:9000万 < 1.2亿", _mag("9000万") < _mag("1.2亿"))
ck("⑪a2 无后缀=数值本身、含逗号剥", _mag("1,050") == 1050.0 and _mag("85%") == 85.0)
# ⑪a3 混量纲真单增(9000万→1.2亿)→ 不再误判 monotonic_violation(终审点名的假阳)
ws_mag = WorldState({"案": {"累计额": _tl((0, "8000万"), (1, "9000万"), (2, "1.2亿"), (3, "1.5亿"))}}, n_sessions=4)
prof_mag = {"field_schema": [{"name": "累计额", "kind": "numeric", "monotonic": "up", "unit": "万"}]}
ck("⑪a3 混量纲真单增→无 violation(万/亿假阳消除)", not any(x["type"] == "monotonic_violation" for x in validate(ws_mag, None, prof_mag)))
# ⑪a4 混量纲真出界(2亿 对 值域[0,5000]万)→ 抓到 out_of_range(终审点名的假阴)
ws_mag2 = WorldState({"案": {"额度": _tl((0, "3000"), (1, "2亿"))}}, n_sessions=2)
prof_mag2 = {"field_schema": [{"name": "额度", "kind": "numeric", "range": [0, 5000]}]}
ck("⑪a4 混量纲真出界→抓到 out_of_range(万/亿假阴消除)", any(x["type"] == "out_of_range" for x in validate(ws_mag2, None, prof_mag2)))
# ⑪b 千分位的真·单增累计轨迹 → 不再误判 monotonic_violation(假阳消除)
ws11 = WorldState({"甲": {"累计工时": _tl((0, "980"), (1, "1,050"), (2, "1,200"), (3, "1,450"))}}, n_sessions=4)
prof11 = {"field_schema": [{"name": "累计工时", "kind": "numeric", "monotonic": "up"}]}
ck("⑪b 千分位真单增→无 violation(假阳消除)", not any(x["type"] == "monotonic_violation" for x in validate(ws11, None, prof11)))
# ⑪c 千分位的真·出界值 → 不再漏判 out_of_range(假阴消除)
ws11c = WorldState({"乙": {"额度": _tl((0, "3000"), (1, "12,000"))}}, n_sessions=2)
prof11c = {"field_schema": [{"name": "额度", "kind": "numeric", "range": [0, 5000]}]}
ck("⑪c 千分位真出界→抓到 out_of_range(假阴消除)", any(x["type"] == "out_of_range" for x in validate(ws11c, None, prof11c)))
# ⑪d【高】字段约束 critic【改值】(up→down)→ _canonicalize_lines 以 draft 覆盖(非仅补缺)
draft_ov = {"active_lines": [{"line": "L1_timeline"}],
            "domain_profile": {"field_schema": [{"name": "累计工时", "kind": "numeric", "monotonic": "up"},
                                                {"name": "评分", "kind": "numeric", "range": [0, 10]}]}}
wp_ov = {"active_lines": [{"line": "L1_timeline"}],
         "domain_profile": {"field_schema": [{"name": "累计工时", "kind": "numeric", "monotonic": "down"},  # critic 改了 up→down(更毒)
                                             {"name": "评分", "kind": "numeric", "range": [0, 5]}]}}        # critic 改窄了 range
_canonicalize_lines(wp_ov, draft_ov, log=lambda *a: None)
fs_ov = {f["name"]: f for f in wp_ov["domain_profile"]["field_schema"]}
ck("⑪d critic 改 up→down → 以 draft 覆盖回 up(治'改'非只治'删')", fs_ov["累计工时"]["monotonic"] == "up")
ck("⑪d critic 改窄 range → 覆盖回原值", fs_ov["评分"]["range"] == [0, 10])
# ⑪e critic 在 observe 沉默处的合法补充【保留】(draft 该字段无该约束 → 不覆盖)
draft_add = {"active_lines": [{"line": "L1_timeline"}],
             "domain_profile": {"field_schema": [{"name": "工时", "kind": "numeric"}]}}              # draft 无 monotonic
wp_add = {"active_lines": [{"line": "L1_timeline"}],
          "domain_profile": {"field_schema": [{"name": "工时", "kind": "numeric", "monotonic": "up"}]}}  # critic 补了
_canonicalize_lines(wp_add, draft_add, log=lambda *a: None)
ck("⑪e critic 在 draft 沉默处的补充保留(不被覆盖掉)",
   wp_add["domain_profile"]["field_schema"][0].get("monotonic") == "up")

# ════════ ⑫ 小尾巴:Q49 主语保真兜底 + 拒答属性归属协议 ════════
# ⑫a phrase 主语兜底:phraser 丢了实体名(代词化)→ 退回 intent 原文(不出悬空主语题)
class _PronounTracer:
    def chat_json(self, tag, messages, **kw):
        if tag == "phrase":
            return {"question": "第5周的时候，他的‘督导合伙人’是哪一个？"}   # 故意丢主语「周涛」
        return {"docs": []}
from pipeline.render import phrase_questions
ord49 = {"line": "L5_conflict", "capability": "L5_conflict", "entity": "周涛", "field": "督导合伙人",
         "gt": "宋清", "aux": {"session": 4, "rule": "source_reliability", "authoritative_value": "宋清",
                              "authoritative_source": "官方通报", "rumor_value": "周正", "rumor_source": "走廊传闻"}}
phrased = phrase_questions([ord49], {"domain_profile": {}}, _PronounTracer(), log=lambda *a: None)
ck("⑫a phraser 丢主语→退回含『周涛』的 intent(不出悬空代词题)", phrased and "周涛" in phrased[0]["question"])
# ⑫b 实体名在题面里则不动(正常 phrase 不误伤)
class _OkTracer:
    def chat_json(self, tag, messages, **kw):
        return {"question": "周涛的督导合伙人按官方记录是哪位？"} if tag == "phrase" else {"docs": []}
ph_ok = phrase_questions([ord49], {"domain_profile": {}}, _OkTracer(), log=lambda *a: None)
ck("⑫b 正常含主语题面不被兜底改写", ph_ok[0]["question"] == "周涛的督导合伙人按官方记录是哪位？")
# ⑫c 协议加属性归属声明(v4)
from pipeline.factory import ANSWER_PROTOCOL as _AP
ck("⑫c 协议 v4 + 属性归属声明", _AP["version"] == 4 and _AP.get("attribute_ownership_no_fold") is True
   and any("不得经关系链折算" in r for r in _AP["rules"]))

# ════════ ⑦ L4 _choice_field 叠词去重 ════════
l4 = PreferenceLine()
ck("⑦轴名已带'倾向' → 不叠词", l4._choice_field("本期处理策略倾向") == "本期处理策略倾向")
ck("⑦普通轴名 → 照常加 TAG", l4._choice_field("随访方式") == "随访方式倾向")

npass = sum(1 for ok, _ in checks if ok)
for ok, name in checks:
    if not ok:
        print(f"  ✗ {name}")
print(f"[刀1 self-test] {npass}/{len(checks)} PASS")
sys.exit(0 if npass == len(checks) else 1)
