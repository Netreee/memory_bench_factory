"""
pipeline.stage_c_corpus_synthesis_v7 — Stage C V7: 多文档密度 + 种子扰动。

V7 第二刀(redesign_v7.md §3.2/§3.3),针对审查 A 的"结构性天花板":
  V6 每 period 只 1 篇文档 → 不存在"单 session 内多文档聚合"、信息密度低、~40% 伪记忆题。
  V7 每 period 拆成【多篇异质文档】,把字段【分散】到不同文档:
    - 数值指标(缺陷率/oncall…)→ 周报(weekly_report)
    - 人事/状态(负责人/对接人/阶段,含 signal_competition)→ 通报(personnel_update)
  → 让检索 top-k 真的要"从多文档里挑"、支持跨文档聚合题、为 M2(证据塌陷)铺路。

复用 V6:skeleton 合成(synthesize_chain_skeleton_v6)+ 信号竞争铁律(DOC_SYNTHESIS_SYSTEM_V6)。
  signal_competition 字段固定落在"通报"组 → 旧值跨多 period 在通报里高频、新值低频,频率结构不变。

跑法:cd memory_bench_factory && ./venv/bin/python -m pipeline.stage_c_corpus_synthesis_v7
"""
from __future__ import annotations
from pathlib import Path
from typing import Optional
import json
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config

from .schema import Chain, Period, ScenarioDimensions
from .stage_c_corpus_synthesis_v6 import (
    synthesize_chain_skeleton_v6, DOC_SYNTHESIS_SYSTEM_V6,
)
from .stage_d_v7_question_synthesis import _is_quantity


# 文档组 → 文风提示(让多篇文档真异质,不是同一篇换标题)
_DOC_STYLE = {
    "weekly_report": "正式周报:用指标小节叙述本周进展与数据",
    "personnel_update": "内部通报/邮件:口吻像 HR 或 PM 发的人事与状态通知,简短",
    "meeting_minutes": "会议纪要:要点式记录讨论与决议",
}


def _group_fields(skeleton: dict) -> list:
    """把字段分到不同文档组。返回 [(doc_type, [field_names]), ...]。

    - 数值字段(缺陷率/oncall)+ 文本 stable(项目名)→ weekly_report
    - signal_competition + 非数值 evolving(状态/阶段)→ personnel_update
    signal_competition 固定落在 personnel_update,保持其跨 period 频率结构。
    """
    fields = skeleton.get("fields", [])
    periods = skeleton.get("periods", [])
    sample_state = periods[0].get("state", {}) if periods else {}

    def is_num(f):
        return _is_quantity(sample_state.get(f.get("name")))

    metric = [f["name"] for f in fields
              if f.get("type") in ("stable", "evolving") and is_num(f)]
    stable_text = [f["name"] for f in fields
                   if f.get("type") == "stable" and not is_num(f)]
    sc = [f["name"] for f in fields if f.get("type") == "evolving_signal_competition"]
    other_evolving = [f["name"] for f in fields
                      if f.get("type") == "evolving" and not is_num(f)]

    groups = []
    g_report = metric + stable_text
    g_personnel = sc + other_evolving
    if g_report:
        groups.append(("weekly_report", g_report))
    if g_personnel:
        groups.append(("personnel_update", g_personnel))
    if not groups:  # 兜底:全塞一篇
        groups.append(("weekly_report", [f["name"] for f in fields]))
    return groups


def _build_doc_user_prompt_v7(skeleton, sub_state, period_idx, sc_in_group,
                              samples_summary, doc_type, all_field_names):
    sc_note = "(本篇无 signal_competition 字段)"
    if sc_in_group:
        lines = [f"  - {fn}: 当前值='{sub_state.get(fn,'?')}'"
                 f"(★只写这个值,严禁提其他 period 的值)" for fn in sc_in_group]
        sc_note = "★ 信号竞争字段(严禁回顾/展望):\n" + "\n".join(lines)
    style = _DOC_STYLE.get(doc_type, "")
    return (
        f"【chain 主题】{skeleton.get('main_theme','?')}\n"
        f"【本篇文档类型】{doc_type} —— {style}\n"
        f"【★本篇只负责这些字段(period {period_idx} 当前值,必须 100% 体现)】\n"
        f"{json.dumps(sub_state, ensure_ascii=False, indent=2)}\n"
        f"【★严禁写入其它字段】本篇【不要】提到这些字段:"
        f"{[f for f in all_field_names if f not in sub_state]}\n\n"
        f"{sc_note}\n\n"
        f"【风格参考】\n{samples_summary}\n\n"
        f"请只就上面分配给本篇的字段,合成 1 篇 {doc_type},严格 JSON。"
    )


def synthesize_period_doc_group_v7(skeleton, period_idx, field_group, doc_type, samples):
    """合成一篇【只含 field_group 字段】的文档(信号竞争铁律沿用 V6 system)。"""
    periods = skeleton.get("periods", [])
    if period_idx >= len(periods):
        return None
    full_state = periods[period_idx].get("state", {}) or {}
    sub_state = {f: full_state[f] for f in field_group if f in full_state}
    if not sub_state:
        return None
    sc_in_group = [f.get("name") for f in skeleton.get("fields", [])
                   if f.get("type") == "evolving_signal_competition"
                   and f.get("name") in sub_state]
    all_field_names = [f.get("name") for f in skeleton.get("fields", [])]
    samples_summary = "\n".join(
        f"[doc {i}] title='{d.title}' preview:{(d.content or '')[:160]}..."
        for i, d in enumerate(samples[:3]))
    msgs = [
        {"role": "system", "content": DOC_SYNTHESIS_SYSTEM_V6},
        {"role": "user", "content": _build_doc_user_prompt_v7(
            skeleton, sub_state, period_idx, sc_in_group, samples_summary,
            doc_type, all_field_names)},
    ]
    try:
        data = config.chat_json(msgs, temperature=0.7, max_tokens=3072)
    except Exception as e:
        print(f"[doc-v7] period={period_idx} {doc_type} LLM 异常: {e}")
        return None
    return {
        "doc_id": str(data.get("doc_id", f"synth_v7_{uuid.uuid4().hex[:8]}")),
        "title": str(data.get("title", "")),
        "content": str(data.get("content", "")),
        "metadata": {**(data.get("metadata", {}) or {}),
                     "doc_type": doc_type, "synthetic": True},
    }


def synthesize_corpus_v7(
    expanded_pool: list,
    dimensions: ScenarioDimensions,
    cell_requirements: list,
    n_chains: int = 1,
    n_periods_per_chain: int = 5,
    verbose: bool = True,
) -> list:
    """V7 主入口:多文档密度 corpus(每 period 多篇异质文档)。"""
    chains: list = []
    for chain_idx in range(n_chains):
        if verbose:
            print(f"\n[stage_c_v7] === Chain {chain_idx+1}/{n_chains} ===")
            print(f"[stage_c_v7] Step 1: skeleton(复用 V6,种子扰动)...")
        skeleton = synthesize_chain_skeleton_v6(
            expanded_pool, dimensions, cell_requirements,
            n_periods=n_periods_per_chain, chain_idx=chain_idx)
        if skeleton is None:
            print(f"[stage_c_v7] chain {chain_idx} skeleton 失败,跳过")
            continue

        groups = _group_fields(skeleton)
        if verbose:
            print(f"[stage_c_v7]   chain: {skeleton.get('chain_name')}")
            print(f"[stage_c_v7]   ★ 文档分组({len(groups)} 篇/period):")
            for dt, fns in groups:
                print(f"       - {dt}: {fns}")

        periods: list = []
        period_info_list = skeleton.get("periods", [])
        for period_idx in range(n_periods_per_chain):
            pinfo = period_info_list[period_idx] if period_idx < len(period_info_list) else {}
            docs = []
            for doc_type, field_group in groups:
                if verbose:
                    print(f"[stage_c_v7]   period {period_idx} · {doc_type} "
                          f"({len(field_group)}字段)...", end=" ", flush=True)
                doc = synthesize_period_doc_group_v7(
                    skeleton, period_idx, field_group, doc_type, expanded_pool)
                if doc is None:
                    print("[失败]")
                    continue
                docs.append(doc)
                if verbose:
                    print(f"len={len(doc.get('content',''))}c")
            periods.append(Period(
                period_idx=period_idx,
                date=str(pinfo.get("date", "")),
                timestamp=str(pinfo.get("date", "")),
                ingest_docs=docs,
                state=pinfo.get("state", {}),
            ))

        chain = Chain(
            chain_id=str(skeleton.get("chain_id", f"chain_v7_{chain_idx}")),
            chain_name=str(skeleton.get("chain_name", f"chain_v7_{chain_idx}")),
            cluster=skeleton.get("main_theme"),
            period_count=len(periods),
            periods=periods,
            qas=[],
        )
        if periods:
            periods[0].state["_skeleton"] = skeleton
        chains.append(chain)
    return chains


def main():
    from .schema import RefinedScenarioSpec, Document
    from .stage_a_dimensions import infer_dimensions
    from .stage_b_ops_profile import build_ops_profile
    from .cell_requirements_table import build_cell_requirements

    spec = RefinedScenarioSpec(
        name="office_v7_density",
        description_refined="AI 工程团队周报,业务线 leader 视角",
        corpus_samples=[
            Document(doc_id="d1", title="周报 W17",
                     content="本周 P0 缺陷率 20%。Oncall 10。负责人:张三。阶段:开发。",
                     metadata={"date": "2025-04-17", "doc_type": "周报"}),
            Document(doc_id="d2", title="周报 W18",
                     content="本周 P0 缺陷率 17%。Oncall 9。负责人:张三。阶段:开发。",
                     metadata={"date": "2025-04-24", "doc_type": "周报"}),
        ],
        perspective="cross_line_leader", subject_type="project",
        temporal_pattern="weekly", target_size=12,
    )
    dims = infer_dimensions(spec, use_llm=False)
    profile = build_ops_profile(dims, target_size=12, enforce_five_organs_full=False)
    cell_reqs = build_cell_requirements(profile)

    print("=== Stage C V7(多文档密度)===")
    chains = synthesize_corpus_v7(spec.corpus_samples, dims, cell_reqs,
                                  n_chains=1, n_periods_per_chain=5)
    c = chains[0]
    total_docs = sum(len(p.ingest_docs) for p in c.periods)
    print(f"\n=== ✓ {c.chain_name}: {len(c.periods)} period, "
          f"{total_docs} 篇文档(V6 是 {len(c.periods)} 篇,V7 密度 ×{total_docs//len(c.periods)})===")
    # 抽看 period 0 的多篇文档,验证字段分散
    print(f"\n--- period 0 的 {len(c.periods[0].ingest_docs)} 篇文档(验证字段分散)---")
    for doc in c.periods[0].ingest_docs:
        print(f"  [{doc['metadata'].get('doc_type')}] {doc['title']}")
        print(f"    {doc['content'][:120]}...")


if __name__ == "__main__":
    main()
