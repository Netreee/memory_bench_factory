"""
pipeline.closed_loop —— §S 闭环旋钮 driver(从 run_factory_v2 拆出,行为不变)。
反推世界规模/配额 → ①供给环 → 渲 → 接地 → ②实测纠偏。4 个纯决策函数 + build_to_target 循环控制。
★build_to_target 内部【惰性 import】factory 的 stage(断开 factory↔closed_loop 导入环);函数体逐字未改。
"""
from __future__ import annotations
import dataclasses, math
from pipeline.world_state import WorldState
from pipeline.lines import line_for
from pipeline.run import Run, _run_stage
from pipeline.targetspec import TargetSpec, invert_rate, WorldParams, DEFAULT_SLACK


def _orders_by_line(orders) -> dict:
    """{line_id: 该线产了多少 order}。"""
    out: dict = {}
    for o in orders:
        out[o.get("line", "?")] = out.get(o.get("line", "?"), 0) + 1
    return out


def _order_deficit(produced: dict, target_orders: dict, feasible: set) -> dict:
    """①环判据:可行线里【实质】短缺 {line: 全额缺多少}(短超容差才算)。不可行线不计(扩世界也没用)。
    ★容差 = max(1, round(配额×0.15)):配额本就是 floor/survival×slack 的【过度供给】,slack 就是用来吸收
    "12/13 这种噪声抖动"的——差 1 单 / <15% 不该触发重渲整个世界(实测:差 1 单曾引发 14→49 的 3.5× 重建)。
    真不够(短超容差)才记赤字去长世界;小缺口交给 over-provision slack + ②实测纠偏环 + fail-open 兜。"""
    out = {}
    for lid, tgt in target_orders.items():
        got = produced.get(lid, 0)
        if lid in feasible and got < tgt - max(1, round(tgt * 0.15)):
            out[lid] = tgt - got
    return out


def _grow_for_supply(params: WorldParams, deficit: dict) -> WorldParams:
    """①环成长:【实质】供不上才长世界,**按缺口比例温和补**实体(不再 ×1.4 一刀切)。夹 clamp。无赤字原样返回。
    粗率 ~0.4 单/实体(同 invert_rate 的 RATE_L3);缺口大才顺带加几周。"""
    from pipeline.targetspec import N_ENT_CLAMP, N_SESS_CLAMP
    if not deficit:
        return params
    short = sum(deficit.values())                                   # 实质短缺总单数
    add_ent = max(3, math.ceil(short / 0.4))                        # 按缺口比例补实体(0.4 单/实体粗率)
    n_ent = min(N_ENT_CLAMP[1], params.n_entities + add_ent)
    n_sess = min(N_SESS_CLAMP[1], params.n_sessions + 2)            # 实质赤字才到这里 → 顺带 +2 周(助 L1/L3 跨周供给)
    return dataclasses.replace(params, n_entities=n_ent, n_sessions=n_sess)


def _floor_status(by_line: dict, overall: dict, spec: TargetSpec, feasible: set):
    """②环判据。返回 (met, per_line_final{line:接地数}, growable[低于floor的可行线], permanent[有floor但不可行的线])。
    可行线低于 floor → 放大重渲也许能救(growable);不可行线 → 永久达不到(permanent,别空转)。
    met = 各线 floor 全满足(growable+permanent 皆空) 且 总数 ≥ min_questions。"""
    per_line_final = {lid: by_line.get(lid, {}).get("grounded", 0) for lid in spec.per_line_min}
    growable, permanent = [], []
    for lid, floor in spec.per_line_min.items():
        got = by_line.get(lid, {}).get("grounded", 0)
        if got >= floor:
            continue
        (growable if lid in feasible else permanent).append(f"{lid}:{got}/{floor}")
    total_ok = overall.get("grounded", 0) >= spec.min_questions
    met = (not growable) and (not permanent) and total_ok
    return met, per_line_final, growable, permanent


def build_to_target(run: Run, spec: TargetSpec, max_rounds: int = 2, order_subrounds: int = 2):
    """§S 闭环旋钮 v0。前置:input + whitepaper 已由 drive 跑完(本函数从 01_whitepaper.json 起,自管 world→grounding)。
    ★只管【循环控制】:反推规模/配额 → patch 白皮书+config → 直调【现有 stage 函数】(经 _run_stage)→ 读回产物判 floor。
      stage 体零复制(评审:避免第二条编排路径漂移);quota 经 run.config 喂给 stage_orders。
    一轮 = ①订单供给环(stage_world+stage_orders 多子轮长世界) → 出题 → 整轮重渲 → 接地 → ② floor 校验/纠偏。
    达标 MET 返回;耗尽 max_rounds 仍不达 → fail-open 标 UNMET(不抛错,题库照出,留痕 manifest.algo.met_status)。"""
    from pipeline.factory import (stage_world, stage_orders, stage_well_posed,
                                  stage_questions, stage_corpus, stage_grounding, ART)
    wp = run.read(ART["whitepaper"])
    canon_lines = [{**l, "line": line_for(l.get("line", "")).id}                  # ★白皮书线 id 变体 → 规范 id(与 by_line / 先验键对齐)
                   for l in wp.get("active_lines", []) if line_for(l.get("line", ""))]
    active = list(dict.fromkeys(l["line"] for l in canon_lines))                  # 已建且去重的激活线
    if not spec.per_line_min:                                                     # 没显式给 → 白皮书 weight 派生(weight 终于被读)
        spec.per_line_min = spec.derive_per_line_min(canon_lines)
    plm: dict = {}                                                                # 显式 floor 的 key 也过 line_for 规范化(防 --per-line L1=30 被静默丢)
    for k, v in spec.per_line_min.items():
        ln = line_for(k)
        if ln and ln.id in active:
            plm[ln.id] = v
    spec.per_line_min = plm                                                       # 只对【已建激活线】设 floor
    s = sum(spec.per_line_min.values())                                           # ★总下限盈余无处落 → 按比例摊进各线 floor
    if 0 < s < spec.min_questions:                                                #   (使「逐线 floor 全达 ⟹ 总数达标」,消掉"总数单独差"的歧义态)
        scale = spec.min_questions / s
        spec.per_line_min = {k: math.ceil(v * scale) for k, v in spec.per_line_min.items()}
    params = invert_rate(spec)
    profile = wp.get("domain_profile", {})
    run.log(f"╔═ 闭环旋钮 build_to_target:min_q={spec.min_questions} per_line_min={spec.per_line_min}")
    run.log(f"║  invert_rate(保守先验,slack={DEFAULT_SLACK}) → n_ent={params.n_entities} n_sess={params.n_sessions} "
            f"target_orders={params.target_orders}")

    last = {"kept": [], "report": {}, "growable": [], "permanent": []}
    met = False
    config = run.manifest["config"]
    prev_entities = None                                                         # round1 世界实体集,作 ②环增量续渲的 delta 基线
    for rnd in range(1, max_rounds + 1):
        run.log(f"╠═ 第 {rnd}/{max_rounds} 轮 ══════════════════════════════")
        sw = wp.setdefault("shared_world_spec", {})                              # patch 规模旋钮(域值仍只从白皮书/world 来)
        sw.setdefault("entities", {})["count"] = params.n_entities
        sw.setdefault("timeline", {})["n_sessions"] = params.n_sessions
        wp.setdefault("domain_profile", {})["l5_max_conflicts"] = params.max_n_conflicts
        run.write(ART["whitepaper"], wp)
        config["quotas"] = dict(params.target_orders)                            # ★经 config 喂配额给 stage_orders(不内联 run_lines)
        config["augment"] = (rnd > 1)                                            # ★rnd>1:stage_world 增量 augment(在既有世界上长新实体,§10.1)
        run._save_manifest()

        # ── ① 订单供给环:直调 stage_world + stage_orders(便宜,绝不渲);供不上 → 长世界重跑 ──
        feasible: set = set()
        for sub in range(1, order_subrounds + 1):
            _run_stage(run, "world", stage_world, ART["world"])
            _run_stage(run, "orders", stage_orders, ART["orders"])
            _run_stage(run, "well_posed", stage_well_posed, "03_well_posed_report.json")  # ★边A闸:赤字按【良定义后】供给算
            ws = WorldState.from_dict(run.read(ART["world"]))
            feasible = {ln.id for a in active if (ln := line_for(a)) and ln.feasible(ws, profile)[0]}
            produced = _orders_by_line(run.read(ART["orders"]))   # 此时 03_orders 已是过闸(良定义)子集
            deficit = _order_deficit(produced, params.target_orders, feasible)
            run.log(f"║  ①供给子轮{sub}:实产 {produced} / 配额 {params.target_orders} → 赤字 {deficit or '无'}")
            if not deficit or sub == order_subrounds:
                break
            params = _grow_for_supply(params, deficit)                           # 供不上 → 长世界重建
            sw["entities"]["count"] = params.n_entities
            sw["timeline"]["n_sessions"] = params.n_sessions
            run.write(ART["whitepaper"], wp)
            run.log(f"║  ↑供给不足 → 长世界 n_ent={params.n_entities} n_sess={params.n_sessions} 重建")

        # ── 出题 → 渲染(round1 全量;round2+ ★增量 delta:只渲新实体、旧 docs 原样保留,§10.1)→ 接地 ──
        _run_stage(run, "questions", stage_questions, ART["questions"])
        ws = WorldState.from_dict(run.read(ART["world"]))
        if rnd == 1:
            prev_entities = set(ws.entities)
            (run.dir / ART["corpus"]).unlink(missing_ok=True)                    # 全量:清残留 ckpt 从头
            _run_stage(run, "corpus", stage_corpus, ART["corpus"])
        else:
            new_ents = sorted(set(ws.entities) - prev_entities)                  # ★只渲【新实体】,旧实体 docs 不重渲
            config["render_only"] = new_ents
            run.log(f"║  增量续渲:+{len(new_ents)} 新实体(旧 {len(prev_entities)} 实体 docs 不动,省整轮重渲)")
            _run_stage(run, "corpus", stage_corpus, ART["corpus"])
            config.pop("render_only", None)
            prev_entities = set(ws.entities)
        _run_stage(run, "grounding", stage_grounding, ART["grounding"])

        # ── ② floor 校验(读回 stage 产物判定;driver 只做循环决策)──
        report = run.read("06_grounding_report.json")
        met, per_line_final, growable, permanent = _floor_status(report["by_line"], report["overall"], spec, feasible)
        last = {"kept": run.read(ART["grounding"]), "report": report, "growable": growable, "permanent": permanent}
        o = report["overall"]
        run.log(f"║  ②接地后:总 {o['grounded']}/{spec.min_questions}  逐线 {per_line_final}")
        run.log(f"║  达标={met}  待长(可行未达){growable or '无'}  永久(不可行){permanent or '无'}")
        run.set_algo(targetspec={"min_questions": spec.min_questions, "per_line_min": spec.per_line_min,
                                 "haystack_ratio": spec.haystack_ratio},
                     per_line_final=per_line_final, met_status="MET" if met else f"round{rnd}_unmet")
        if met:
            run.log(f"╚═ ✓ 旋钮达标(MET):总 {o['grounded']}≥{spec.min_questions},各线 floor 均满足。")
            break
        if permanent:                                                            # floor 落在【不可行线】→ 永远达不到,长世界也救不了,别空转
            run.log(f"╚═ ⚠ floor 落在不可行线{permanent}(基质供不出)→ 无解,fail-open(不耗剩余轮次)。")
            break
        if rnd < max_rounds:                                                     # ② 用实测 survival 放大(只增不减)
            measured = {lid: v["survival"] for lid, v in report["by_line"].items() if v.get("survival") is not None}
            params = invert_rate(spec, survival=measured, slack=DEFAULT_SLACK * 1.3)
            params = dataclasses.replace(params, n_entities=max(params.n_entities, len(ws.entities)),
                                         n_sessions=ws.n_sessions)               # ★augment 只长实体不长周(周变了旧 docs 就失效,失去增量意义)
            run.log(f"║  ②实测 survival={measured} → 重算 target_orders={params.target_orders} "
                    f"n_ent={params.n_entities} n_sess={params.n_sessions}(下一轮增量续渲:只长新实体)")

    if not met:
        unmet = (last["growable"] + last["permanent"]) or ["总数未达 min_questions"]
        run.set_algo(met_status=f"UNMET: {unmet}")
        run.log(f"╚═ ✗ 旋钮未达标(UNMET,已尽 {max_rounds} 轮):{unmet}。题库仍写出(fail-open,留痕 met_status)。")
    run.manifest["current_stage"] = ""
    config.pop("augment", None); config.pop("render_only", None)                 # ★清增量信号,免泄漏到后续 --only 重跑
    run.set_status("done")
    return last["kept"], ("MET" if met else "UNMET")

