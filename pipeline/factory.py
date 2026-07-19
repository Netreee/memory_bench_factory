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
    "medical": {   # ★跨场景区分度对照:词汇/结构都与 office 不同,仍有 2 个 person 字段喂 L2;含复选偏好轴喂 L4
        "description": (
            "评测记忆体在『慢病患者多次门诊病程跟踪』场景的表现。医生跨多次就诊追踪患者的"
            "血压、空腹血糖、用药方案、诊断结论、主治医师、会诊上级等随时间演化的字段;"
            "某些指标会停止监测,主治医师变更会带动会诊关系变化,要以最新一次就诊为准。"
            "此外,患者【每次就诊都会选择一种随访方式】(门诊复诊 / 电话随访 / 上门随访),"
            "其稳定偏好需从【多次选择】中反推(从不直接声明,且最近一次未必代表一贯倾向)。"),
        "few_shot": [
            {"title": "门诊记录 0312", "content": "患者本次血压 150/95,空腹血糖 7.8,主治:李医生,会诊上级:王主任。予二甲双胍。本次随访方式:门诊复诊。",
             "doc_type": "门诊记录", "date": "2025-03-12"},
            {"title": "检查报告 0312", "content": "糖化血红蛋白 8.2%,诊断:2型糖尿病。主治:周医生。",
             "doc_type": "检查报告", "date": "2025-03-12"},
        ],
    },
    "legal": {   # ★第三场景·远角落(5维框架):非对话 ingest(matter 文件)+ 非 first-person 视角(被合伙人评判的承办)+ 案件阶段动力学
        "description": (
            "评测记忆体在『律所多案件(matter)承办跟踪』场景的表现。律所跨多次案件评审追踪每个案件的"
            "主办律师、督导合伙人、对手方、案件状态、争议标的额、累计计费工时、风险评分等随【案件阶段】演化的字段;"
            "主办律师变更会带动督导关系变化,某些案件撤诉/和解后会停止计费跟踪,要以最新一次评审为准。"
            "此外,每次案件评审都会就该案件选定一种【处理策略倾向】(庭外和解 / 调解 / 提起诉讼 / 申请仲裁),"
            "其稳定偏好需从【多次评审的选择】中反推(从不直接声明,且最近一次未必代表一贯倾向)。"),
        "few_shot": [
            {"title": "案件评审纪要 M0312", "content": "本次评审:争议标的额 320万,累计计费工时 86,风险评分 4。主办律师:陈律,督导合伙人:周合伙人。本期处理策略倾向:庭外和解。",
             "doc_type": "评审纪要", "date": "2025-03-12"},
            {"title": "对手方备忘 M0312", "content": "对手方:鼎兴贸易;争议焦点:供货违约。主办律师:李律。",
             "doc_type": "对手方备忘", "date": "2025-03-12"},
        ],
    },

    # ════════ 过夜批量(6 场景)· 均按既有配方:演化字段(L1/KU)+ 人物关系链(L2)+ 数值场(L3/L7)
    #          + 4 选 1 偏好轴(L4,多次选择反推)+ 停统字段(FORGET)+ 文本/状态字段(L5)════════
    "game": {   # 游戏:NPC/个性化剧情/世界观
        "description": (
            "评测记忆体在『开放世界 RPG 剧情与阵营追踪』场景的表现。游戏主持跨多个【章节】追踪每个 NPC 的"
            "好感度、所属阵营、当前任务状态、角色等级、对玩家称谓等随【剧情推进】演化的字段,要以最新章节为准;"
            "NPC 改换阵营会带动其【效忠领主】变化,某些一次性支线在结算后会停止追踪。"
            "此外,玩家每章会在关键抉择点选一种【行事路线】(正义 / 中立 / 谋略 / 暴力),"
            "其稳定倾向需从【多章的选择】中反推(从不直接声明,且最近一章未必代表一贯路线)。"),
        "few_shot": [
            {"title": "第七章 · 剧情日志", "content": "灰袍贤者对玩家好感度升至 70,现效忠『银鹿议会』。当前任务:护送商队(进行中)。本章玩家抉择:谋略。",
             "doc_type": "剧情日志", "date": "2025-05-01"},
            {"title": "第七章 · 角色档案", "content": "黑剑佣兵等级 18,所属阵营『银鹿议会』,效忠领主:贤者。称玩家为『盟友』。",
             "doc_type": "角色档案", "date": "2025-05-01"},
        ],
    },
    "agent": {   # 复杂任务/长程 Agent:工具经验记忆/复用
        "description": (
            "评测记忆体在『长程 Agent 系统任务执行与工具复用』场景的表现。编排器跨多个【执行轮次】追踪每个"
            "子任务的状态、所用工具、工具成功率、负责执行体、累计重试次数等随【轮次】演化的字段,要以最新一轮为准;"
            "子任务改派执行体会带动其【上级编排器】变化,被弃用的工具会停止统计成功率。"
            "此外,每轮遇阻时会选一种【修复策略】(原地重试 / 更换工具 / 降级方案 / 上报人工),"
            "其稳定倾向需从【多轮的选择】中反推(从不直接声明,且最近一轮未必代表一贯策略)。"),
        "few_shot": [
            {"title": "轮次 R12 · 执行轨迹", "content": "子任务『抓取财报』状态:成功。所用工具:web_fetch(成功率 0.82)。执行体:agent_A。本轮修复策略:更换工具。",
             "doc_type": "执行轨迹", "date": "2025-05-02"},
            {"title": "轮次 R12 · 工具调用记录", "content": "工具 sql_query 返回超时,重试 2 次。负责执行体:agent_B,上级编排器:planner_main。",
             "doc_type": "工具调用记录", "date": "2025-05-02"},
        ],
    },
    "cs": {   # 企业客服/个性化 1V1 运营
        "description": (
            "评测记忆体在『企业客服多客户 1V1 运营跟踪』场景的表现。客户成功团队跨多个【服务周期】追踪每个客户的"
            "分层等级、满意度评分、负责客服、当前工单状态、承诺履约率等随周期演化的字段,要以最新周期为准;"
            "客户改派负责客服会带动其【客服主管】变化,工单关闭后会停止追踪其处理时长。"
            "此外,客户每次接触会被记录一种【偏好沟通渠道】(电话 / 在线IM / 邮件 / 上门),"
            "其稳定偏好需从【多次接触的渠道】中反推(从不直接声明,且最近一次未必代表一贯偏好)。"),
        "few_shot": [
            {"title": "服务周期 W19 · 工单记录", "content": "客户『云图科技』分层:战略级。满意度 4.6。负责客服:小林。当前工单:计费异常(处理中)。本次接触渠道:电话。",
             "doc_type": "工单记录", "date": "2025-05-09"},
            {"title": "服务周期 W19 · 交接备忘", "content": "客户『云图科技』承诺履约率 92%。负责客服:小林,客服主管:陈经理。",
             "doc_type": "交接备忘", "date": "2025-05-09"},
        ],
    },
    "companion": {   # 陪伴/情感支持(内容保持中性、临床口吻)
        "description": (
            "评测记忆体在『陪伴助手长期用户跟踪』场景的表现。助手跨多次【互动】追踪用户的情绪基线、压力水平、"
            "主要倾诉对象、当前关注议题、作息规律等随时间演化的字段,要以最新一次互动为准;主要倾诉对象变化会带动"
            "其【关系角色】变化,已化解的旧议题会停止跟踪。系统对【安全禁忌话题】只登记边界、不存具体内容。"
            "此外,用户每次互动会表现出一种【偏好的支持方式】(倾听陪伴 / 给出建议 / 转移注意 / 安排提醒),"
            "其稳定偏好需从【多次互动】中反推(从不直接声明,且最近一次未必代表一贯偏好)。"),
        "few_shot": [
            {"title": "互动记录 0510", "content": "本次情绪基线:平稳。压力水平 3/10。主要倾诉对象:大学室友。当前关注议题:转岗准备。本次偏好支持方式:给出建议。",
             "doc_type": "互动记录", "date": "2025-05-10"},
            {"title": "事件备忘 0510", "content": "用户提及 6 月 2 日为其母亲生日(重要纪念日)。主要倾诉对象:大学室友,关系角色:挚友。",
             "doc_type": "事件备忘", "date": "2025-05-10"},
        ],
    },
    "assistant": {   # 个性化助手:生活/内容/购物/智能家居
        "description": (
            "评测记忆体在『个性化生活助手长期偏好跟踪』场景的表现。助手跨多次【交互】追踪用户在各品类的偏好、"
            "月度预算、常用下单账户、智能家居主控设备、推荐命中率等随时间演化的字段,要以最新一次为准;主控设备更换"
            "会带动其【所在房间场景】变化,已退订的服务会停止跟踪。系统登记用户的【硬约束】(过敏原 / 禁区 / 规则)。"
            "此外,用户每次购物会体现一种【选购取向】(性价比优先 / 品质优先 / 品牌优先 / 环保优先),"
            "其稳定偏好需从【多次选购】中反推(从不直接声明,且最近一次未必代表一贯取向)。"),
        "few_shot": [
            {"title": "交互记录 0511", "content": "本次下单:厨房用品。月度预算 2000 元。常用账户:家庭主号。本次选购取向:性价比优先。已知过敏原:花生。",
             "doc_type": "交互记录", "date": "2025-05-11"},
            {"title": "设备设置 0511", "content": "智能家居主控设备:客厅音箱,所在房间场景:客厅。推荐命中率 0.74。",
             "doc_type": "设备设置", "date": "2025-05-11"},
        ],
    },
    "kb": {   # 企业知识/经验系统共享
        "description": (
            "评测记忆体在『企业知识库版本与经验传承跟踪』场景的表现。知识团队跨多个【发布周期】追踪每条知识条目的"
            "版本号、有效状态、维护人、引用次数、准确率评分等随时间演化的字段,要以最新发布为准;条目改派"
            "维护人会带动其【领域负责人】变化,过期失效的条目会停止维护跟踪。"
            "此外,每次知识更新会选一种【发布策略】(灰度发布 / 全量发布 / 定向推送 / 标记废弃),"
            "其稳定倾向需从【多次更新的选择】中反推(从不直接声明,且最近一次未必代表一贯策略)。"),
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
