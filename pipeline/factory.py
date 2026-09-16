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
import argparse, hashlib, json, sys, time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.world_state import WorldState
from pipeline.lines import run_lines, prepare_lines as _prepare_lines
from pipeline.central_office import central_office
from pipeline.world_gen import build_world
from pipeline.world_blueprint import WorldBlueprintError, normalize_world_blueprint, relation_capacity
from pipeline.render import render_corpus, phrase_questions
from pipeline.run import Run, Stage, drive, list_runs, latest_run_for, new_run_id, RUNS_DIR
from pipeline.targetspec import TargetSpec
from pipeline.closed_loop import build_to_target
from tools.diversity_metrics import report as diversity_report
from pipeline.seed_pack import SeedPackError
from pipeline.seed_run import (prepare_seed_input, seed_config, validate_seed_input,
                               validate_seed_identity)
from pipeline.seed_world import validate_seed_world


SCENARIOS = {
    "office": {
        "description": (
            "构造一个真实公司的运营世界：员工受雇并归属部门，部门控制预算，项目跨部门协作并由负责人承担，"
            "里程碑与项目依赖决定推进。世界由入职/离职/调岗/任命生效、预算申请/审批/调整、项目立项/延期/"
            "里程碑验收等事件驱动；人事通知、组织名册、预算审批单、项目周报分别观察这个世界的不同切面。"),
        "few_shot": [
            {"title": "AI工程周报 W17", "content": "本周 P0缺陷率 20%,Oncall 10。负责人:张三。汇报对象:CTO。",
             "doc_type": "周报", "date": "2025-04-17"},
            {"title": "数据平台通报 W17", "content": "本周 SLA 99.1%,值班 8 人。负责人:王五。",
             "doc_type": "通报", "date": "2025-04-17"},
        ],
    },
    "medical": {
        "description": (
            "构造一个慢病诊疗世界：患者经历不规则就诊，诊断、检验指标、处方和用药方案分属不同对象；临床医生"
            "负责诊疗，药物通过处方与患者关联。就诊、检验出结果、确诊/修订诊断、开药/停药、转诊等事件改变病程；"
            "门诊病历、检验报告、处方单和会诊记录存在不同时间戳与观察延迟。"),
        "few_shot": [
            {"title": "门诊记录 0312", "content": "患者本次血压 150/95,空腹血糖 7.8,主治:李医生,会诊上级:王主任。予二甲双胍。本次随访方式:门诊复诊。",
             "doc_type": "门诊记录", "date": "2025-03-12"},
            {"title": "检查报告 0312", "content": "糖化血红蛋白 8.2%,诊断:2型糖尿病。主治:周医生。",
             "doc_type": "检查报告", "date": "2025-03-12"},
        ],
    },
    "legal": {
        "description": (
            "构造一个律所案件世界：案件连接委托人、对手方、承办律师、合伙人、证据与法院程序；案件按立案、"
            "举证、庭审、裁判、执行或和解等生命周期推进。委托建立、律师改派、证据提交、开庭、裁判、和解与"
            "费用入账是领域事件；委托协议、法院文书、证据目录、庭审纪要和工时账单是不同权威层级的证据。"),
        "few_shot": [
            {"title": "案件评审纪要 M0312", "content": "本次评审:争议标的额 320万,累计计费工时 86,风险评分 4。主办律师:陈律,督导合伙人:周合伙人。本期处理策略倾向:庭外和解。",
             "doc_type": "评审纪要", "date": "2025-03-12"},
            {"title": "对手方备忘 M0312", "content": "对手方:鼎兴贸易;争议焦点:供货违约。主办律师:李律。",
             "doc_type": "对手方备忘", "date": "2025-03-12"},
        ],
    },

    # 六个领域输入只描述各自世界，不预埋“必须喂齐哪些能力线”的统一配方。
    "game": {
        "description": (
            "构造一个开放世界 RPG 的进程世界：玩家角色拥有等级与装备，任务指向地区或首领，首领有存活/击败状态"
            "并掉落装备，阵营控制地区且影响任务解锁。探索、接取任务、战斗、击败首领、掉落、拾取、装备、升级、"
            "完成任务与解锁区域组成核心循环；任务日志、战斗记录、战利品清单和角色面板是证据渠道，时间按章节/遭遇推进。"),
        "few_shot": [
            {"title": "第七章 · 剧情日志", "content": "灰袍贤者对玩家好感度升至 70,现效忠『银鹿议会』。当前任务:护送商队(进行中)。本章玩家抉择:谋略。",
             "doc_type": "剧情日志", "date": "2025-05-01"},
            {"title": "第七章 · 角色档案", "content": "黑剑佣兵等级 18,所属阵营『银鹿议会』,效忠领主:贤者。称玩家为『盟友』。",
             "doc_type": "角色档案", "date": "2025-05-01"},
        ],
    },
    "agent": {
        "description": (
            "构造一个长程 Agent 执行世界：一次 run 包含带依赖的任务 DAG，任务由执行体调度并调用工具，工具产生"
            "观测或 artifact，验证器决定产物是否可接受。调度、工具调用、超时/失败、重试、改路由、产物生成、验证"
            "通过与回滚是核心事件；执行 trace、工具日志、artifact 元数据和验证报告分别记录不同层级的事实，时间按执行轮推进。"),
        "few_shot": [
            {"title": "轮次 R12 · 执行轨迹", "content": "子任务『抓取财报』状态:成功。所用工具:web_fetch(成功率 0.82)。执行体:agent_A。本轮修复策略:更换工具。",
             "doc_type": "执行轨迹", "date": "2025-05-02"},
            {"title": "轮次 R12 · 工具调用记录", "content": "工具 sql_query 返回超时,重试 2 次。负责执行体:agent_B,上级编排器:planner_main。",
             "doc_type": "工具调用记录", "date": "2025-05-02"},
        ],
    },
    "cs": {
        "description": (
            "构造一个企业客户成功世界：客户账户包含联系人与合同，服务工单受 SLA 约束并由客服/支持队列承接，"
            "续约风险由未解决问题和承诺履行共同影响。工单创建、分派、升级、响应、解决/重开、责任转移、承诺兑现"
            "与续约是领域事件；CRM 纪要、工单流水、SLA 告警、合同与交接记录具有不同权威性和时效。"),
        "few_shot": [
            {"title": "服务周期 W19 · 工单记录", "content": "客户『云图科技』分层:战略级。满意度 4.6。负责客服:小林。当前工单:计费异常(处理中)。本次接触渠道:电话。",
             "doc_type": "工单记录", "date": "2025-05-09"},
            {"title": "服务周期 W19 · 交接备忘", "content": "客户『云图科技』承诺履约率 92%。负责客服:小林,客服主管:陈经理。",
             "doc_type": "交接备忘", "date": "2025-05-09"},
        ],
    },
    "companion": {
        "description": (
            "构造一个长期陪伴助手的受限记忆世界：用户与现实联系人、持续议题、承诺/纪念日、提醒和隐私边界是不同对象；"
            "一次互动可提出、澄清或撤回事实，议题可出现、缓解或解决，关系称谓与提醒会被更新。对话记录、用户明确更正、"
            "提醒确认和边界设置是不同证据渠道；敏感内容只保存允许的抽象边界，不把具体秘密变成普通字段。"),
        "few_shot": [
            {"title": "互动记录 0510", "content": "本次情绪基线:平稳。压力水平 3/10。主要倾诉对象:大学室友。当前关注议题:转岗准备。本次偏好支持方式:给出建议。",
             "doc_type": "互动记录", "date": "2025-05-10"},
            {"title": "事件备忘 0510", "content": "用户提及 6 月 2 日为其母亲生日(重要纪念日)。主要倾诉对象:大学室友,关系角色:挚友。",
             "doc_type": "事件备忘", "date": "2025-05-10"},
        ],
    },
    "assistant": {
        "description": (
            "构造一个个人生活助手世界：用户、家庭成员、住址/房间、设备、例程、订单、订阅和硬约束分别建模；"
            "设备加入/迁移/离线、例程创建/触发/覆盖、下单/退货、订阅续费/取消、过敏或禁区更新等事件改变世界。"
            "购物凭证、设备事件流、日历、订阅通知和用户明确指令是证据渠道，且账户、家庭成员与设备归属不能混淆。"),
        "few_shot": [
            {"title": "交互记录 0511", "content": "本次下单:厨房用品。月度预算 2000 元。常用账户:家庭主号。本次选购取向:性价比优先。已知过敏原:花生。",
             "doc_type": "交互记录", "date": "2025-05-11"},
            {"title": "设备设置 0511", "content": "智能家居主控设备:客厅音箱,所在房间场景:客厅。推荐命中率 0.74。",
             "doc_type": "设备设置", "date": "2025-05-11"},
        ],
    },
    "kb": {
        "description": (
            "构造一个企业知识治理世界：知识条目由版本构成，版本引用来源并可能依赖其他条目；维护人与领域 owner 承担"
            "审核责任，消费系统引用已发布版本。起草、评审、发布、替代、回滚、废弃、来源失效与依赖断裂是核心事件；"
            "条目正文、版本 diff、评审意见、发布记录、引用图和失效公告共同构成可观察证据，时间按发布批次推进。"),
        "few_shot": [
            {"title": "知识条目 KB-204 · v3", "content": "《退款流程规范》升级至 v3,有效状态:现行。维护人:老周。引用次数 1280,准确率 0.95。本次发布策略:全量发布。",
             "doc_type": "知识条目", "date": "2025-05-12"},
            {"title": "变更记录 KB-204", "content": "v2→v3 修订退款时限。维护人:老周,领域负责人:支付组组长。",
             "doc_type": "变更记录", "date": "2025-05-12"},
        ],
    },
}



ART = {"input": "00_input.json", "whitepaper": "01_whitepaper.json", "world": "02_world.json",
       "orders": "03_orders.json", "questions": "04_questions.json", "corpus": "05_corpus.json",
       "grounding": "06_grounded_questions.json"}
CORPUS_CKPT = "05_corpus.ckpt.json"
CORPUS_RENDER_CONTRACT_VERSION = 3

# ★作答协议(B类①修复):benchmark 出厂【显式声明】None 的两类语义 + 期望作答,治"None 未定义→理性系统被误判"。
#   契约层一处声明(非逐题补丁),所有 None 题共享;eval 侧据此把 gold 哨兵映射到人类作答。
ANSWER_PROTOCOL = {
    "version": 4,
    "rules": [
        "普通问题:答该项在【题面所指时点】的具体值。",
        "【截至最新一期】:锚点 = 该实体最后一次有效记录(不是全局最后一周),沿用其最近有效值(carry-forward)。"
        "全程在场的实体折到全局最后一期,早退场的实体折到它最后出现那一期——这是【同一条沿用规则】碰上不同寿命,不是两套口径。",
        "【整体趋势题】整体趋势 = 【首末净方向】(看整段、以首期 vs 末期的净变化为准);中途或末期的【局部反跳不改判】。只回『上升』或『下降』。"
        "(末期常有一次反向跳动,是【考你别只看最近一两期】,按首末净方向作答即可。)",
        "【属性归属】每个属性只属于【它本来记录在其上的那类实体】。问某实体它【自身从无记录】的属性(哪怕同名/关联实体有该属性)→ 答『无此项/查无此记录』,"
        "★不得经关系链折算到关联实体的值(如问『某律师的争议焦点』:争议焦点是案件的属性、律师本身没有 → 查无,不要去取他经办案件的争议焦点)。",
        "【从未涉及/不存在】:所问项在本场景根本没有(gold 标记 INSUFFICIENT,产线 ABS)→ 期望答『无此项/查无此记录』。",
        "【曾有但已显式停止统计】:所问项曾被跟踪、现已停更(gold forgotten=True,产线 FORGET)→ 期望答『已停止统计/不再跟踪』。",
        "【停统前的最后值】:若问的是『停止统计前最后一次』(产线 PREEXPIRE)→ 这是另一类问法,照常答停掉那一刻的值(非 None)。",
    ],
    "attribute_ownership_no_fold": True,    # ★个人/角色不具案件级属性,问及判查无、不经关系折算(run112358 D:Q50-56 拒答属性归属未声明 → 此处声明)
    "trend_means_net_first_to_last": True,  # ★L7 趋势=首末净方向、末期反跳不改判(run110317 D 点"假二选一":协议没定义非单调如何裁 → 此处定义,二选一即公平、recency 陷阱保住)
    "latest_means_carry_forward": True,     # ★"最新一期"=该实体末次有效记录沿用,统一口径(run160053 D 点的"双标"实为读者侧表面歧义,gold 本就单一真源 latest_valid)
    "two_none_types_distinguished": True,   # ★区分"从未存在"(ABS)vs"曾有已停"(FORGET)是考点
    "gold_sentinel_map": {"INSUFFICIENT": "无此项/查无此记录", "forgotten=true": "已停止统计/不再跟踪"},
}


def stage_input(run: Run):
    scenario = prepare_seed_input(run)
    if scenario is None:
        if run.scenario not in SCENARIOS:
            raise ValueError(f"未知场景 {run.scenario!r}；使用内置 --scenario 或 --seed-pack")
        scenario = SCENARIOS[run.scenario]
    run.write(ART["input"], scenario)
    about = {"answer_protocol": ANSWER_PROTOCOL}
    if scenario.get("seed"):
        about["seed"] = scenario["seed"]
        about["generation_mode"] = "real_task_seeded_synthetic"
        run.set_algo(seed=scenario["seed"])
    run.write("00_about.json", about)


def _pin_game_primary(wp: dict) -> None:
    """把游戏唯一主角固定为一个实例，并同步白皮书的实体总数。

    这是 game 场景的产品语义，不由通用 closed-loop 猜测；外围角色仍可扩容，
    主角则由 ``exact`` 策略永久锁为 1。
    """
    blueprint = wp.get("world_blueprint") or {}
    types = blueprint.get("entity_types") or []
    primaries = [item for item in types if isinstance(item, dict) and item.get("primary") is True]
    if len(primaries) != 1:
        raise WorldBlueprintError(
            f"game 白皮书必须且只能有一个 primary entity type，当前={len(primaries)}")
    primaries[0]["count"] = 1
    primaries[0]["cardinality_policy"] = "exact"
    # 标量 FK 的容量取决于 owner 数量；主角收缩为 1 后，同步收紧不可实现的关系下限。
    for relation in blueprint.get("relation_types") or []:
        minimum = relation.get("min_count")
        capacity = relation_capacity(blueprint, relation)
        if (isinstance(minimum, int) and not isinstance(minimum, bool)
                and capacity > 0 and minimum > capacity):
            relation["min_count"] = capacity
    normalize_world_blueprint(wp)
    total = sum(int(item.get("count", 0)) for item in types if isinstance(item, dict))
    wp.setdefault("shared_world_spec", {}).setdefault("entities", {})["count"] = total


def stage_whitepaper(run: Run):
    sc = run.read(ART["input"])
    pack = validate_seed_input(run, sc)
    if pack is None:
        wp = central_office(sc["description"], sc["few_shot"], run.tracer, run.log)
    else:
        wp = central_office(sc["description"], sc["few_shot"], run.tracer, run.log,
                            seed_pack=pack)
        validate_seed_identity(run, wp)
        run.write("01_seed_audit.json", wp["seed_audit"])
    if run.scenario == "game":
        _pin_game_primary(wp)
    run.write(ART["whitepaper"], wp)
    run.set_algo(active_lines=[l.get("line") for l in wp.get("active_lines", [])],
                 medium=wp.get("output_medium") or wp.get("domain_profile", {}).get("medium"))



def stage_world(run: Run):
    wp = run.read(ART["whitepaper"])
    validate_seed_identity(run, wp)
    existing = None
    # game 的 Story Ledger 必须基于单一 canon；即使 manifest 残留 augment 也始终全量重建。
    if (run.scenario != "game" and run.manifest["config"].get("augment")
            and run.has(ART["world"])):                         # ★增量(§10.1):旧世界上 augment 新实体
        existing = WorldState.from_dict(run.read(ART["world"]))
    ws = build_world(wp, run.tracer, run.log, existing=existing,
                     narrative=(run.scenario == "game"))
    _prepare_lines(wp, ws, run.log)
    seed_audit = validate_seed_world(wp, ws)
    if wp.get("seed_contract"):
        run.write("02_seed_audit.json", seed_audit)
    run.write(ART["world"], ws.to_dict())
    # world 已更换，任何旧渲染中断点都不再与当前 canon 对应。
    (run.dir / CORPUS_CKPT).unlink(missing_ok=True)
    run.set_algo(entities=len(ws.entities), sessions=ws.n_sessions)


def stage_orders(run: Run):
    wp = run.read(ART["whitepaper"]); ws = WorldState.from_dict(run.read(ART["world"]))
    validate_seed_identity(run, wp)
    validate_seed_world(wp, ws)
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
    if run.has(ART["whitepaper"]):
        wp = run.read(ART["whitepaper"])
        validate_seed_identity(run, wp)
        validate_seed_world(wp, ws)
    kept, report = run_well_posed(orders, ws)
    run.write(ART["orders"], kept)                          # 过闸 orders 覆写(下游 stage_questions 只对良定义题出题)
    run.write("03_well_posed_report.json", report)
    o = report["overall"]
    run.set_algo(well_posed={"overall": o, "by_line": report["by_line"], "n_dropped": report["n_dropped"]})
    run.log(f"  ★边A闸(良定义):{o['well_posed']}/{o['n']} 良定义({(o['pass_rate'] or 0):.0%}),弃 {report['n_dropped']} "
            f"→ 过闸 orders 下游出题")


def stage_questions(run: Run):
    orders = run.read(ART["orders"]); wp = run.read(ART["whitepaper"])
    pack = validate_seed_identity(run, wp)
    qs = phrase_questions(orders, wp, run.tracer, run.log)
    if pack is not None:
        provenance = run.read(ART["input"])["seed"]
        qs = [{**q, "seed": provenance} for q in qs]
    run.write(ART["questions"], qs)
    run.set_algo(questions=len(qs))


def _corpus_checkpoint_identity(wp: dict, world: dict, target: int,
                                delta_mode: bool, only: set | None,
                                pairs: set[tuple[str, int]] | None) -> str:
    """计算渲染中断点的稳定身份；输入或渲染范围变化即不可续用。"""
    payload = {
        "version": CORPUS_RENDER_CONTRACT_VERSION,
        "whitepaper": wp,  # style_spec 属于白皮书，随整体一起绑定。
        "world": world,
        "target_tokens": target,
        "delta_scope": {
            "mode": "delta" if delta_mode else "full",
            "entities": sorted(only or []),
            "entity_sessions": [[entity, session] for entity, session in sorted(pairs or set())],
        },
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def stage_corpus(run: Run):
    wp = run.read(ART["whitepaper"]); world = run.read(ART["world"])
    ws = WorldState.from_dict(world)
    validate_seed_identity(run, wp)
    validate_seed_world(wp, ws)
    if run.scenario == "game" and not ws.narrative:
        raise WorldBlueprintError(
            "game corpus 缺少合法 Story Ledger；请先强制重跑 world，禁止退化为普通语料渲染")
    cfg = run.manifest["config"]
    requested_delta = "render_only" in cfg or "render_only_pairs" in cfg
    # 叙事世界也允许在【已有完整 05】上定向补充实体×章节；render_corpus 会继续
    # 使用 Story Ledger/reviewer，并在全量旧+新语料上复核全部 canonical event。
    # 没有既有语料时禁止把 delta 冒充首次全量渲染。
    if requested_delta and ws.narrative:
        if not run.has(ART["corpus"]):
            raise WorldBlueprintError(
                "game narrative delta 需要已有完整 corpus；首次渲染必须覆盖全部 Story Ledger")
        published = run.read(ART["corpus"])
        published_corpus = published.get("corpus", published) if isinstance(published, dict) else {}
        expected_sessions = set(ws.sessions())
        published_sessions = {
            item.get("session_id") for item in (published_corpus.get("sessions") or [])
            if isinstance(item, dict)
        }
        published_done = set(published.get("done_weeks") or []) if isinstance(published, dict) else set()
        if published_sessions != expected_sessions or published_done != expected_sessions:
            raise WorldBlueprintError(
                "game narrative delta 的既有 corpus 未精确覆盖全部章节，拒绝把局部语料当完整基线")
    delta_mode = requested_delta
    if requested_delta and ws.narrative:
        run.log("  ⓘ game Story Ledger 定向补渲：只追加指定范围，收口仍复核全部 canonical event")
    target = int(run.manifest["config"].get("target_tokens", 1_000_000))
    only = set(cfg.get("render_only") or []) if delta_mode else None
    pairs = ({(item[0], int(item[1])) for item in (cfg.get("render_only_pairs") or [])
              if isinstance(item, (list, tuple)) and len(item) == 2} if delta_mode else None)
    identity = _corpus_checkpoint_identity(wp, world, target, delta_mode, only, pairs)
    ckpt = run.dir / CORPUS_CKPT
    if ckpt.exists():                                       # 中断续渲只读独立 checkpoint
        st = run.read(CORPUS_CKPT)
        if st.get("identity") == identity:
            corpus, done = st["corpus"], set(st["done_weeks"])
            run.log(f"  ↻ 续渲:已完成 {len(done)} 周")
        else:
            corpus, done = {"sessions": []}, set()
            run.log("  ⓘ 渲染 checkpoint 与当前输入不匹配，忽略并从空语料开始")
    elif delta_mode and run.has(ART["corpus"]):             # 非剧情闭环的增量渲染从上一版成品起步
        st = run.read(ART["corpus"])
        corpus, done = st["corpus"], set(st["done_weeks"])
    else:
        corpus, done = {"sessions": []}, set()

    def save():
        run.write(CORPUS_CKPT, {"identity": identity, "corpus": corpus,
                                "done_weeks": sorted(done)})

    render_corpus(wp, ws, target, run.tracer, corpus, done, save, run.log,
                  only_entities=only, only_entity_sessions=pairs)
    # 只有整个渲染成功后才发布最终产物；中断时旧成品不会被半成品覆盖。
    run.write(ART["corpus"], {"corpus": corpus, "done_weeks": sorted(done)})
    ckpt.unlink(missing_ok=True)
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
    if run.has(ART["whitepaper"]):
        wp = run.read(ART["whitepaper"])
        validate_seed_identity(run, wp)
        if wp.get("seed_contract"):
            validate_seed_world(wp, WorldState.from_dict(run.read(ART["world"])))
    elif run.manifest.get("config", {}).get("seed_pack_digest"):
        raise SeedPackError("种子运行缺少白皮书，不能发布题库")
    questions = run.read(ART["questions"])
    corpus_obj = run.read(ART["corpus"])
    kept, report = run_grounding(questions, corpus_obj)
    run.write(ART["grounding"], kept)
    run.write("06_grounding_report.json", report)
    o = report["overall"]
    algo_update = {"grounding": {"overall": o, "by_line": report["by_line"],
                                  "by_capability": report["by_capability"],
                                  "n_dropped": report["n_dropped"]}}
    # grounding 是产物汇合点，必须据当前 06 重算闭环账本。否则一次早期失败后
    # 从中游恢复，即使最终题量已达标，manifest 仍会永久携带陈旧 UNMET。
    target = (run.manifest.get("algo") or {}).get("targetspec") or {}
    floors = target.get("per_line_min") or {}
    if target and isinstance(floors, dict):
        per_line_final = {
            line_id: int((report["by_line"].get(line_id) or {}).get("grounded", 0) or 0)
            for line_id in floors
        }
        min_questions = int(target.get("min_questions", 0) or 0)
        met = (int(o.get("grounded", 0) or 0) >= min_questions
               and all(per_line_final[line_id] >= int(floor)
                       for line_id, floor in floors.items()))
        algo_update.update({
            "per_line_final": per_line_final,
            "met_status": "MET" if met else "UNMET_GROUNDING",
        })
    run.set_algo(**algo_update)
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
    ap.add_argument("--scenario", default=None)
    ap.add_argument("--seed-pack", help="策展种子 JSON；增强 input→whitepaper，后续阶段保持同一合同")
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

    if a.seed_pack and a.scenario:
        ap.error("--seed-pack 已定义场景，不能同时指定 --scenario")
    try:
        seed_cfg = seed_config(a.seed_pack) if a.seed_pack else {}
    except (SeedPackError, OSError) as error:
        ap.error(str(error))
    requested_scenario = ("seed_" + seed_cfg["seed_id"] if seed_cfg
                          else a.scenario or "office")

    if a.run_id:                                            # 在已有 run 上继续:scenario 取自其 manifest
        run_id, scenario = a.run_id, requested_scenario
        mf = RUNS_DIR / a.run_id / "manifest.json"
        if mf.exists():
            scenario = json.loads(mf.read_text(encoding="utf-8")).get("scenario", requested_scenario)
    elif a.resume:
        run_id = latest_run_for(requested_scenario) or new_run_id(requested_scenario)
        scenario = requested_scenario
    else:
        run_id, scenario = new_run_id(requested_scenario), requested_scenario

    cfg = {"from": a.from_stage, "to": a.to_stage, "only": a.only}
    manifest_path = RUNS_DIR / run_id / "manifest.json"
    if seed_cfg and manifest_path.exists():
        try:
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
            seed_cfg = seed_config(a.seed_pack, previous.get("config") or {})
        except (SeedPackError, OSError) as error:
            ap.error(str(error))
    cfg.update(seed_cfg)
    if a.target_mtokens is not None:
        cfg["target_tokens"] = int(a.target_mtokens * 1_000_000)
    elif not (RUNS_DIR / run_id / "manifest.json").exists():
        cfg["target_tokens"] = 1_000_000
    run = Run(scenario, run_id, tag=a.tag, config_meta=cfg)

    t0 = time.time()
    tgt = run.manifest["config"].get("target_tokens", 1_000_000)
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
