"""
pipeline.central_office —— 中央办公室【并行议会】版:场景+few-shot → 白皮书。

redesign_factory_v2.md §3 的升级:从"单轮提案+批判"→ 7 个【议会视角】并行 → 综合 → 批判。
视角(都不叫 L、不叫宪法,避免与产线 L1–L7 / 元架构宪法撞名):
  观测  observe  : few-shot 字面给了啥(只抽取、不推断)
  怀疑  skeptic  : 假定 few-shot 系统性缺失,阐发缺了啥(带合理性缰绳 + observed/inferred 标注)
  映射  map      : 对照宪法 L1–L7,本场景每条该不该激活、怎么落地
  媒介  medium   : 本场景还能是什么形式(先穷尽常见、再补反常但合理 —— 大而全,非猎奇)
  文风  style    : 抽 few-shot 风格 DNA + 原文当渲染范例
  陷阱  traps    : 本场景天然在哪坑记忆系统(接 M1–M6,设计区分度)
  世界  world    : 先冻结类型/关系/事件/时间制度,定义场景不可换皮的“骨架”
→ 综合成白皮书草案 → 批判(覆盖/自洽/gt可行/区分度)→ 定稿。

白皮书 schema 与下游(build_world/run_lines/render)兼容:必出 world_blueprint / domain_profile / medium /
active_lines / shared_world_spec / capability_targets;另附 style_spec / traps / line_mapping /
observed_vs_inferred(留痕,后续渲染/诊断用)。
"""
from __future__ import annotations
from copy import deepcopy
import json
import config
from pipeline.lines import taxonomy_prose
from pipeline.prompts import render          # ★议会 prompts 收编进注册表(council.*)
from pipeline.world_blueprint import normalize_world_blueprint, WorldBlueprintError

# ── 7 个议会视角的 system prompt ──────────────────────────────────────────
OBSERVE_SYS = render("council.observe")
SKEPTIC_SYS = render("council.skeptic")
MAP_SYS = render("council.map", taxonomy=taxonomy_prose())   # ★map 内联宪法 taxonomy(调用侧算好传入)
MEDIUM_SYS = render("council.medium")
STYLE_SYS = render("council.style")
TRAPS_SYS = render("council.traps")
WORLD_SYS = render("council.world")
WORLD_REVIEW_SYS = render("council.world_review")
WORLD_REPAIR_SYS = render("council.world_repair")

# ── 综合 + 批判 ──────────────────────────────────────────────────────────
CRITIC_SYS = render("council.critic", taxonomy=taxonomy_prose())   # ★critic 也内联 canonical 菜单(014559:critic 重写丢 L7+自编名,根在它没拿到菜单)
# (SYNTH_SYS 已删:综合早已改为代码确定性装配 _assemble_whitepaper,该 prompt 不再被调用)

_PERSPECTIVES = [
    ("observe", OBSERVE_SYS, "据实抽取 few-shot 字面信息。"),
    ("skeptic", SKEPTIC_SYS, "阐发本场景 few-shot 没展示但真实存在的媒介/实体/关系(带缰绳)。"),
    ("map", MAP_SYS, "对照 L1–L7,逐条判断本场景该不该激活、怎么落地。"),
    ("medium", MEDIUM_SYS, "发散本场景所有可能形式(常见穷尽 + 反常但合理),大而全。"),
    ("style", STYLE_SYS, "抽 few-shot 风格 DNA。"),
    ("traps", TRAPS_SYS, "设计本场景坑记忆系统的陷阱(接区分度)。"),
    ("world", WORLD_SYS, "定义本场景不可换皮的实体类型、关系、领域事件、因果与时间制度。"),
]


def _g(d, *keys, default=None):
    cur = d if isinstance(d, dict) else {}
    for k in keys:
        cur = cur.get(k, {}) if isinstance(cur, dict) else {}
    return cur if cur not in ({}, None) else default


_OBSERVE_UNSPECIFIED = {
    "", "-", "[]", "无", "可省", "不适用", "未注明", "未提供", "未观测", "未知",
    "null", "none", "nil", "n/a", "unknown",
}


def _observe_unspecified(value) -> bool:
    """判断 observe 输出是否只是“未知/可省”占位，而非可冻结的字面事实。"""
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in _OBSERVE_UNSPECIFIED
    if isinstance(value, (list, tuple)):
        return not value or all(_observe_unspecified(item) for item in value)
    if isinstance(value, dict):
        return not value or all(_observe_unspecified(item) for item in value.values())
    return False


def _observed_strings(value) -> list[str]:
    """清洗 observe 的字符串数组，丢弃空值/占位符/畸形元素并保持顺序去重。"""
    if not isinstance(value, list):
        return []
    clean = [item.strip() for item in value
             if isinstance(item, str) and not _observe_unspecified(item)]
    return list(dict.fromkeys(clean))


def _observed_blueprint_issues(blueprint: dict, observed: dict) -> list[str]:
    """检查蓝图是否完整且原样承接 few-shot 的字面字段硬事实。"""
    fields_by_name = {}
    for entity_type in blueprint.get("entity_types", []):
        for field in entity_type.get("fields") or []:
            fields_by_name.setdefault(field.get("name"), field)
    issues = []
    raw_fields = observed.get("observed_fields") if isinstance(observed, dict) else None
    if _observe_unspecified(raw_fields):
        raw_fields = []
    elif not isinstance(raw_fields, list):
        return ["observe.observed_fields 必须是 array"]
    for index, field in enumerate(raw_fields):
        if _observe_unspecified(field):
            continue
        if not isinstance(field, dict):
            issues.append(f"observe.observed_fields[{index}] 必须是 object")
            continue
        name = field.get("name")
        if _observe_unspecified(name):
            continue
        if not isinstance(name, str):
            issues.append(f"observe.observed_fields[{index}].name 必须是字符串")
            continue
        declared = fields_by_name.get(name)
        if declared is None:
            issues.append(f"未覆盖 few-shot 已观测字段:{name}")
            continue
        for attr in ("kind", "unit", "monotonic", "range"):
            value = field.get(attr)
            # observe 模型常把可选值输出成占位符；未知不是硬约束，半空值仍须 fail-closed。
            if not _observe_unspecified(value) and value != declared.get(attr):
                issues.append(
                    f"改写 few-shot 硬事实:{name}.{attr} observed={value!r},blueprint={declared.get(attr)!r}")
    return issues


def _separate_observed_relation_fields(candidate: dict, observed: dict) -> list[str]:
    """把 few-shot 展示字段与同名关系 FK 确定性拆成两条字段。

    LLM 容易在 ``category/person`` 硬事实与 ``reference`` 关系字段之间振荡。
    这里不改变关系端点或领域语义，只保留观察字段原形，并给使用它的关系创建
    独立 reference 字段；未绑定关系的误改 reference 则恢复观察 kind。
    返回修复说明用于 Run 留痕。
    """
    if not isinstance(candidate, dict):
        return []
    blueprint = candidate.get("world_blueprint", candidate)
    if not isinstance(blueprint, dict):
        return []
    raw_observed = observed.get("observed_fields") if isinstance(observed, dict) else None
    if not isinstance(raw_observed, list):
        return []
    observed_specs = {
        item.get("name"): item for item in raw_observed
        if isinstance(item, dict) and isinstance(item.get("name"), str)
        and item.get("name") and not _observe_unspecified(item.get("kind"))
    }
    if not observed_specs:
        return []

    types = {item.get("id"): item for item in blueprint.get("entity_types", [])
             if isinstance(item, dict) and isinstance(item.get("id"), str)}
    relations = [item for item in blueprint.get("relation_types", []) if isinstance(item, dict)]
    repairs: list[str] = []

    def _field(entity_type: dict, name: str):
        return next((item for item in entity_type.get("fields", [])
                     if isinstance(item, dict) and item.get("name") == name), None)

    def _fresh_reference_name(owner: dict, base: str) -> str:
        names = {item.get("name") for item in owner.get("fields", []) if isinstance(item, dict)}
        stem = f"{base}引用"
        if stem not in names:
            return stem
        index = 2
        while f"{stem}{index}" in names:
            index += 1
        return f"{stem}{index}"

    # 先处理确实把 observed 显示字段拿去当 relation.field 的关系。
    for relation in relations:
        old_name = relation.get("field")
        observed_spec = observed_specs.get(old_name)
        if not observed_spec:
            continue
        endpoints = [types.get(relation.get("from_type")), types.get(relation.get("to_type"))]
        owners = [entity_type for entity_type in endpoints
                  if entity_type is not None and _field(entity_type, old_name) is not None]
        if len(owners) != 1:
            continue
        owner = owners[0]
        owner_field = _field(owner, old_name)
        new_name = _fresh_reference_name(owner, old_name)
        owner["fields"].append({"name": new_name, "kind": "reference"})
        relation["field"] = new_name
        # 同名显示字段仍按 observe 硬事实保留，不能被关系改型。
        owner_field.update({key: deepcopy(value) for key, value in observed_spec.items()
                            if key in ("kind", "unit", "monotonic", "range")
                            and not _observe_unspecified(value)})
        repairs.append(f"{owner.get('id')}.{old_name}→显示字段 + {new_name}(relation={relation.get('id')})")

    # 再恢复没有关系绑定、却被模型误改成 reference 的 observed 同名字段。
    bound = {(entity_type.get("id"), relation.get("field"))
             for relation in relations for entity_type in types.values()
             if entity_type.get("id") in (relation.get("from_type"), relation.get("to_type"))
             and _field(entity_type, relation.get("field")) is not None}
    for type_id, entity_type in types.items():
        for field in entity_type.get("fields", []):
            spec = observed_specs.get(field.get("name")) if isinstance(field, dict) else None
            if not spec or (type_id, field.get("name")) in bound:
                continue
            for key in ("kind", "unit", "monotonic", "range"):
                value = spec.get(key)
                if not _observe_unspecified(value) and field.get(key) != value:
                    field[key] = deepcopy(value)
                    repairs.append(f"{type_id}.{field.get('name')}.{key} 恢复 observe={value!r}")
    return repairs


def _assemble_whitepaper(views: dict, desc: str) -> dict:
    """★综合 = 代码确定性装配；世界视角是必须通过机械校验的硬门。"""
    obs = views.get("observe") or {}
    skp = views.get("skeptic") or {}
    mp = views.get("map") or {}
    med = views.get("medium") or {}
    sty = views.get("style") or {}
    trp = views.get("traps") or {}
    world_review = deepcopy(views.get("world_review") or {})
    # 新白皮书不允许 world 视角失败后悄悄退化成 legacy 单类型；历史适配只在读取旧产物时启用。
    world_view = views.get("world") or {}
    blueprint = normalize_world_blueprint(world_view)
    type_specs = blueprint["entity_types"]
    primary = next(t for t in type_specs if t.get("primary"))

    obs_ents = _observed_strings(obs.get("observed_entities"))
    observed_media = _observed_strings(obs.get("observed_media"))
    # blueprint 是字段/主体单一真源；observe 仅留 provenance，不再决定世界拓扑。
    entity_noun = primary["noun"]
    fields_by_name = {}
    for t in type_specs:
        for f in t.get("fields") or []:
            fields_by_name.setdefault(f.get("name"), deepcopy(f))
    observed_conflicts = _observed_blueprint_issues(blueprint, obs)
    if observed_conflicts:
        raise WorldBlueprintError(
            "world_blueprint 未承接 few-shot 硬事实:\n- " + "\n- ".join(observed_conflicts))
    fields = list(fields_by_name.values())

    # L4 只能映射到世界本来就存在的字段，不能在蓝图之后另造一条“偏好时间线”。
    preference_axis = deepcopy(mp.get("preference_axis")) if isinstance(mp.get("preference_axis"), dict) else None
    if preference_axis:
        axis_field = str(preference_axis.get("field") or "").strip()
        owners = [t["id"] for t in type_specs
                  if any(f.get("name") == axis_field for f in (t.get("fields") or []))]
        requested_owner = preference_axis.get("entity_type")
        owner = requested_owner if requested_owner in owners else (owners[0] if len(owners) == 1 else None)
        if not owner:
            preference_axis = None
        else:
            preference_axis["entity_type"] = owner
    blueprint["evidence_channels"] = list(dict.fromkeys(
        [x for x in blueprint.get("evidence_channels", []) if isinstance(x, str)]
        + observed_media))
    evidence = [x for x in blueprint.get("evidence_channels", []) if isinstance(x, str)]
    genres = list(dict.fromkeys(observed_media +
                                (med.get("recommended_mix") or []) + evidence)) or ["记录"]
    observed_stopped = obs.get("stopped_phrase_seen")
    stopped = (observed_stopped.strip()
               if isinstance(observed_stopped, str) and not _observe_unspecified(observed_stopped)
               else "停止/失效")

    # 旧 schema 的关系镜像从可执行蓝图派生，不能再由 skeptic 的 prose 充当死元数据。
    relations = [{"type": r["id"], "from": r["from_type"], "to": r["to_type"],
                  "field": r["field"], "temporal": r.get("temporal", False)}
                 for r in blueprint.get("relation_types", [])]

    # 激活产线:映射 applicable + 陷阱 boost(按 L<n> 前缀匹配)
    boost = {(t.get("boost_line") or "")[:2] for t in (trp.get("traps") or [])}
    active = []
    for pl in (mp.get("per_line") or []):
        if pl.get("applicable") and pl.get("line"):
            if str(pl.get("line")).lower().startswith("l4") and not preference_axis:
                continue
            w = float(pl.get("weight_hint") or 0.3)
            if pl["line"][:2] in boost:
                w = min(1.0, w + 0.1)
            active.append({"line": pl["line"], "weight": round(w, 2), "why": pl.get("instantiation", "")})
    if not active:
        active = [{"line": "L1_timeline", "weight": 0.5, "why": "兜底(映射视角未产出 applicable)"}]

    temporal = blueprint["temporal_model"]
    n_ent = sum(t["count"] for t in type_specs)
    derived_sms = [{"field": f["name"], "states": f["states"]}
                   for t in type_specs for f in t.get("fields", [])
                   if f.get("name") and isinstance(f.get("states"), list) and f.get("states")]
    state_machines = derived_sms                 # typed 生命周期只由 blueprint.fields[].states 决定
    return {
        "scenario_id": "auto",
        "domain_profile": {"entity_noun": entity_noun, "field_schema": fields,
                           "doc_genres": genres[:6], "stopped_phrase": stopped,
                           "preference_axis": preference_axis,       # L4 只引用 blueprint 中唯一归属的真实字段
                           "state_machines": state_machines},     # ★C1③ 单向状态序声明(议会出;validate 据此查 illegal_transition,只查声明字段)
        "world_blueprint": blueprint,
        # 审议记录与可执行契约并列留在白皮书中，方便人工先审“世界骨子”，再看能力映射。
        "world_review": world_review,
        "medium": {"type": "documents", "genres": genres[:6], "cadence": temporal["cadence"],
                   "time_unit": temporal["unit"],
                   "candidates": (med.get("common_media") or []) + [m.get("form") for m in (med.get("unconventional_media") or [])]},
        "active_lines": active,
        "shared_world_spec": {"entities": {"count": n_ent},
                              "timeline": {"n_sessions": temporal["n_sessions"],
                                           "unit": temporal["unit"], "cadence": temporal["cadence"],
                                           "step_days": temporal.get("step_days", 7),
                                           "change_density": "按领域事件节律铺满全程"},
                              "relations": relations},
        "capability_targets": {"total_q": 200},
        "style_spec": sty.get("style_spec"),
        "use_fewshot_as_exemplar": sty.get("use_fewshot_as_exemplar", True),
        "traps": trp.get("traps"),
        "line_mapping": mp.get("per_line"),
        "provenance": {"observed_entities": obs_ents,
                       "inferred_entities": [e.get("type") for e in (skp.get("latent_entities") or [])]},
    }


def _canonicalize_lines(wp: dict, draft: dict, log=print):
    """★C4 议会选线稳定化(014559 根因:critic 全文重写无 id 词表约束 + 采纳闸只查 truthy →
    legal run 丢 L7、6 条全自编名,权重经前缀兜底错挂)。代码侧三件事,prompt 铁律只是软约束、这里才是硬闸:
      ① critic 的 active_lines 逐条过 line_for 锁回 canonical id(认不出的丢弃并记日志);
      ② active_lines 的集合锁定为 draft(= map applicable==true 集)：critic 不许删线，也不许把
         line_mapping 中的“不适用/weight=0”条目重新塞回 active_lines；critic 只可润色正权重和 why；
      ③ domain_profile 的 preference_axis / state_machines 若被 critic 重写丢掉,从 draft 回填(同款丢失防护)。"""
    from pipeline.lines import line_for
    draft_ids = {
        ln.id for item in (draft.get("active_lines") or [])
        if (ln := line_for(item.get("line", ""))) is not None
    }
    fixed, seen, renamed, dropped = [], set(), [], []
    for l in (wp.get("active_lines") or []):
        ln = line_for(l.get("line", ""))
        if ln is None:
            dropped.append(l.get("line")); continue
        if ln.id not in draft_ids or float(l.get("weight") or 0) <= 0:
            dropped.append(l.get("line")); continue
        if ln.id in seen:
            continue
        if ln.id != l.get("line"):
            renamed.append(f"{l.get('line')}→{ln.id}")
        seen.add(ln.id); fixed.append({**l, "line": ln.id})
    backfilled = []
    for dl in (draft.get("active_lines") or []):          # draft 的 line 来自 map(canonical),仍过一遍 line_for 兜底
        ln = line_for(dl.get("line", ""))
        if ln and ln.id not in seen:
            seen.add(ln.id); fixed.append({**dl, "line": ln.id}); backfilled.append(ln.id)
    wp["active_lines"] = fixed
    dp, ddp = wp.get("domain_profile") or {}, draft.get("domain_profile") or {}
    for k in ("preference_axis", "state_machines"):
        if not dp.get(k) and ddp.get(k):
            dp[k] = ddp[k]; backfilled.append(f"domain_profile.{k}")
    # ★字段级约束【draft 为唯一真源·直接覆盖】(value_shape 两轮审计 HIGH):
    #   字段的 unit/monotonic/range 来自 observe 据实抽取(draft),critic prompt 铁律本就【不得删改】它们。
    #   旧版只在 final【缺失】时补缺 → 只治了 critic"删",没治"改":critic 把 monotonic up→down 原样放行,
    #   validate 反向强制累计字段只减不增 = 主动制造逻辑不可能 gold(比"删"更毒)。故直接以 draft 覆盖
    #   (draft 没声明的不动 → 保留 critic 在 observe 沉默处的合法补充)。range 拷贝防共享可变对象。
    draft_attrs = {(f.get("name") or "").strip(): f for f in (ddp.get("field_schema") or []) if f.get("name")}
    fixed_attr, ghost_attr = 0, []
    for f in dp.get("field_schema") or []:
        src = draft_attrs.get((f.get("name") or "").strip())
        if not src:
            continue
        for k in ("unit", "monotonic", "range"):
            if src.get(k) is not None and src.get(k) != f.get(k):
                f[k] = list(src[k]) if isinstance(src[k], list) else src[k]; fixed_attr += 1
    # ★改名漏接检测(审计·中):critic 把 draft 里带约束的字段改了名 → 名不命中、约束丢失且 validate 停查 → 告警(无自动回填,留痕给人查议会一致性)
    final_names = {(f.get("name") or "").strip() for f in (dp.get("field_schema") or [])}
    ghost_attr = [n for n, sf in draft_attrs.items()
                  if n not in final_names and any(sf.get(k) is not None for k in ("unit", "monotonic", "range"))]
    if fixed_attr:
        backfilled.append(f"字段约束以draft覆盖×{fixed_attr}")
    if ghost_attr:
        log(f"    ⚠议会·critic 改名致字段约束丢失(unit/mono/range 停查):{ghost_attr}——查议会命名一致性")
    wp["domain_profile"] = dp
    if draft.get("world_blueprint"):
        # ★世界骨架是独立议会已校验的契约，critic 只能评论，不能删除或重写；兼容镜像也锁回同一真源。
        wp["world_blueprint"] = deepcopy(draft["world_blueprint"])
        # domain_profile / medium / shared_world_spec 都只是 blueprint 的兼容投影；整块锁回草案，
        # 防止最终能力 critic 绕过 blueprint，暗改主体、证据渠道、时间制度或偏好字段。
        wp["domain_profile"] = deepcopy(draft.get("domain_profile") or {})
        wp["medium"] = deepcopy(draft.get("medium") or {})
        wp["shared_world_spec"] = deepcopy(draft.get("shared_world_spec") or {})
        normalize_world_blueprint(wp)  # 最终再走一次硬校验，防未来 canonical 逻辑破坏引用闭包。
    if "world_review" in draft:
        # 最终能力/文风 critic 无权覆写或删除先于能力映射完成的世界审议记录。
        wp["world_review"] = deepcopy(draft["world_review"])
    if renamed or dropped or backfilled:
        log(f"    议会·canonical 化:改名 {renamed or '无'} / 丢弃不可识别 {dropped or '无'} / 补漏 {backfilled or '无'}")


def central_office(desc, few_shot, tracer, log=print) -> dict:
    """先冻结世界骨架，再做能力映射，最后代码装配并批判白皮书。"""
    fs = json.dumps(few_shot, ensure_ascii=False)
    def _view(p):                                         # 7 视角彼此独立 → 并发
        key, sysp, ask = p
        out = tracer.chat_json(f"council.{key}",
            [{"role": "system", "content": sysp},
             {"role": "user", "content": render("council.view_user", desc=desc, fs=fs, ask=ask)}],
            temperature=0.6, max_tokens=4096)
        ok = isinstance(out, dict) and "__error__" not in out
        log(f"    议会·{key} {'✓' if ok else '⚠失败'}")
        return key, (out if isinstance(out, dict) else {})
    foundations = [p for p in _PERSPECTIVES if p[0] not in ("map", "world")]
    views = dict(config.pmap(_view, foundations, workers=len(foundations)))
    # 架构师先读 observe 的硬事实与 skeptic/medium 的补全意见，再综合世界；不是并行独白。
    world_ask = ("综合下面的议会前置材料，先设计领域世界骨架。observe 是 few-shot 硬事实，不得改写；"
                 "skeptic 只作带置信度的候选，需自行裁决；medium 用来校准证据生态。\n"
                 + json.dumps({k: views.get(k) for k in ("observe", "skeptic", "medium")}, ensure_ascii=False))
    # 架构师输出先过机械硬门；把明确错误与完整 observe 冻结清单共同回喂，避免修一处忘一处。
    world_out: dict = {}
    world_error = ""
    observed_contract = json.dumps(
        {"observed_fields": (views.get("observe") or {}).get("observed_fields") or [],
         "observed_media": (views.get("observe") or {}).get("observed_media") or []},
        ensure_ascii=False,
    )
    for world_attempt in range(1, 7):
        first_pass = world_attempt == 1
        system = WORLD_SYS if first_pass else WORLD_REPAIR_SYS
        user = (render("council.view_user", desc=desc, fs=fs, ask=world_ask)
                if first_pass else render(
                    "council.world_repair_user", errors=world_error,
                    candidate=json.dumps(world_out, ensure_ascii=False))
                    + f"\n【每轮都必须完整保留的 observe 冻结清单】\n{observed_contract}")
        candidate = tracer.chat_json("council.world" if first_pass else "council.world_repair",
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.5 if world_attempt == 1 else 0.2, max_tokens=8192)
        world_out = candidate if isinstance(candidate, dict) else {}
        relation_repairs = _separate_observed_relation_fields(world_out, views.get("observe") or {})
        if relation_repairs:
            log(f"    议会·world 关系/展示字段机械拆分:{relation_repairs}")
        try:
            blueprint = normalize_world_blueprint(world_out)
        except WorldBlueprintError as error:
            world_error = str(error)
            log(f"    议会·world 第{world_attempt}轮未通过机械校验:{world_error}")
            continue
        observed_issues = _observed_blueprint_issues(blueprint, views.get("observe") or {})
        if observed_issues:
            world_error = "world_blueprint 未承接 few-shot 硬事实:\n- " + "\n- ".join(observed_issues)
            log(f"    议会·world 第{world_attempt}轮未通过观察闭包:{world_error}")
            continue
        log(f"    议会·world ✓(综合前置材料;第{world_attempt}轮)")
        break
    else:
        raise WorldBlueprintError("world 架构师六轮后仍未通过机械/观察校验:" + world_error)
    # world 是能力映射的前置条件：先机械验骨架，再让 map 只判断哪些能力天然可读。
    views["world"] = {"world_blueprint": deepcopy(blueprint)}
    world_draft = deepcopy(views["world"])
    review_record: dict = {}
    review_candidate = blueprint
    review_error = "反方未返回结果"
    for review_attempt in range(1, 6):
        review = tracer.chat_json("council.world_review",
            [{"role": "system", "content": WORLD_REVIEW_SYS},
             {"role": "user", "content": render(
                 "council.world_review_user", desc=desc, fs=fs,
                 candidate=json.dumps(review_candidate, ensure_ascii=False))
                 + (f"\n【上轮未获批准】{review_error}\n请继续修订，勿降低为口头辩解。"
                    if review_attempt > 1 else "")}],
            temperature=0.3, max_tokens=8192)
        relation_repairs = _separate_observed_relation_fields(review, views.get("observe") or {})
        if relation_repairs:
            log(f"    议会·world_review 关系/展示字段机械拆分:{relation_repairs}")
        review_record = (deepcopy(review.get("review"))
                         if isinstance(review, dict) and isinstance(review.get("review"), dict) else {})
        review_problems: list[str] = []
        schema_repaired = False
        try:
            reviewed_blueprint = normalize_world_blueprint(review if isinstance(review, dict) else {})
        except WorldBlueprintError as error:
            # 语义反方容易在重写 JSON 时制造纯 schema 错误；交给独立修理员修引用闭包，
            # 不让反方一边讨论领域一边猜校验规则。
            repair = tracer.chat_json("council.world_repair",
                [{"role": "system", "content": WORLD_REPAIR_SYS},
                 {"role": "user", "content": render(
                     "council.world_repair_user", errors=str(error),
                     candidate=json.dumps(review if isinstance(review, dict) else {}, ensure_ascii=False))}],
                temperature=0.1, max_tokens=8192)
            relation_repairs = _separate_observed_relation_fields(repair, views.get("observe") or {})
            if relation_repairs:
                log(f"    议会·world_repair 关系/展示字段机械拆分:{relation_repairs}")
            try:
                reviewed_blueprint = normalize_world_blueprint(repair if isinstance(repair, dict) else {})
            except WorldBlueprintError as repair_error:
                reviewed_blueprint = None
                review_problems.append(f"blueprint 修理后仍未通过机械校验:{repair_error}")
            else:
                review_record["schema_repaired"] = True
                schema_repaired = True
        if reviewed_blueprint is not None:
            review_problems.extend(
                _observed_blueprint_issues(reviewed_blueprint, views.get("observe") or {}))
        if schema_repaired:
            # 修理员只保证 JSON 契约；其输出必须在下一轮重新接受领域/换皮评审。
            review_problems.append("schema 修理后的 blueprint 必须重新经过反方评审")
        risk = str(review_record.get("reskin_risk") or "").strip().lower()
        if risk != "low":
            review_problems.append(f"修订后 residual reskin_risk 必须为 low，当前={risk or 'missing'}")
        for key in ("findings", "decisions"):
            values = review_record.get(key)
            if not isinstance(values, list) or not any(isinstance(x, str) and x.strip() for x in values):
                review_problems.append(f"review.{key} 必须保留至少一条非空审议记录")
        if not review_problems and reviewed_blueprint is not None:
            blueprint = reviewed_blueprint
            views["world"] = {"world_blueprint": deepcopy(blueprint)}
            review_record.update({"mechanical_outcome": "accepted", "attempts": review_attempt})
            log("    议会·world_review ✓(换皮/本体/动力学/拓扑/时间/证据复核)")
            break
        if reviewed_blueprint is not None:
            review_candidate = reviewed_blueprint
        review_error = "; ".join(review_problems)
        log(f"    议会·world_review 第{review_attempt}轮未批准:{review_error}")
    else:
        raise WorldBlueprintError("world_review 五轮后仍未批准，停止能力映射:" + review_error)
    views["world_review"] = review_record
    views["world_draft"] = world_draft
    map_ask = ("基于下面这份【已经冻结并通过机械校验的 world_blueprint】做能力映射。"
               "只能引用其中已有的实体类型、字段、关系和事件；不准为了激活某条产线要求世界补结构。\n"
               + json.dumps(blueprint, ensure_ascii=False))
    map_out = tracer.chat_json("council.map",
        [{"role": "system", "content": MAP_SYS},
         {"role": "user", "content": render("council.view_user", desc=desc, fs=fs, ask=map_ask)}],
        temperature=0.6, max_tokens=4096)
    views["map"] = map_out if isinstance(map_out, dict) else {}
    log(f"    议会·map {'✓' if isinstance(map_out, dict) and '__error__' not in map_out else '⚠失败'}(world-first)")

    draft = _assemble_whitepaper(views, desc)             # ★代码确定性装配；world 视角非法则在这里明确失败
    log(f"    议会·综合(代码装配)✓ active_lines={[l['line'] for l in draft['active_lines']]}")

    # 可选 LLM 批判润色；失败/无效则用已通过 world blueprint 硬门的代码草案。
    crit = tracer.chat_json("council.critique",
        [{"role": "system", "content": CRITIC_SYS},
         {"role": "user", "content": render("council.critic_user", desc=desc, fs=fs,
                                            draft=json.dumps(draft, ensure_ascii=False))}],
        temperature=0.3, max_tokens=8192)
    wp = crit if (isinstance(crit, dict) and crit.get("active_lines") and crit.get("domain_profile")) else draft
    if wp is draft:
        log("    议会·批判失败/无效 → 用代码草案(已是有效白皮书)")
    else:
        log("    议会·批判润色 ✓")
        _canonicalize_lines(wp, draft, log)               # ★C4:critic 全文重写易丢线/自编名(014559 丢 L7)→ 锁回 canonical + 以 draft 闭包补漏
    # critic 失败走 draft 时同样确认骨架仍合法；任何失败都在白皮书阶段暴露。
    normalize_world_blueprint(wp)
    wp["_council_views"] = views        # 留痕:7 视角原始报告
    log(f"    议会·定稿;激活产线 {[l.get('line') for l in wp.get('active_lines', [])]}")
    return wp
