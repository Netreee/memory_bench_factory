"""
tools.cross_scenario_v6 — V6 跨场景泛化验证(标准 D)。

证明:同一条 V6 流水线、零手工调参,换不同领域的 few-shot 输入,
都能产出 valid + 含信号竞争 M3 材料的 benchmark。

3 个对照领域(刻意拉开):
  - office     AI 工程周报(已有,profile=office_engineering)
  - support    客服工单(profile=customer_support)
  - legal      法律并购案件(profile=legal_matter)

跑法(单场景,便于并行/控成本):
  cd memory_bench_factory
  ./venv/bin/python tools/cross_scenario_v6.py support
  ./venv/bin/python tools/cross_scenario_v6.py legal
"""
from __future__ import annotations
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.schema import RefinedScenarioSpec, Document
from pipeline.run_pipeline_v6 import run_pipeline_v6, PipelineConfigV6


# ─────────────────────────────────────────────────────────────────────────────
# 场景库(few-shot 输入:description + 2-3 篇样本)
# ─────────────────────────────────────────────────────────────────────────────

SCENARIOS = {
    # ── AI 工程周报 → office_engineering profile ────────────────
    "office": RefinedScenarioSpec(
        name="office_engineering_v6",
        description_refined=(
            "我是业务线 leader,每周审阅 AI 工程团队迭代进展。"
            "关键字段:P0 缺陷率、Oncall 数量、负责人、客户对接人、SLA 达标率、版本。"
        ),
        corpus_samples=[
            Document(doc_id="d1", title="AI 工程周报 W17",
                     content="本周 P0 缺陷率 20%(2/10),Oncall 数量 10。负责人:张三。",
                     metadata={"date": "2025-04-17", "doc_type": "周报", "author": "张三"}),
            Document(doc_id="d2", title="AI 工程周报 W18",
                     content="本周 P0 缺陷率 17%。Oncall 数量 9。负责人:张三。",
                     metadata={"date": "2025-04-24", "doc_type": "周报", "author": "张三"}),
            Document(doc_id="d3", title="AI 工程周报 W19",
                     content="本周 P0 缺陷率 14%。Oncall 数量 8。负责人:张三。",
                     metadata={"date": "2025-05-01", "doc_type": "周报", "author": "张三"}),
        ],
        perspective="cross_line_leader",
        subject_type="project",
        temporal_pattern="weekly",
        target_size=12,
    ),

    # ── 客服工单 → customer_support profile ─────────────────────
    "support": RefinedScenarioSpec(
        name="customer_support_v6",
        description_refined=(
            "我是客服团队 line manager,每周复盘重点客户的服务工单进展。"
            "关注字段:工单状态、对接客服、SLA 响应时长、客户满意度、问题分类。"
            "必须以最新信息为准,追踪字段变化。"
        ),
        corpus_samples=[
            Document(doc_id="t1", title="云图科技 工单周报 W10",
                     content="客户「云图科技」本周工单(订单同步异常)状态:处理中。"
                             "对接客服:小林。SLA 响应时长 4 小时,客户满意度 4.2。",
                     metadata={"date": "2025-03-03", "doc_type": "工单", "author": "小林"}),
            Document(doc_id="t2", title="云图科技 工单周报 W11",
                     content="「云图科技」工单本周持续跟进,对接客服小林。"
                             "SLA 响应时长 3.5 小时,满意度 4.3。",
                     metadata={"date": "2025-03-10", "doc_type": "工单", "author": "小林"}),
        ],
        perspective="line_manager",
        subject_type="customer",
        temporal_pattern="weekly",
        target_size=12,
    ),

    # ── 法律并购案件 → legal_matter profile ─────────────────────
    "legal": RefinedScenarioSpec(
        name="legal_matter_v6",
        description_refined=(
            "我是合规/审计视角,每周追踪并购案件的处理进展。"
            "关注字段:案件阶段、主办律师、风险等级、关键条款状态、对方对接人。"
            "必须以最新信息为准。"
        ),
        corpus_samples=[
            Document(doc_id="m1", title="赛维并购案 进展备忘 W6",
                     content="「赛维并购案」本周进展:尽职调查阶段。主办律师:陈伟。"
                             "风险等级:中。本周完成财务 DD 初稿,对方对接人周经理。",
                     metadata={"date": "2025-02-05", "doc_type": "合同", "author": "陈伟"}),
            Document(doc_id="m2", title="赛维并购案 进展备忘 W7",
                     content="「赛维并购案」本周推进交割条件谈判,主办律师陈伟负责。"
                             "风险等级:中,对方对接人周经理。",
                     metadata={"date": "2025-02-12", "doc_type": "合同", "author": "陈伟"}),
        ],
        perspective="audit_compliance",
        subject_type="contract_matter",
        temporal_pattern="weekly",
        target_size=12,
    ),
}


def run_one(key: str, n_chains: int = 1, n_periods: int = 5):
    spec = SCENARIOS[key]
    tag = "" if n_chains == 1 else f"_x{n_chains}"
    cfg = PipelineConfigV6(
        use_llm_for_dims=False,
        target_size=spec.target_size,
        n_chains=n_chains,
        n_periods_per_chain=n_periods,
        n_docs_per_period=1,
        benchmark_id=f"v6_{key}{tag}",
    )
    print(f"\n{'#'*64}\n# 跨场景验证: {key}  ({spec.name})  n_chains={n_chains}\n{'#'*64}")
    bm = run_pipeline_v6(refined_spec=spec, cfg=cfg)
    print(f"\n=== [{key}] 完成 ===")
    print(f"  profile: {bm.ops_profile.profile_name}")
    print(f"  total: {bm.statistics.total_questions}")
    print(f"  by_failmode: {bm.statistics.by_failmode}")
    ev = bm.scenario_spec_snapshot.get("m3_signal_competition_evidence", [])
    print(f"  ★ M3 信号竞争证据 {len(ev)} 条:")
    for e in ev:
        print(f"    [{e['qid']}] {e.get('field')}: "
              f"旧'{e.get('old_value')}'({e.get('old_freq')}x) vs "
              f"新'{e.get('new_value')}'({e.get('new_freq')}x), ratio={e.get('signal_ratio')}")
    return bm


def main():
    args = sys.argv[1:]
    n_chains = 1
    n_periods = 5
    if "--chains" in args:
        i = args.index("--chains")
        n_chains = int(args[i + 1])
        args = args[:i] + args[i + 2:]
    if "--periods" in args:
        i = args.index("--periods")
        n_periods = int(args[i + 1])
        args = args[:i] + args[i + 2:]
    keys = args or ["office", "support", "legal"]
    for k in keys:
        if k not in SCENARIOS:
            print(f"[skip] 未知场景 {k},可选: {list(SCENARIOS)}")
            continue
        run_one(k, n_chains=n_chains, n_periods=n_periods)


if __name__ == "__main__":
    main()
