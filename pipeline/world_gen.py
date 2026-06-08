"""
pipeline.world_gen —— §5 共享世界生成(从 run_factory_v2 拆出,行为不变)。
build_world:并发批次让 LLM 填世界表 → assemble_world 成状态机 → W.3 CRITIC 修复轮(validate 缺陷定向重生成)。
"""
from __future__ import annotations
import json
import config
from pipeline.world_state import assemble_world, validate, WorldState, _strip_disambig, name_collisions
from pipeline.prompts import render


def _world_system(profile: dict) -> str:
    noun = profile.get("entity_noun", "实体")
    fields = profile.get("field_schema", [])
    fdesc = "、".join(f"{f['name']}({f.get('kind')})" for f in fields) or "若干随时间演化字段"
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
    ws, _ = assemble_world(merged)
    # ★W.3 CRITIC 修复轮:assemble 算出的缺陷不再"只 log 就扔"——定向重生成坏字段(复用并行骨架:发散批次→收敛修复)
    ent_idx = {e.get("name"): e for e in merged["entities"]}
    for rep in range(3):
        defects = validate(ws, merged)
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
    rem = validate(ws, merged)
    coll = name_collisions(ws)                        # ★Fix3:表面塌缩兜底检测(收集期已按主干去重,这里抓漏网)
    log(f"  ✓ 基础世界:{len(ws.entities)} 实体 / {ws.n_sessions} 周 / 修复后残留缺陷 {len(rem)}"
        + (f" / ⚠表面塌缩近重名 {coll}" if coll else ""))  # 产线基质由 stage_world 的 line.prepare() 叠加
    return ws

