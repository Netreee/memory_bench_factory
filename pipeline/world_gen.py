"""
pipeline.world_gen —— §5 共享世界生成(从 run_factory_v2 拆出,行为不变)。
build_world:并发批次让 LLM 填世界表 → assemble_world 成状态机 → W.3 CRITIC 修复轮(validate 缺陷定向重生成)。
"""
from __future__ import annotations
import json
import random
import re
import config
from pipeline.world_state import (assemble_world, validate, WorldState, _strip_disambig, name_collisions,
                                   Op, SET, UPDATE, EXPIRE, DELETE, _to_num)
from pipeline.prompts import render

_BARE_NUM = re.compile(r"^-?\d+(?:\.\d+)?$")


def _affix_units(ws: WorldState, profile: dict | None, log=print) -> int:
    """★C1② 单位真源化:schema 声明了 unit 的字段,canonical 裸数值统一补上单位(代码定,不靠 LLM 服从)。
    根(014559 实证):世界 1000+ 数值 op 全裸数、渲染 LLM 31% 表述自发加'万元'、36/43 实体跨周单位忽有忽无
    ——量纲是 gold 的一部分却没有真源。补法 = 真源迁移:单位进 canonical,渲染核验子串尺对'加单位'的
    单向失明自动闭合('200万元' 必须逐字在场),零新增规则。幂等(已带单位的值非裸数,二次调用 no-op)。
    Op.prev 同步补(TR gt 的 {from,to} 也是人类口径)。非裸数值(LLM 自带别的写法)不动,只计数提示。"""
    units = {f.get("name"): str(f.get("unit")).strip()
             for f in (profile or {}).get("field_schema", []) if f.get("unit")}
    # ★unit 声明自检(刀1审计):unit 本身是纯数字(LLM 错填如 '0')会使 vs+u 仍匹配 _BARE_NUM → 每轮 ×10 跑飞
    bad_units = {f: u for f, u in units.items() if not u or _BARE_NUM.fullmatch(u)}
    if bad_units:
        log(f"  ⚠单位声明非法(纯数字/空),跳过:{bad_units}")
        units = {f: u for f, u in units.items() if f not in bad_units}
    if not units:
        return 0
    n, odd = 0, 0
    for ent, flds in ws.entities.items():
        for fname, tl in flds.items():
            u = units.get(fname)
            if not u:
                continue
            for o in tl.ops:
                for attr in ("value", "prev"):
                    v = getattr(o, attr)
                    if v is None:
                        continue
                    vs = str(v).strip()
                    if _BARE_NUM.fullmatch(vs):
                        setattr(o, attr, vs + u); n += 1
                    elif not vs.endswith(u) and _to_num(vs) is not None:
                        odd += 1                      # 非裸且后缀≠声明单位(如'1.2亿'):不动,只计数告警(混量纲闸未建,记 backlog)
    if n or odd:
        log(f"  ★单位真源化:{n} 个裸数值补上 schema 单位(单一真源){f';⚠{odd} 个异型写法未动(混量纲风险,人工抽查)' if odd else ''}")
    return n

# ── ★结构后处理(B:L7 没料的根治)──────────────────────────────────────────────
# LLM 吐的数值轨迹是【无趋势噪声 + 样样每周变】(实测:血压=[130,145,120,135…]、变动数全顶满)。
# L7-S1(趋势)要的"单调走向"世界里根本没有 → 产 0 单。趋势这种 type-critical 结构【不能靠 prompt 让 LLM 造】
# (它给不出带精确 margin+末段反转的可证伪趋势,踩坑账#6),必须【确定性代码】构造。
# 放 world_gen(而非 L7.prepare):趋势是【世界属性】、在世界构造期一次性定,早于任何线 enumerate/渲染
# → 单一真源不破、不可能矛盾;L7 退回"骑世界";整个 benchmark 也获得真实结构(不止 L7)。
_TREND_MIN_PTS = 5        # 需 ≥5 个点:三形状都要"首末净方向 + 中段≥1 局部反向"成形(点太少摆不开)


def _trend_values(n: int, lo: float, hi: float, up: bool, int_like: bool, decimals: int, shape: str = "end_reversal"):
    """造 n 个值,整体【首末净方向】= up(升)/down(降),但中段形状可变(174925 D:11 题全'单调+末周反跳'
    → 可被"永远看首末"识破)。三种形状,全部满足协议(趋势=首末净方向)且【看首末对、看局部错】:
      end_reversal : 前段单调 + 末步反向(看最近一段会判反)
      mid_dip      : 整体上行但中段一个深谷 / 整体下行但中段一个高峰(看中段会判反)
      late_surge   : 大部分平缓、末段才显著净移(看前段会判"没趋势/相反")
    净方向恒由首末锚定:首=lo·末=hi(up)/首=hi·末=lo(down),保证首末净方向唯一可裁。
    返回字符串列表;四舍五入后破坏【首末净方向严格成立 且 至少一处局部反向】→ None(跳过,不注坏趋势)。"""
    if n < 3 or hi <= lo:
        return None
    span = hi - lo
    first, last = (lo, hi) if up else (hi, lo)                  # 首末锚死净方向
    mid = n - 2                                                 # 中间点数
    if shape == "mid_dip":                                      # 中段一个反向极值(谷/峰)
        ext = hi if not up else lo                              # up→中段探到 lo(谷);down→探到 hi(峰)
        k = mid // 2 + 1
        raw = [first] + [ext if i == k else first + (last - first) * i / (n - 1) for i in range(1, n - 1)] + [last]
    elif shape == "late_surge":                                 # 前段微动、末段净移(看前段判不出/判反)
        plateau = first + (last - first) * 0.12
        raw = [first] + [plateau + (first - plateau) * 0.3 * (1 if i % 2 else -1) for i in range(1, n - 1)] + [last]
    else:                                                       # end_reversal(默认)
        step = span / (n - 2)
        raw = [(lo + i * step) if up else (hi - i * step) for i in range(n - 1)]
        raw.append(raw[-1] - step if up else raw[-1] + step)
    fmt = (lambda x: str(int(round(x)))) if int_like else (lambda x: f"{x:.{decimals}f}")
    out = [fmt(x) for x in raw]
    nums = [float(x) for x in out]
    from pipeline.lines.L7_consolidation import NET_FRAC                        # ★单一真源(审计:勿抄字面量 0.30,防漂移)
    net = (nums[-1] - nums[0]) if up else (nums[0] - nums[-1])
    net_ok = net >= NET_FRAC * (max(nums) - min(nums)) > 0                      # 首末净方向清晰(与 L7 _trend_label 同口径同常量)
    has_local_rev = any((nums[i] < nums[i - 1]) if up else (nums[i] > nums[i - 1]) for i in range(1, n))  # ≥1 处局部反向(挫"看局部"捷径)
    distinct = len({round(x, 6) for x in nums}) >= 3                           # 非恒定(≥3 个不同值;late_surge 平台期本就有重复,不强求 n-1)
    return out if (net_ok and has_local_rev and distinct) else None


def imprint_structure(ws: WorldState, log=print, profile: dict | None = None, seed: int = 20260608) -> int:
    """给一小撮【纯数值、≥5点、无停统、未注过】的字段注入真趋势(三形状轮转,首末净方向清晰、值域/格式不变)。
    幂等(已注的实体/字段跳过,augment 只注新);只动数值字段 → 与 L4(category)/L5(text)/L2(person)基质不相交,不撞。
    停统字段不碰(留给 FORGET/L6)。返回注入字段数。"""
    rng = random.Random(seed)
    done = set(getattr(ws, "_trended_fields", []) or [])
    ws._trended_fields = list(done)
    # ★value_shape:声明了【单调形状】的字段(累计/合计等)绝不注人造趋势——它们本就单向单调(world-gen+validate 保证),
    #   安 end_reversal/mid_dip 会直接违反单调语义(183626:imprint 给"累计工时"安"下降"→逻辑不可能 gold)。L7 也不问它们的"趋势"。
    shaped = {f.get("name") for f in (profile or {}).get("field_schema", []) if f.get("monotonic") in ("up", "down")}
    cands = []
    for ent, flds in ws.entities.items():
        for fname, tl in flds.items():
            if (ent, fname) in done or fname in shaped:
                continue
            ops = tl._sorted()
            if any(o.op in (EXPIRE, DELETE) for o in ops):          # 有停统 → 留给 FORGET/L6,不注趋势
                continue
            vals = [o.value for o in ops if o.op in (SET, UPDATE) and o.value]
            nums = [_to_num(v) for v in vals]
            if len(vals) < _TREND_MIN_PTS or any(x is None for x in nums):
                continue
            cands.append((ent, fname, tl, vals, nums))
    rng.shuffle(cands)
    cap = int((profile or {}).get("l7_max_trends", 24))             # 注入上限(够喂 L7 配额、又不淹没全世界震荡多样性)
    n_done = 0
    for i, (ent, fname, tl, vals, nums) in enumerate(cands):
        if n_done >= cap:
            break
        # ★后缀保真(C1② 配套):'450万元'/'2.5%' 重写后必须回贴后缀——旧实现 fmt 输出裸数,
        #   会把单位/百分号整条剥掉(二次剥单位源)。各值后缀不一致 → 字段口径已乱,跳过不注。
        sufs = {re.sub(r"^-?\d+(?:\.\d+)?", "", str(v)).strip() for v in vals}
        if len(sufs) != 1:
            continue
        suf = sufs.pop()
        lo, hi = min(nums), max(nums)
        int_like = all(float(x) == int(x) for x in nums)
        decimals = max((len(m.group(1)) for v in vals if (m := re.search(r"\.(\d+)", str(v)))), default=1)
        pts = [(o.session, o.date) for o in tl._sorted() if o.op in (SET, UPDATE)]
        shape = ("end_reversal", "mid_dip", "late_surge")[n_done % 3]   # ★形状轮转(174925:11题全单调+末反跳可被识破)
        new_vals = _trend_values(len(pts), lo, hi, up=(n_done % 2 == 0), int_like=int_like, decimals=decimals, shape=shape)
        if new_vals is None:                                        # 值域太窄,造不出严格趋势 → 跳过
            continue
        new_ops, prev = [], None
        for (s, d), v in zip(pts, new_vals):
            nv = v + suf
            new_ops.append(Op(s, d, SET if prev is None else UPDATE, nv, prev)); prev = nv
        tl.ops = new_ops
        ws._trended_fields.append((ent, fname)); n_done += 1
    if n_done:
        log(f"  ★结构后处理:{n_done} 个数值字段注入真趋势(三形状轮转 end_reversal/mid_dip/late_surge,首末净方向清晰、值域/格式不变)→ 喂 L7-S1")
    return n_done


def _field_desc(f: dict) -> str:
    """字段描述串(给 world prompt):名(kind + value_shape 约束)。约束随白皮书声明走,代码只转述、不猜。"""
    ann = [f.get("kind") or "?"]
    if f.get("unit"):
        ann.append(f"单位{f['unit']}")
    if f.get("monotonic") == "up":
        ann.append("累计只增不减")
    elif f.get("monotonic") == "down":
        ann.append("只减不增")
    rng = f.get("range")
    if isinstance(rng, (list, tuple)) and len(rng) == 2:
        ann.append(f"值域{rng[0]}-{rng[1]}")
    return f"{f.get('name')}({'·'.join(str(a) for a in ann)})"


def _world_system(profile: dict) -> str:
    noun = profile.get("entity_noun", "实体")
    fields = profile.get("field_schema", [])
    fdesc = "、".join(_field_desc(f) for f in fields) or "若干随时间演化字段"
    stopped = profile.get("stopped_phrase", "停止/失效")
    return render("world.system", noun=noun, fdesc=fdesc, stopped=stopped)


def build_world(wp, tracer, log=print, existing=None) -> WorldState:
    """existing=None:全量建。existing=WorldState:★增量 augment——只长【新实体】(名避开既有+主干)
    并入既有世界,旧实体不动(给闭环 ②环增量续渲用,§10.1)。"""
    profile = wp.get("domain_profile", {})
    spec = wp.get("shared_world_spec", {})
    n_entities = int(spec.get("entities", {}).get("count", 10))
    n_sessions = int(spec.get("timeline", {}).get("n_sessions", 16))
    noun = profile.get("entity_noun", "实体")
    sysp = _world_system(profile)
    # ★W.3:让 build_world 真正消费白皮书的 change_density / traps(此前全程无视)
    cd = (spec.get("timeline", {}) or {}).get("change_density", "")
    # 近重名陷阱已在源头【议会菜单 council.traps】删除(不靠代码子串猜,审计★1);
    # 万一漏网,seen_base 在收集期按主干去重(出口拦截)= 真兜底,故此处不再用关键词黑名单过滤。
    traps = [t.get("trap") for t in (wp.get("traps") or []) if t.get("trap")][:3]
    extra = (f"★变更密度:evolving 字段尽量按「{cd}」铺满全程。" if cd else "")
    extra += (f"★陷阱布局:本场景需自然埋入这些坑——{traps}(如可矛盾的多源字段、易混字段)。" if traps else "")
    sm_decl = [f"「{m['field']}」只能取 {m['states']} 且按此序单向推进(可跳级、不可回头)"
               for m in (profile.get("state_machines") or []) if m.get("field") and m.get("states")]
    extra += (f"★状态机字段(C1③ 源头约束,validate 还会机械校验):{';'.join(sm_decl)}。" if sm_decl else "")
    merged = {"entities": [], "cascades": [], "absent_fields": []}
    base_ents = existing.entities if existing is not None else {}    # ★增量:在既有世界上只长新实体
    seen = set(base_ents)                             # 新实体名避开既有
    seen_base = {_strip_disambig(e) for e in base_ents}  # ★Fix3:也避开既有主干(不近重名)
    base_n = len(base_ents)
    batch = 8

    def _world_batch(_i):                             # 一个批次:求 batch 个实体
        return tracer.chat_json("world.batch",
            [{"role": "system", "content": sysp},
             {"role": "user", "content": render("world.user", want=batch, noun=noun, smax=n_sessions - 1, extra=extra)}],
            temperature=0.7, max_tokens=8192)

    for rnd in range(4):                              # 最多 4 轮;每轮把"还差几个"凑成的批次【并发】发(全局信号量限在飞 API)
        if base_n + len(merged["entities"]) >= n_entities:
            break
        n_calls = (n_entities - base_n - len(merged["entities"]) + batch - 1) // batch
        for out in config.pmap(_world_batch, range(n_calls), workers=n_calls):
            for e in (out.get("entities", []) if isinstance(out, dict) else []):
                nm = e.get("name")
                if nm and nm not in seen and e.get("fields"):
                    base = _strip_disambig(nm)
                    if base in seen_base:             # ★Fix3:主干已存在(含既有世界)→ 表面塌缩近重名,丢弃
                        continue
                    seen.add(nm); seen_base.add(base); merged["entities"].append(e)
        log(f"  世界 round{rnd+1}: 累计 {base_n + len(merged['entities'])}/{n_entities} {noun}"
            f"{'(增量)' if existing is not None else ''}(并发 {n_calls} 批)")
    # ★字段白名单(174925 双字段串周根治):字段集【单一真源】= field_schema ∪ state_machines.field
    #   (审计 HIGH:state_machines 声明的『案件状态』不在 field_schema、却被 extra 指示 LLM 必造 → 漏进真源会被
    #    误删、C1③ 空转。真源必须并上它)。world-gen LLM 擅自发明的字段(legal『处理策略』『争议解决策略』与 L4 轴
    #   语义/取值池重叠 → gold 串)一律剔除。收自由度归 schema(同议会 canonical-id),非子串猜=非补丁。
    #   ★preference_axis.field 故意【不】并入真源:它由 L4.prepare 在 build_world 之后独家注入,并入会让 LLM 发明的
    #    轴字段存活 → 双流污染复活(正是要治的病)。L5 侧信道不占世界字段,不受此限。schema 空则不启用(防误删空世界)。
    schema_fields = {f.get("name") for f in profile.get("field_schema", []) if f.get("name")}
    schema_fields |= {m.get("field") for m in (profile.get("state_machines") or []) if m.get("field")}
    if schema_fields:
        dropped_f, emptied = {}, []
        for e in merged["entities"]:
            off_schema = [fn for fn in (e.get("fields") or {}) if fn not in schema_fields]
            for fn in off_schema:
                e["fields"].pop(fn, None); dropped_f[fn] = dropped_f.get(fn, 0) + 1
            if not e.get("fields"):                       # 审计 LOW:白名单把某实体剔成空 → assemble 会静默丢该实体
                emptied.append(e.get("name"))
        if dropped_f:
            log(f"  ★字段白名单:剔除 schema 外擅自发明字段 {dict(sorted(dropped_f.items(), key=lambda x:-x[1]))}"
                + (f";⚠{len(emptied)} 个实体被剔空将被丢弃 {emptied}(议会字段命名与世界生成不符,查 schema)" if emptied else ""))
    ws, _ = assemble_world(merged)
    # ★W.3 CRITIC 修复轮:assemble 算出的缺陷不再"只 log 就扔"——定向重生成坏字段(复用并行骨架:发散批次→收敛修复)
    ent_idx = {e.get("name"): e for e in merged["entities"]}
    for rep in range(3):
        defects = validate(ws, merged, profile)       # ★profile 进闸:illegal_transition(状态倒流)也进 CRITIC 修复轮
        if not defects:
            break
        by_ent: dict = {}
        for d in defects:
            by_ent.setdefault(d["entity"], []).append(d)
        log(f"  ⟳ 世界修复轮{rep+1}:{len(defects)} 个字段缺陷({len(by_ent)} 实体)→ 定向重生成坏字段")

        def _repair(item):
            ent, ds = item
            cur = (ent_idx.get(ent) or {}).get("fields", {})
            lines = "\n".join(
                f"  字段「{d['field']}」缺陷[{d['type']}]:{d['detail']};当前={json.dumps(cur.get(d['field'], {}), ensure_ascii=False)}"
                for d in ds)
            return ent, tracer.chat_json("world.repair",
                [{"role": "system", "content": render("world.repair", noun=noun, smax=n_sessions - 1)},
                 {"role": "user", "content": render("world.repair_user", noun=noun, ent=ent, defects=lines, smax=n_sessions - 1)}],
                temperature=0.8, max_tokens=4096)

        for ent, out in config.pmap(_repair, list(by_ent.items()), workers=min(8, len(by_ent))):
            e = ent_idx.get(ent)
            newf = (out.get("fields") if isinstance(out, dict) else None) or {}
            if e and newf:                                # 只覆盖被点名的坏字段,不新增/不动其它字段
                e["fields"].update({k: v for k, v in newf.items() if k in e.get("fields", {})})
        ws, _ = assemble_world(merged)
    ws.n_sessions = max(ws.n_sessions or 0, n_sessions, (existing.n_sessions if existing is not None else 0))
    if existing is not None:                          # ★增量 augment:只把【新实体】并入既有世界,旧实体/旧 docs 全不动
        existing.entities.update(ws.entities)
        existing.n_sessions = max(existing.n_sessions or 0, ws.n_sessions)
        ws = existing
    rem = validate(ws, merged, profile)               # ★validate 在【注趋势前】跑(对 merged 一致,不误报);imprint 产出本就良构,无需复验
    coll = name_collisions(ws)                        # ★Fix3:表面塌缩兜底检测(收集期已按主干去重,这里抓漏网)
    log(f"  ✓ 基础世界:{len(ws.entities)} 实体 / {ws.n_sessions} 周 / 修复后残留缺陷 {len(rem)}"
        + (f" / ⚠表面塌缩近重名 {coll}" if coll else ""))  # 产线基质由 stage_world 的 line.prepare() 叠加
    # ★声明-世界对齐自检(刀1审计:声明字段在世界中无命中时静默 no-op,漂移不可观测)
    all_fields = {f for flds in ws.entities.values() for f in flds}
    ghost = [m.get("field") for m in (profile.get("state_machines") or []) if m.get("field") and m["field"] not in all_fields]
    ghost += [f.get("name") for f in profile.get("field_schema", []) if f.get("unit") and f.get("name") not in all_fields]
    if ghost:
        log(f"  ⚠声明字段未在世界命中(states/unit 约束将空转,检查议会命名一致性):{sorted(set(ghost))}")
    _affix_units(ws, profile, log)                    # ★C1②:单位真源化(在 imprint 前,imprint 会保后缀回贴)
    imprint_structure(ws, log, profile)               # ★B:注入真趋势(世界属性,早于 orders/render → 不矛盾);L7-S1 据此有料
    return ws

