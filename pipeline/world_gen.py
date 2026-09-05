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
                                   Op, SET, UPDATE, EXPIRE, DELETE, INSUFFICIENT, INVALID,
                                   _to_num, _as_int)
from pipeline.world_blueprint import relation_owner_side
from pipeline.prompts import render

_BARE_NUM = re.compile(r"^-?\d+(?:\.\d+)?$")
_FIELD_KIND_SUFFIX = re.compile(
    r"\s*[\(\uff08]\s*(?:text|status|category|numeric|number|person|reference|string)\s*[\)\uff09]\s*$",
    re.IGNORECASE,
)


def _canonicalize_generated_field_names(fields: dict, allowed: set[str]) -> dict:
    """将模型偶发输出的“字段名(kind)”归一为蓝图字段名。

    只在剥掉已知 kind 后与 allowed 中某一字段精确相等时生效；
    未知字段仍留给 off-schema 门禁删除。
    """
    out = {}
    for raw_name, spec in fields.items():
        name = raw_name
        if isinstance(raw_name, str) and raw_name not in allowed:
            candidate = _FIELD_KIND_SUFFIX.sub("", raw_name)
            if candidate in allowed:
                name = candidate
        # 模型同时给标准键和带注解键时，优先保留标准键。
        if name not in out or raw_name in allowed:
            out[name] = spec
    return out


def _filter_incremental_structure(structure: dict, existing: WorldState,
                                  blueprint: dict) -> tuple[list[dict], list[dict]]:
    """过滤增量骨架中与旧世界重复或同期冲突的候选。

    保留引用旧实体的真新关系/事件，但不让模型重放 rel-1/evt-1，
    也不让它在旧 canonical timeline 已有操作的同一 session 再写一次。
    """
    old_rel_ids = {item.get("id") for item in existing.relations if item.get("id")}
    old_rel_edges = {(item.get("type"), item.get("from"), item.get("to"),
                      _as_int(item.get("session"), 0)) for item in existing.relations}
    rel_decls = {item.get("id"): item for item in blueprint.get("relation_types", [])}
    relations = []
    for item in (structure.get("relations") or []):
        if not isinstance(item, dict) or item.get("id") in old_rel_ids:
            continue
        edge = (item.get("type"), item.get("from"), item.get("to"),
                _as_int(item.get("session"), 0))
        if edge in old_rel_edges:
            continue
        decl = rel_decls.get(item.get("type")) or {}
        side = relation_owner_side(blueprint, decl) if decl else None
        owner = item.get("from") if side != "to" else item.get("to")
        timeline = existing.timeline(owner, decl.get("field")) if owner and decl.get("field") else None
        if timeline and any(op.session == edge[3] for op in timeline.ops):
            continue
        relations.append(item)

    old_event_ids = {item.get("id") for item in existing.events if item.get("id")}
    old_event_keys = {(item.get("type"), _as_int(item.get("session"), -1),
                       json.dumps(item.get("participants") or {}, ensure_ascii=False, sort_keys=True))
                      for item in existing.events}
    events = []
    for item in (structure.get("events") or []):
        if not isinstance(item, dict) or item.get("id") in old_event_ids:
            continue
        session = _as_int(item.get("session"), -1)
        key = (item.get("type"), session,
               json.dumps(item.get("participants") or {}, ensure_ascii=False, sort_keys=True))
        if key in old_event_keys:
            continue
        clashes = False
        for effect in item.get("effects") or []:
            entity, field = effect.get("entity"), effect.get("field")
            timeline = existing.timeline(entity, field) if entity and field else None
            if timeline and any(op.session == session for op in timeline.ops):
                clashes = True
                break
        if not clashes:
            events.append(item)
    return relations, events


def _wire_declared_causality(structure: dict, blueprint: dict) -> int:
    """只在已有事件已满足类型与时差时补 caused_by，不创建事件或改写 session。"""
    events = [event for event in structure.get("events", []) if isinstance(event, dict)]
    rules = [rule for rule in blueprint.get("causal_rules", []) if isinstance(rule, dict)]
    wired = 0
    for rule in rules:
        trigger_type = rule.get("trigger_event")
        effect_type = rule.get("effect_event")
        delay = _as_int(rule.get("delay_sessions"), -1)
        parents = sorted((event for event in events
                          if event.get("type") == trigger_type and event.get("id")),
                         key=lambda event: (_as_int(event.get("session"), -1), str(event.get("id"))))
        children = sorted((event for event in events
                           if event.get("type") == effect_type and event.get("id")),
                          key=lambda event: (_as_int(event.get("session"), -1), str(event.get("id"))))
        if any(child.get("caused_by") == parent.get("id")
               and child.get("id") != parent.get("id")
               and _as_int(child.get("session"), -1) - _as_int(parent.get("session"), -1) == delay
               for child in children for parent in parents):
            continue
        pair = next(((parent, child) for child in children if not child.get("caused_by")
                     for parent in parents
                     if child.get("id") != parent.get("id")
                     and _as_int(child.get("session"), -1)
                     - _as_int(parent.get("session"), -1) == delay), None)
        if pair:
            parent, child = pair
            child["caused_by"] = parent["id"]
            wired += 1
    return wired


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


def _world_system(profile: dict, type_id: str, time_unit: str, open_schema: bool = False) -> str:
    noun = profile.get("entity_noun", "实体")
    fields = profile.get("field_schema", [])
    fdesc = "、".join(_field_desc(f) for f in fields) or ("若干随时间演化字段" if open_schema else "无内在字段")
    stopped = profile.get("stopped_phrase", "停止/失效")
    return render("world.system", noun=noun, type_id=type_id, time_unit=time_unit,
                  fdesc=fdesc, stopped=stopped)


def build_world(wp, tracer, log=print, existing=None) -> WorldState:
    """按白皮书蓝图生成 typed world，并将关系/事件编译进既有 Timeline 地基。

    历史白皮书会先适配成单类型蓝图；显式蓝图不合法、类型数量不足或声明结构没有
    合法实例时 fail-loud，不允许静默退化回扁平世界。``existing`` 只增长缺口实体。
    """
    from pipeline.world_blueprint import normalize_world_blueprint, WorldBlueprintError

    blueprint = normalize_world_blueprint(wp)
    typed_contract = not blueprint.get("legacy_adapter", False)
    temporal = blueprint["temporal_model"]
    n_sessions = int(temporal["n_sessions"])
    time_unit = temporal["unit"]
    type_specs = blueprint["entity_types"]
    primary = next(t for t in type_specs if t.get("primary"))

    # 下游旧产线继续消费 domain_profile，但字段集合只能由 blueprint 投影，不能成为第二真源。
    profile = dict(wp.get("domain_profile") or {})
    projected_fields = {}
    for t in type_specs:
        for f in t.get("fields") or []:
            projected_fields.setdefault(f.get("name"), dict(f))
    profile["entity_noun"] = primary["noun"]
    profile["field_schema"] = list(projected_fields.values())
    derived_sms = [{"field": f["name"], "states": f["states"]}
                   for t in type_specs for f in t.get("fields", [])
                   if f.get("name") and isinstance(f.get("states"), list) and f.get("states")]
    if typed_contract:
        profile["state_machines"] = derived_sms
    elif derived_sms:
        old_sms = list(profile.get("state_machines") or [])
        seen_sms = {m.get("field") for m in old_sms}
        profile["state_machines"] = old_sms + [m for m in derived_sms if m["field"] not in seen_sms]

    spec = wp.get("shared_world_spec", {})
    noun = primary["noun"]
    # ★W.3:让 build_world 真正消费白皮书的 change_density / traps(此前全程无视)
    cd = (spec.get("timeline", {}) or {}).get("change_density", "")
    # 近重名陷阱已在源头【议会菜单 council.traps】删除(不靠代码子串猜,审计★1);
    # 万一漏网,seen_base 在收集期按主干去重(出口拦截)= 真兜底,故此处不再用关键词黑名单过滤。
    traps = [t.get("trap") for t in (wp.get("traps") or []) if t.get("trap")][:3]
    base_extra = (f"★变更密度:evolving 字段尽量按「{cd}」铺满全程。" if cd else "")
    base_extra += (f"★陷阱布局:本场景需自然埋入这些坑——{traps}(如可矛盾的多源字段、易混字段)。" if traps else "")
    merged = {"entities": [], "relations": [], "events": [], "cascades": [], "absent_fields": []}
    base_ents = existing.entities if existing is not None else {}    # ★增量:在既有世界上只长新实体
    seen = set(base_ents)                             # 新实体名避开既有
    seen_base = {_strip_disambig(e) for e in base_ents}  # ★Fix3:也避开既有主干(不近重名)
    batch = 8

    existing_types = getattr(existing, "entity_types", {}) if existing is not None else {}
    if typed_contract and base_ents and not existing_types:
        raise WorldBlueprintError("typed blueprint 不能在缺少 entity_types 的旧世界上增量生成；请全量重建")
    if existing is not None and blueprint.get("legacy_adapter") and not existing_types:
        existing.entity_types.update({name: "legacy_entity" for name in base_ents})
        existing_types = existing.entity_types
    missing_total = sum(max(0, t["count"] - sum(1 for x in existing_types.values() if x == t["id"]))
                        for t in type_specs)
    if existing is not None and missing_total == 0:
        # 闭环已有实体数已达目标时保持真正 no-op：不调用 LLM、不重建旧 timeline，只补元数据。
        existing.n_sessions = max(existing.n_sessions or 0, n_sessions)
        existing.world_blueprint = blueprint
        coll = name_collisions(existing)
        log(f"  ✓ 基础世界:{len(existing.entities)} 实体 / {existing.n_sessions} {time_unit} / 增量无需新增"
            + (f" / ⚠表面塌缩近重名 {coll}" if coll else ""))
        if not typed_contract:
            _affix_units(existing, profile, log)
            imprint_structure(existing, log, profile)
        return existing
    relation_fields = {}
    for rel in blueprint.get("relation_types", []):
        side = relation_owner_side(blueprint, rel)
        owner = rel["from_type"] if side == "from" else rel["to_type"]
        relation_fields.setdefault(owner, set()).add(rel["field"])
    event_fields = {}
    for event in blueprint.get("event_types", []):
        roles = event.get("roles") or {}
        for effect in event.get("effect_fields", []):
            tid = roles.get(effect.get("role"))
            event_fields.setdefault(tid, set()).add(effect.get("field"))

    def _event_field_is_baseline_only(spec: dict) -> bool:
        """事件驱动字段在 entity batch 中至多给 session 0 初态，后续变化归 event。"""
        if not isinstance(spec, dict):
            return False
        if spec.get("type") == "stable" or ("value" in spec and "trajectory" not in spec):
            return spec.get("value") not in (None, "")
        points = sorted((p for p in spec.get("trajectory", []) if isinstance(p, dict)),
                        key=lambda p: _as_int(p.get("session"), -1))
        if not points or _as_int(points[0].get("session"), -1) != 0:
            return False
        last = None
        material = 0
        for point in points:
            value = point.get("value")
            if value in (None, ""):
                if last is not None:
                    material += 1
                last = None
            elif last is None or str(value).strip() != str(last).strip():
                material += 1
                last = value
        return material <= 1

    # 每个 entity type 独立生成，只给本类型字段；类型不是 prompt 装饰，而是字段白名单的索引。
    for t in type_specs:
        tid, type_noun = t["id"], t["noun"]
        base_count = (sum(1 for x in existing_types.values() if x == tid)
                      if existing_types else (len(base_ents) if blueprint.get("legacy_adapter") else 0))
        want_total = t["count"]
        produced: list[dict] = []
        intrinsic = [f for f in t.get("fields", []) if f.get("name") not in relation_fields.get(tid, set())]
        type_profile = {**profile, "entity_noun": type_noun, "field_schema": intrinsic}
        type_field_names = {f.get("name") for f in t.get("fields", [])}
        sm_decl = [f"「{m['field']}」只能取 {m['states']} 且按此序单向推进(可跳级、不可回头)"
                   for m in (profile.get("state_machines") or [])
                   if m.get("field") in type_field_names and m.get("states")]
        type_extra = base_extra + (f"★状态机字段(C1③ 源头约束,validate 还会机械校验):{';'.join(sm_decl)}。"
                                   if sm_decl else "")
        owned = sorted(event_fields.get(tid, set()) - relation_fields.get(tid, set()))
        if owned:
            type_extra += (f"★这些字段由 domain event 驱动:{owned}。entity batch 可省略它们；若给初态，"
                           "只能给 stable 或 session=0 的单一基线，严禁自行生成后续变化，后续只能由事件 effect 写入。")
            if set(owned) == {f.get("name") for f in intrinsic if f.get("name")}:
                type_extra += ("★本类型全部字段都由 event 驱动：请直接输出 fields={}；"
                               "不要为了满足末尾 null 要求擅自生成任何字段轨迹。")
        open_schema = bool(blueprint.get("legacy_adapter") and not intrinsic)
        sysp = _world_system(type_profile, tid, time_unit, open_schema=open_schema)

        for rnd in range(4):
            need = want_total - base_count - len(produced)
            if need <= 0:
                break
            wants = [min(batch, max(0, need - i * batch)) for i in range((need + batch - 1) // batch)]

            def _world_batch(want):
                used_names = json.dumps(sorted(seen), ensure_ascii=False)
                return tracer.chat_json("world.batch",
                    [{"role": "system", "content": sysp},
                     {"role": "user", "content": render("world.user", want=want, noun=type_noun,
                                                          type_id=tid, smax=n_sessions - 1, extra=type_extra)
                      + f"\n【全世界已占用专名，禁止复用或换类型冒用】{used_names}"
                        "\n必须返回足量、与本类型 noun 相称的新专名。"}],
                    temperature=0.7, max_tokens=8192)

            for out in config.pmap(_world_batch, wants, workers=len(wants)):
                for raw in (out.get("entities", []) if isinstance(out, dict) else []):
                    if not isinstance(raw, dict):
                        continue
                    e = dict(raw)
                    if typed_contract and e.get("type") != tid:
                        continue
                    e["type"] = tid                 # legacy 输出常写 noun；入真源后一律用稳定 type id
                    nm = e.get("name")
                    if not nm or nm in seen or not isinstance(e.get("fields", {}), dict):
                        continue
                    base = _strip_disambig(nm)
                    if base in seen_base:
                        continue
                    allowed = {f.get("name") for f in intrinsic if f.get("name")}
                    required = allowed - event_fields.get(tid, set())
                    fields = _canonicalize_generated_field_names(
                        dict(e.get("fields") or {}), allowed)
                    off_schema = [] if open_schema else [fn for fn in fields if fn not in allowed]
                    for fn in off_schema:
                        fields.pop(fn, None)
                    if typed_contract and not required.issubset(fields):
                        continue                     # 缺本类型内在字段的实体不计数，交给下一生成轮补齐
                    if typed_contract:
                        # 事件字段的轨迹由 structure 单一真源生成。模型若无视提示给了多期轨迹，
                        # 丢掉该字段即可；不能因此连合法实体专名也整条拒收，导致 0/N 振荡。
                        for fname in event_fields.get(tid, set()) - relation_fields.get(tid, set()):
                            if fname in fields and not _event_field_is_baseline_only(fields[fname]):
                                fields.pop(fname, None)
                    e["fields"] = fields
                    seen.add(nm); seen_base.add(base); produced.append(e)
                    if base_count + len(produced) >= want_total:
                        break
            log(f"  世界·{tid} round{rnd+1}:累计 {base_count + len(produced)}/{want_total} {type_noun}"
                f"{'(增量)' if existing is not None else ''}(并发 {len(wants)} 批)")
        if base_count + len(produced) < want_total:
            raise WorldBlueprintError(
                f"entity type {tid} 实例不足:{base_count + len(produced)}/{want_total}；4 轮后仍未满足蓝图")
        merged["entities"].extend(produced[:max(0, want_total - base_count)])

    # 关系/事件在所有 typed entities 生成后统一实例化；提示中带完整契约与可引用实体目录。
    structural_markers = ("entity ", "relation", "event", "causal rule")
    if typed_contract and (blueprint.get("relation_types") or blueprint.get("event_types")):
        def _initial_state(name, tid, raw_fields=None):
            out = {}
            if raw_fields is None and existing is not None:
                for fname in event_fields.get(tid, set()):
                    tl = existing.entities.get(name, {}).get(fname)
                    if tl and tl.value_at_session(0) not in (None, INSUFFICIENT, INVALID):
                        out[fname] = tl.value_at_session(0)
                return out
            for fname in event_fields.get(tid, set()):
                spec = (raw_fields or {}).get(fname) or {}
                if spec.get("type") == "stable" or ("value" in spec and "trajectory" not in spec):
                    if spec.get("value") not in (None, ""):
                        out[fname] = spec.get("value")
                else:
                    first = next((p for p in spec.get("trajectory", [])
                                  if isinstance(p, dict) and _as_int(p.get("session"), -1) == 0
                                  and p.get("value") not in (None, "")), None)
                    if first:
                        out[fname] = first.get("value")
            return out

        catalog = [{"name": e, "type": existing_types.get(e),
                    "initial_state": _initial_state(e, existing_types.get(e))} for e in base_ents]
        catalog += [{"name": e.get("name"), "type": e.get("type"),
                     "initial_state": _initial_state(e.get("name"), e.get("type"), e.get("fields") or {})}
                    for e in merged["entities"]]
        hint = ""
        for attempt in range(5):
            structure = tracer.chat_json("world.structure",
                [{"role": "system", "content": render("world.structure", smax=n_sessions - 1)},
                 {"role": "user", "content": render(
                     "world.structure_user", time_unit=time_unit, cadence=temporal["cadence"],
                     smax=n_sessions - 1, blueprint=json.dumps(blueprint, ensure_ascii=False),
                     entities=json.dumps(catalog, ensure_ascii=False)) + hint}],
                temperature=0.4, max_tokens=8192)
            if isinstance(structure, dict):
                _wire_declared_causality(structure, blueprint)
                relations = [x for x in structure.get("relations", []) if isinstance(x, dict)]
                events = [x for x in structure.get("events", []) if isinstance(x, dict)]
                if existing is not None:
                    relations, events = _filter_incremental_structure(structure, existing, blueprint)
                merged["relations"], merged["events"] = relations, events
            trial, trial_issues = assemble_world(merged, blueprint=blueprint, existing=existing)
            structural_issues = [x for x in trial_issues if x.startswith(structural_markers)]
            if not structural_issues:
                break
            log(f"  ⟳ 世界骨架实例修复轮{attempt+1}:{len(structural_issues)} 个契约违例")
            hint = ("\n【上轮机械校验失败，必须基于上轮候选逐项修正】\n- "
                    + "\n- ".join(structural_issues)
                    + "\n【上轮候选 JSON】\n"
                    + json.dumps(structure, ensure_ascii=False))

    ws, compile_issues = assemble_world(merged, blueprint=blueprint, existing=existing)
    structural_issues = [x for x in compile_issues if x.startswith(structural_markers)]
    if structural_issues:
        raise WorldBlueprintError("世界实例未满足 blueprint:\n- " + "\n- ".join(structural_issues))
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
            tid = (ent_idx.get(ent) or {}).get("type") or getattr(ws, "entity_types", {}).get(ent)
            repair_noun = next((t["noun"] for t in type_specs if t["id"] == tid), noun)
            lines = "\n".join(
                f"  字段「{d['field']}」缺陷[{d['type']}]:{d['detail']};当前={json.dumps(cur.get(d['field'], {}), ensure_ascii=False)}"
                for d in ds)
            return ent, tracer.chat_json("world.repair",
                [{"role": "system", "content": render("world.repair", noun=repair_noun, smax=n_sessions - 1)},
                 {"role": "user", "content": render("world.repair_user", noun=repair_noun, ent=ent, defects=lines, smax=n_sessions - 1)}],
                temperature=0.8, max_tokens=4096)

        for ent, out in config.pmap(_repair, list(by_ent.items()), workers=min(8, len(by_ent))):
            e = ent_idx.get(ent)
            newf = (out.get("fields") if isinstance(out, dict) else None) or {}
            if e and newf:                                # 只覆盖被点名的坏字段,不新增/不动其它字段
                e["fields"].update({k: v for k, v in newf.items() if k in e.get("fields", {})})
        ws, compile_issues = assemble_world(merged, blueprint=blueprint, existing=existing)
        structural_issues = [x for x in compile_issues if x.startswith(structural_markers)]
        if structural_issues:
            raise WorldBlueprintError("修复后世界结构破坏 blueprint:\n- " + "\n- ".join(structural_issues))
    ws.n_sessions = max(ws.n_sessions or 0, n_sessions, (existing.n_sessions if existing is not None else 0))
    if existing is not None:                          # ★增量 augment:只把【新实体】并入既有世界,旧实体/旧 docs 全不动
        # 保持调用方持有的对象身份不变，但用“旧世界 + delta 编译”的完整结果原子替换其状态。
        for attr in ("entities", "cascades", "absent_fields", "n_sessions", "conflicts", "sensitive",
                     "conditional_rules", "rule_instances", "entity_types", "relations", "events",
                     "world_blueprint"):
            setattr(existing, attr, getattr(ws, attr))
        existing._trended_fields = list(getattr(ws, "_trended_fields", []) or [])
        ws = existing
    rem = validate(ws, merged, profile)               # ★validate 在【注趋势前】跑(对 merged 一致,不误报);imprint 产出本就良构,无需复验
    if typed_contract and rem:
        details = [f"{d.get('entity')}.{d.get('field')}[{d.get('type')}]:{d.get('detail')}" for d in rem]
        raise WorldBlueprintError("typed world 修复轮耗尽后仍有真值缺陷:\n- " + "\n- ".join(details))
    coll = name_collisions(ws)                        # ★Fix3:表面塌缩兜底检测(收集期已按主干去重,这里抓漏网)
    log(f"  ✓ 基础世界:{len(ws.entities)} 实体 / {ws.n_sessions} {time_unit} / 修复后残留缺陷 {len(rem)}"
        + (f" / ⚠表面塌缩近重名 {coll}" if coll else ""))  # 产线基质由 stage_world 的 line.prepare() 叠加
    # ★声明-世界对齐自检(刀1审计:声明字段在世界中无命中时静默 no-op,漂移不可观测)
    all_fields = {f for flds in ws.entities.values() for f in flds}
    ghost = [m.get("field") for m in (profile.get("state_machines") or []) if m.get("field") and m["field"] not in all_fields]
    ghost += [f.get("name") for f in profile.get("field_schema", []) if f.get("unit") and f.get("name") not in all_fields]
    if ghost:
        log(f"  ⚠声明字段未在世界命中(states/unit 约束将空转,检查议会命名一致性):{sorted(set(ghost))}")
    if typed_contract:
        log("  ✓ typed world 冻结:跳过 legacy 单位补写/趋势整形，领域事件与 timeline 保持同一真源")
    else:
        _affix_units(ws, profile, log)                # legacy 保持历史单位真源化行为
        imprint_structure(ws, log, profile)           # legacy 保持历史 L7 基质整形行为
    return ws
