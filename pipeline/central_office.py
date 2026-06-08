"""
pipeline.central_office —— 中央办公室【并行议会】版:场景+few-shot → 白皮书。

redesign_factory_v2.md §3 的升级:从"单轮提案+批判"→ 6 个【议会视角】并行 → 综合 → 批判。
视角(都不叫 L、不叫宪法,避免与产线 L1–L7 / 元架构宪法撞名):
  观测  observe  : few-shot 字面给了啥(只抽取、不推断)
  怀疑  skeptic  : 假定 few-shot 系统性缺失,阐发缺了啥(带合理性缰绳 + observed/inferred 标注)
  映射  map      : 对照宪法 L1–L7,本场景每条该不该激活、怎么落地
  媒介  medium   : 本场景还能是什么形式(先穷尽常见、再补反常但合理 —— 大而全,非猎奇)
  文风  style    : 抽 few-shot 风格 DNA + 原文当渲染范例
  陷阱  traps    : 本场景天然在哪坑记忆系统(接 M1–M6,设计区分度)
→ 综合成白皮书草案 → 批判(覆盖/自洽/gt可行/区分度)→ 定稿。

白皮书 schema 与下游(build_world/run_lines/render)兼容:必出 domain_profile / medium /
active_lines / shared_world_spec / capability_targets;另附 style_spec / traps / line_mapping /
observed_vs_inferred(留痕,后续渲染/诊断用)。
"""
from __future__ import annotations
import json
import config
from pipeline.lines import taxonomy_prose
from pipeline.prompts import render          # ★议会 prompts 收编进注册表(council.*)

# ── 6 个议会视角的 system prompt ──────────────────────────────────────────
OBSERVE_SYS = render("council.observe")
SKEPTIC_SYS = render("council.skeptic")
MAP_SYS = render("council.map", taxonomy=taxonomy_prose())   # ★map 内联宪法 taxonomy(调用侧算好传入)
MEDIUM_SYS = render("council.medium")
STYLE_SYS = render("council.style")
TRAPS_SYS = render("council.traps")

# ── 综合 + 批判 ──────────────────────────────────────────────────────────
CRITIC_SYS = render("council.critic")
# (SYNTH_SYS 已删:综合早已改为代码确定性装配 _assemble_whitepaper,该 prompt 不再被调用)

_PERSPECTIVES = [
    ("observe", OBSERVE_SYS, "据实抽取 few-shot 字面信息。"),
    ("skeptic", SKEPTIC_SYS, "阐发本场景 few-shot 没展示但真实存在的媒介/实体/关系(带缰绳)。"),
    ("map", MAP_SYS, "对照 L1–L7,逐条判断本场景该不该激活、怎么落地。"),
    ("medium", MEDIUM_SYS, "发散本场景所有可能形式(常见穷尽 + 反常但合理),大而全。"),
    ("style", STYLE_SYS, "抽 few-shot 风格 DNA。"),
    ("traps", TRAPS_SYS, "设计本场景坑记忆系统的陷阱(接区分度)。"),
]


def _g(d, *keys, default=None):
    cur = d if isinstance(d, dict) else {}
    for k in keys:
        cur = cur.get(k, {}) if isinstance(cur, dict) else {}
    return cur if cur not in ({}, None) else default


def _assemble_whitepaper(views: dict, desc: str) -> dict:
    """★综合 = 代码确定性装配(把 6 份结构化视角合成白皮书,治 LLM 大 prompt 吐空)。"""
    obs = views.get("observe") or {}
    skp = views.get("skeptic") or {}
    mp = views.get("map") or {}
    med = views.get("medium") or {}
    sty = views.get("style") or {}
    trp = views.get("traps") or {}

    obs_ents = obs.get("observed_entities") or []
    fields0 = obs.get("observed_fields") or []
    person_field_names = {f.get("name") for f in fields0 if f.get("kind") == "person"}
    # ★主体 = 观测视角点名的 main_entity_noun;兜底:第一个【非 person 字段名】的观测实体(防把"负责人"当主体)
    entity_noun = obs.get("main_entity_noun") or next(
        (e for e in obs_ents if e not in person_field_names), None) or (obs_ents[0] if obs_ents else "实体")
    fields = obs.get("observed_fields") or []
    genres = list(dict.fromkeys((obs.get("observed_media") or []) + (med.get("recommended_mix") or []))) or ["记录"]
    stopped = obs.get("stopped_phrase_seen") or "停止/失效"

    # 关系:怀疑视角 high/med 置信的(给 L2 多跳供料)
    relations = [{"type": r.get("type"), "from": r.get("from"), "to": r.get("to")}
                 for r in (skp.get("latent_relations") or [])
                 if r.get("type") and r.get("confidence") in ("high", "med")]

    # 激活产线:映射 applicable + 陷阱 boost(按 L<n> 前缀匹配)
    boost = {(t.get("boost_line") or "")[:2] for t in (trp.get("traps") or [])}
    active = []
    for pl in (mp.get("per_line") or []):
        if pl.get("applicable") and pl.get("line"):
            w = float(pl.get("weight_hint") or 0.3)
            if pl["line"][:2] in boost:
                w = min(1.0, w + 0.1)
            active.append({"line": pl["line"], "weight": round(w, 2), "why": pl.get("instantiation", "")})
    if not active:
        active = [{"line": "L1_timeline", "weight": 0.5, "why": "兜底(映射视角未产出 applicable)"}]

    n_ent = max(8, 2 * len(relations) + 6)
    return {
        "scenario_id": "auto",
        "domain_profile": {"entity_noun": entity_noun, "field_schema": fields,
                           "doc_genres": genres[:6], "stopped_phrase": stopped,
                           "preference_axis": mp.get("preference_axis")},   # ★L4 偏好基质(议会出,域无关;无则 None→L4 infeasible)
        "medium": {"type": "documents", "genres": genres[:6], "cadence": "weekly",
                   "candidates": (med.get("common_media") or []) + [m.get("form") for m in (med.get("unconventional_media") or [])]},
        "active_lines": active,
        "shared_world_spec": {"entities": {"count": n_ent},
                              "timeline": {"n_sessions": 10, "change_density": "每字段4-8次,铺满全程"},
                              "relations": relations},
        "capability_targets": {"total_q": 200},
        "style_spec": sty.get("style_spec"),
        "use_fewshot_as_exemplar": sty.get("use_fewshot_as_exemplar", True),
        "traps": trp.get("traps"),
        "line_mapping": mp.get("per_line"),
        "provenance": {"observed_entities": obs_ents,
                       "inferred_entities": [e.get("type") for e in (skp.get("latent_entities") or [])]},
    }


def central_office(desc, few_shot, tracer, log=print) -> dict:
    """并行议会(6 视角 LLM)→ 代码装配 → 可选 LLM 批判润色 → 白皮书。tracer 须有 .chat_json。"""
    fs = json.dumps(few_shot, ensure_ascii=False)
    def _view(p):                                         # 6 视角彼此独立 → 并发
        key, sysp, ask = p
        out = tracer.chat_json(f"council.{key}",
            [{"role": "system", "content": sysp},
             {"role": "user", "content": render("council.view_user", desc=desc, fs=fs, ask=ask)}],
            temperature=0.6, max_tokens=4096)
        ok = isinstance(out, dict) and "__error__" not in out
        log(f"    议会·{key} {'✓' if ok else '⚠失败'}")
        return key, (out if isinstance(out, dict) else {})
    views = dict(config.pmap(_view, _PERSPECTIVES, workers=6))

    draft = _assemble_whitepaper(views, desc)             # ★代码确定性装配,永远有效
    log(f"    议会·综合(代码装配)✓ active_lines={[l['line'] for l in draft['active_lines']]}")

    # 可选 LLM 批判润色;失败/无效则用代码草案(白皮书永远有效)
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
    wp["_council_views"] = views        # 留痕:6 视角原始报告
    log(f"    议会·定稿;激活产线 {[l.get('line') for l in wp.get('active_lines', [])]}")
    return wp
