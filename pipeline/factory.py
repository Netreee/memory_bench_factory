"""
pipeline.factory —— Benchmark 工厂【薄装配点】+ CLI(原 run_factory_v2,拆分后瘦身)。

数据流 = stage 序列(见 docs/anchors/run_system_design.md §1):
  input → whitepaper(议会) → world → orders → well_posed(边A闸) → questions → corpus → grounding(边B闸)
各 stage 的重逻辑分散在专门模块(world_gen / render / lines / well_posed / grounding / central_office /
closed_loop);此处只放:场景输入 + stage 薄包装 + STAGES 注册 + CLI。

用法:
  nohup ./venv/bin/python -u -m pipeline.factory --scenario office --target-mtokens 1.0 > /tmp/f.log 2>&1 &
  ./venv/bin/python -m pipeline.factory --scenario office --to world
  ./venv/bin/python -m pipeline.factory --min-questions 80 --per-line L2_relational=15   # 闭环旋钮
  ./venv/bin/python -m pipeline.factory --list-runs
"""
from __future__ import annotations
from pathlib import Path
import argparse, json, sys, time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.world_state import WorldState
from pipeline.lines import run_lines, prepare_lines as _prepare_lines
from pipeline.central_office import central_office
from pipeline.world_gen import build_world
from pipeline.render import render_corpus, phrase_questions
from pipeline.run import Run, Stage, drive, list_runs, latest_run_for, new_run_id, RUNS_DIR
from pipeline.targetspec import TargetSpec
from pipeline.closed_loop import build_to_target
from tools.diversity_metrics import report as diversity_report


SCENARIOS = {
    "office": {
        "description": (
            "评测记忆体在『公司多部门周报』场景的表现。跨业务线 leader 每周看多个部门(工程/数据/算法等)的"
            "周报,关注各部门 P0缺陷率、Oncall数量、负责人、汇报对象、项目状态等随周变化的字段,要以最新信息为准;"
            "负责人变动有时带动汇报关系变化,某些指标会停止统计。"),
        "few_shot": [
            {"title": "AI工程周报 W17", "content": "本周 P0缺陷率 20%,Oncall 10。负责人:张三。汇报对象:CTO。",
             "doc_type": "周报", "date": "2025-04-17"},
            {"title": "数据平台通报 W17", "content": "本周 SLA 99.1%,值班 8 人。负责人:王五。",
             "doc_type": "通报", "date": "2025-04-17"},
        ],
    },
    "medical": {   # ★跨场景区分度对照:词汇/结构都与 office 不同,仍有 2 个 person 字段喂 L2
        "description": (
            "评测记忆体在『慢病患者多次门诊病程跟踪』场景的表现。医生跨多次就诊追踪患者的"
            "血压、空腹血糖、用药方案、诊断结论、主治医师、会诊上级等随时间演化的字段;"
            "某些指标会停止监测,主治医师变更会带动会诊关系变化,要以最新一次就诊为准。"),
        "few_shot": [
            {"title": "门诊记录 0312", "content": "患者本次血压 150/95,空腹血糖 7.8,主治:李医生,会诊上级:王主任。予二甲双胍。",
             "doc_type": "门诊记录", "date": "2025-03-12"},
            {"title": "检查报告 0312", "content": "糖化血红蛋白 8.2%,诊断:2型糖尿病。主治:周医生。",
             "doc_type": "检查报告", "date": "2025-03-12"},
        ],
    },
}



ART = {"input": "00_input.json", "whitepaper": "01_whitepaper.json", "world": "02_world.json",
       "orders": "03_orders.json", "questions": "04_questions.json", "corpus": "05_corpus.json",
       "grounding": "06_grounded_questions.json"}

# ★作答协议(B类①修复):benchmark 出厂【显式声明】None 的两类语义 + 期望作答,治"None 未定义→理性系统被误判"。
#   契约层一处声明(非逐题补丁),所有 None 题共享;eval 侧据此把 gold 哨兵映射到人类作答。
ANSWER_PROTOCOL = {
    "version": 2,
    "rules": [
        "普通问题:答该项在【题面所指时点】的具体值。",
        "【截至最新一期】:锚点 = 该实体最后一次有效记录(不是全局最后一周),沿用其最近有效值(carry-forward)。"
        "全程在场的实体折到全局最后一期,早退场的实体折到它最后出现那一期——这是【同一条沿用规则】碰上不同寿命,不是两套口径。",
        "【从未涉及/不存在】:所问项在本场景根本没有(gold 标记 INSUFFICIENT,产线 ABS)→ 期望答『无此项/查无此记录』。",
        "【曾有但已显式停止统计】:所问项曾被跟踪、现已停更(gold forgotten=True,产线 FORGET)→ 期望答『已停止统计/不再跟踪』。",
        "【停统前的最后值】:若问的是『停止统计前最后一次』(产线 PREEXPIRE)→ 这是另一类问法,照常答停掉那一刻的值(非 None)。",
    ],
    "latest_means_carry_forward": True,     # ★"最新一期"=该实体末次有效记录沿用,统一口径(run160053 D 点的"双标"实为读者侧表面歧义,gold 本就单一真源 latest_valid)
    "two_none_types_distinguished": True,   # ★区分"从未存在"(ABS)vs"曾有已停"(FORGET)是考点
    "gold_sentinel_map": {"INSUFFICIENT": "无此项/查无此记录", "forgotten=true": "已停止统计/不再跟踪"},
}


def stage_input(run: Run):
    run.write(ART["input"], SCENARIOS[run.scenario])
    run.write("00_about.json", {"answer_protocol": ANSWER_PROTOCOL})   # ★出厂作答协议(随题库交付,eval 侧读)


def stage_whitepaper(run: Run):
    sc = run.read(ART["input"])
    wp = central_office(sc["description"], sc["few_shot"], run.tracer, run.log)
    run.write(ART["whitepaper"], wp)
    run.set_algo(active_lines=[l.get("line") for l in wp.get("active_lines", [])],
                 medium=wp.get("output_medium") or wp.get("domain_profile", {}).get("medium"))



def stage_world(run: Run):
    wp = run.read(ART["whitepaper"])
    existing = None
    if run.manifest["config"].get("augment") and run.has(ART["world"]):   # ★增量(§10.1):在既有世界上 augment 新实体,旧不动
        existing = WorldState.from_dict(run.read(ART["world"]))
    ws = build_world(wp, run.tracer, run.log, existing=existing)
    _prepare_lines(wp, ws, run.log)
    run.write(ART["world"], ws.to_dict())
    run.set_algo(entities=len(ws.entities), sessions=ws.n_sessions)


def stage_orders(run: Run):
    wp = run.read(ART["whitepaper"]); ws = WorldState.from_dict(run.read(ART["world"]))
    quotas = run.manifest["config"].get("quotas")           # ★闭环 driver 经 config 喂各线配额;普通 drive 路径无此键 → None → run_lines 回退 total_q(向后兼容)
    orders = run_lines(wp, ws, run.log, quotas=quotas)
    run.write(ART["orders"], orders)
    by_line: dict = {}
    for o in orders:
        by_line[o.get("line", "?")] = by_line.get(o.get("line", "?"), 0) + 1
    run.set_algo(orders=len(orders), orders_by_line=by_line)


def stage_well_posed(run: Run):
    """★边 A 闸(§V 良定义):出题【前】逐题验"gold 是题面在世界里的唯一正确解",ill-posed 即弃。
    纯代码、零 LLM、不碰 corpus。过闸 orders 覆写 03_orders(下游出题用),弃因逐条留 03_well_posed_report.json。
    源头修复后(week_label / L3 跳复现 / L5 排己)新鲜 order 应≈全过 → 本闸=兜底+防回归。"""
    from pipeline.well_posed import run_well_posed
    orders = run.read(ART["orders"]); ws = WorldState.from_dict(run.read(ART["world"]))
    kept, report = run_well_posed(orders, ws)
    run.write(ART["orders"], kept)                          # 过闸 orders 覆写(下游 stage_questions 只对良定义题出题)
    run.write("03_well_posed_report.json", report)
    o = report["overall"]
    run.set_algo(well_posed={"overall": o, "by_line": report["by_line"], "n_dropped": report["n_dropped"]})
    run.log(f"  ★边A闸(良定义):{o['well_posed']}/{o['n']} 良定义({(o['pass_rate'] or 0):.0%}),弃 {report['n_dropped']} "
            f"→ 过闸 orders 下游出题")


def stage_questions(run: Run):
    orders = run.read(ART["orders"]); wp = run.read(ART["whitepaper"])
    qs = phrase_questions(orders, wp, run.tracer, run.log)
    run.write(ART["questions"], qs)
    run.set_algo(questions=len(qs))


def stage_corpus(run: Run):
    wp = run.read(ART["whitepaper"]); ws = WorldState.from_dict(run.read(ART["world"]))
    ckpt = run.dir / ART["corpus"]
    if ckpt.exists():                                       # ★续渲:加载已完成周,接着跑(stage 内部 resume)
        st = json.loads(ckpt.read_text(encoding="utf-8"))
        corpus, done = st["corpus"], set(st["done_weeks"])
        run.log(f"  ↻ 续渲:已完成 {len(done)} 周")
    else:
        corpus, done = {"sessions": []}, set()
    target = int(run.manifest["config"].get("target_tokens", 1_000_000))

    def save():
        ckpt.write_text(json.dumps({"corpus": corpus, "done_weeks": sorted(done)}, ensure_ascii=False), encoding="utf-8")

    only = run.manifest["config"].get("render_only")        # ★增量(§10.1):只渲这些新实体、追加到已有周 docs
    render_corpus(wp, ws, target, run.tracer, corpus, done, save, run.log,
                  only_entities=set(only) if only else None)
    ch = sum(len(dd.get("content", "")) for x in corpus["sessions"] for dd in x["docs"])
    run.set_algo(docs=sum(len(x["docs"]) for x in corpus["sessions"]), chars=ch)

    # ★多样性硬指标(诊断附加项,非主链):测本次 corpus 全部文档正文,写进 manifest.algo.diversity。
    #   失败绝不拖垮整个 run —— 只 log 一句警告 + 跳过。
    try:
        texts = [dd.get("content", "") for x in corpus["sessions"] for dd in x["docs"] if dd.get("content")]
        if len(texts) >= 2:
            div = diversity_report(texts)
            run.set_algo(diversity=div)
            ndg = div.get("NDG_n-gram多样性(越高越好)", {}).get("point")
            ido = div.get("IDO_跨文档重叠(越低越好)", {}).get("point")
            cr = div.get("CR_gzip压缩比(越低越好)", {}).get("point")
            run.log(f"  ◆ 多样性:NDG={ndg} IDO={ido} CR={cr}(n_docs={div.get('n_docs')})")
        else:
            run.log(f"  ⓘ 多样性跳过:文档不足 2 篇({len(texts)})")
    except Exception as e:
        run.log(f"  ⚠ 多样性测量失败(已跳过,不影响 run):{e}")


def stage_grounding(run: Run):
    """★命门3 接地闸(§G):orders(gold)↔ corpus(语料)两支【汇合】,逐题验 gold 是否在证据文档
    【逐字 + 就近归属】可验,不接地即弃。纯代码、零 LLM。出厂题库 = 06_grounded_questions;
    存活率写进 manifest.algo.grounding,弃因逐条另存 06_grounding_report.json。"""
    from pipeline.grounding import run_grounding
    questions = run.read(ART["questions"])
    corpus_obj = run.read(ART["corpus"])
    kept, report = run_grounding(questions, corpus_obj)
    run.write(ART["grounding"], kept)
    run.write("06_grounding_report.json", report)
    o = report["overall"]
    run.set_algo(grounding={"overall": o, "by_line": report["by_line"],
                            "by_capability": report["by_capability"], "n_dropped": report["n_dropped"]})
    run.log(f"  ★接地闸:{o['grounded']}/{o['n']} 接地({(o['survival'] or 0):.0%}),弃 {report['n_dropped']} "
            f"→ 出厂题库 {ART['grounding']}")



STAGES = [
    Stage("input",      [],                       stage_input,      ART["input"]),
    Stage("whitepaper", ["input"],                stage_whitepaper, ART["whitepaper"]),
    Stage("world",      ["whitepaper"],           stage_world,      ART["world"]),
    Stage("orders",     ["whitepaper", "world"],  stage_orders,     ART["orders"]),
    Stage("well_posed", ["orders", "world"],      stage_well_posed, "03_well_posed_report.json"),  # ★边A闸:出题前剔 ill-posed(覆写 03_orders)
    Stage("questions",  ["orders"],               stage_questions,  ART["questions"]),
    Stage("corpus",     ["whitepaper", "world"],  stage_corpus,     ART["corpus"]),
    Stage("grounding",  ["questions", "corpus"],  stage_grounding,  ART["grounding"]),  # ★命门3:gold↔语料 汇合校验
]



def _print_runs():
    rows = list_runs()
    if not rows:
        print("(无 run;output/runs/ 为空)"); return
    print(f"{'run_id':<34} {'status':<8} {'tag':<10} active_lines / Q / docs")
    for m in rows:
        a = m.get("algo", {})
        print(f"{m['run_id']:<34} {m.get('status', '?'):<8} {str(m.get('tag') or '-'):<10} "
              f"{a.get('active_lines', '?')} / {a.get('questions', '?')} / {a.get('docs', '?')}")



def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="office")
    ap.add_argument("--target-mtokens", type=float, default=None, help="目标 token(M);新 run 缺省 1.0,续 run 沿用")
    ap.add_argument("--tag", default=None, help="人类标签(进 manifest,不影响 run_id)")
    ap.add_argument("--from", dest="from_stage", default=None, help=f"从哪个 stage 起跑 {list(ART)}")
    ap.add_argument("--to", dest="to_stage", default=None, help="跑到哪个 stage 止")
    ap.add_argument("--only", default=None, help="只跑某一个 stage")
    ap.add_argument("--force", action="store_true", help="重跑已完成的 stage")
    ap.add_argument("--run", dest="run_id", default=None, help="在已有 run 上继续/重跑(不新开)")
    ap.add_argument("--resume", action="store_true", help="[兼容]续跑该 scenario 最近一次 run")
    ap.add_argument("--list-runs", action="store_true", help="列出所有 run 后退出")
    # ── §S 闭环旋钮:给了 --min-questions 即走 build_to_target(反推世界规模/配额→①供给环→渲→接地→②纠偏) ──
    ap.add_argument("--min-questions", type=int, default=None, help="出厂题库总下限;给了即启用闭环旋钮(否则按原 drive 跑)")
    ap.add_argument("--per-line", action="append", default=None,
                    help="每线 floor,可重复:--per-line L1_timeline=30 --per-line L2_relational=15(缺省按白皮书 weight 派生)")
    ap.add_argument("--haystack-ratio", type=float, default=4.0, help="针:草比(v0 仅留痕,精配是 v2)")
    ap.add_argument("--time-span-weeks", type=int, default=None, help="时间跨度周数(None=反推/默认)")
    ap.add_argument("--max-rounds", type=int, default=2, help="②实测纠偏环最多整轮重渲次数(bounded)")
    a = ap.parse_args()

    if a.list_runs:
        _print_runs(); return

    if a.run_id:                                            # 在已有 run 上继续:scenario 取自其 manifest
        run_id, scenario = a.run_id, a.scenario
        mf = RUNS_DIR / a.run_id / "manifest.json"
        if mf.exists():
            scenario = json.loads(mf.read_text(encoding="utf-8")).get("scenario", a.scenario)
    elif a.resume:
        run_id = latest_run_for(a.scenario) or new_run_id(a.scenario); scenario = a.scenario
    else:
        run_id, scenario = new_run_id(a.scenario), a.scenario

    cfg = {"from": a.from_stage, "to": a.to_stage, "only": a.only}
    if a.target_mtokens is not None:
        cfg["target_tokens"] = int(a.target_mtokens * 1_000_000)
    run = Run(scenario, run_id, tag=a.tag, config_meta=cfg)
    run.manifest["config"].setdefault("target_tokens", 1_000_000)   # 新 run 缺省目标
    run._save_manifest()

    t0 = time.time()
    tgt = run.manifest["config"]["target_tokens"]
    run.log(f"=== run {run_id}(scenario={scenario},目标 {tgt/1e6:.1f}M token)===")

    if a.min_questions is not None:                         # ★闭环旋钮路径:先把 input+whitepaper 跑出来,再交给 driver 自管 world→grounding
        drive(run, STAGES, None, "whitepaper", None, a.force)
        plm: dict = {}
        for kv in (a.per_line or []):
            k, _, v = kv.partition("=")
            if v.strip().isdigit():
                plm[k.strip()] = int(v)
        spec = TargetSpec(min_questions=a.min_questions, per_line_min=plm,
                          haystack_ratio=a.haystack_ratio, time_span_weeks=a.time_span_weeks)
        _, status = build_to_target(run, spec, max_rounds=a.max_rounds)
        run.log(f"=== DONE {run_id}:闭环 {status} / {run.tracer.n} 次 LLM / {round((time.time() - t0) / 60, 1)} min / 留痕 {run.dir} ===")
        return

    drive(run, STAGES, a.from_stage, a.to_stage, a.only, a.force)
    run.log(f"=== DONE {run_id}:{run.tracer.n} 次 LLM / {round((time.time() - t0) / 60, 1)} min / 留痕 {run.dir} ===")


if __name__ == "__main__":
    main()
