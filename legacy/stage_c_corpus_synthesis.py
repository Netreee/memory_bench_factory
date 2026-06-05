"""
pipeline.stage_c_corpus_synthesis — Stage C v3: Corpus 合成(数据合成核心)。

V3 的灵魂:从 ExpandedSeedPool + ScenarioDimensions + OpsProfile 真正合成新 corpus,
而不是复用 OfficeMem 全量数据。

策略:【结构先行,语言后填】
  Step 1 (skeleton): 用 LLM 设计 chain skeleton JSON
    - 字段集(fields):该 chain 关注的字段名 + 类型(stable/evolving/conflict)
    - state by period: 每个 period 后每个字段的 ground truth 值
    - events: 字段变化触发事件(由 OpsProfile 引导:KU+M3 需要反常识注入)
  Step 2 (docs): 用 LLM 根据 skeleton 合成每 period 的实际文档
    - 风格仿 ExpandedSeedPool 中的 samples
    - 文档自然提到该 period 的字段值(基于 state)

W1.5 V3.0 目标:1 chain × 10 periods × 每 period 1 篇文档,corpus content inline。

详见 docs/anchors/redesign_v3.md 第 2 节(Stage C)。

跑法:
  cd memory_bench_factory
  ./venv/bin/python -m pipeline.stage_c_corpus_synthesis
"""
from __future__ import annotations
from pathlib import Path
from typing import Optional
import json
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config

from .schema import (
    Document, Chain, Period, ScenarioDimensions, OpsProfileV3,
    parse_cell_key,
)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Step 1: Chain Skeleton 合成(结构层 ground truth)
# ─────────────────────────────────────────────────────────────────────────────

SKELETON_SYSTEM = """你是 memory benchmark corpus 合成专家。\
你要为一个测试 chain 设计【结构骨架】(skeleton),作为后续生成文档和题目的 ground truth。

【设计原则】
1. 每个 chain 围绕一个【主题】(如某项目、某产品线、某客户案例)
2. 设计 5-8 个【字段(fields)】,每个字段有类型:
   - "stable": 整个 chain 值不变(用于 IE / AR 题)
   - "evolving": 跨 period 变化,新值替代旧值(用于 KU 题)
   - "conflict": 注入【反常识】值,期望 LLM 倾向训练分布默认值(用于 KU+M3 失败模式!)
3. 每个 period 的 state 必须列出所有字段的当前值
4. events 数组记录字段变化触发事件(谁推动了变化、为什么)
5. timeline:period 之间用合理时间间隔(根据 T 维度)

【对接 OpsProfile cell_quota 的字段类型分配】
- IE/MR/TR/ABS 题需要 stable 或 evolving 字段
- KU 题需要 evolving 字段
- (KU, M3) 题需要 conflict 字段 — 必须设计!common_default 字段值故意反常识

【输出严格 JSON 格式】(不要 markdown 包裹)
{
  "chain_id": "chain_<uuid>",
  "chain_name": "...",
  "main_theme": "...",
  "fields": [
    {
      "name": "...",
      "type": "stable" | "evolving" | "conflict",
      "domain": "字段值的取值领域(如百分比/人名/类别)",
      "common_default": "若 type=conflict,LLM 训练分布的高频值",
      "injected_value": "若 type=conflict,我们注入的反常识值"
    },
    ...
  ],
  "periods": [
    {"period_idx": 0, "date": "YYYY-MM-DD", "state": {"field_name": "value", ...}},
    ...
  ],
  "events": [
    {"period_idx": int, "type": "update"|"reveal"|"injection", "field": "...",
     "old_value": "...", "new_value": "...", "trigger": "事件简述"},
    ...
  ]
}"""


def _build_skeleton_user_prompt(
    expanded_pool: list[Document],
    dims: ScenarioDimensions,
    profile: OpsProfileV3,
    n_periods: int,
    chain_idx: int,
) -> str:
    samples_summary = "\n".join(
        f"[doc {i}] doc_type={(d.metadata or {}).get('doc_type','?')} "
        f"date={(d.metadata or {}).get('date','?')} title='{d.title}'\n"
        f"  preview: {(d.content or '')[:250]}..."
        for i, d in enumerate(expanded_pool[:5])
    )
    # 提取 cell_quota 让 LLM 知道下游会出哪些题
    key_cells = sorted(
        [(ck, n) for ck, n in profile.cell_quota.items() if n > 0],
        key=lambda x: -x[1]
    )[:8]
    cells_str = "\n".join(f"  - {ck}: {n} 题" for ck, n in key_cells)

    return (
        f"【场景描述】\n"
        f"I (ingest channels): {dims.I_ingest_channels}\n"
        f"S (memory subject): {dims.S_memory_subject}\n"
        f"V (perspective): {dims.V_perspective}\n"
        f"T (temporal pattern): {dims.T_temporal_pattern}\n"
        f"cognitive_flavor: {dims.cognitive_flavor}\n\n"
        f"【ExpandedSeedPool 摘要】({len(expanded_pool)} 篇)\n"
        f"{samples_summary}\n\n"
        f"【下游 OpsProfile 核心 cells(下游会出这些类型的题,你设计的 fields/events 要支撑)】\n"
        f"{cells_str}\n\n"
        f"【任务】\n"
        f"为 chain {chain_idx + 1} 设计 skeleton:{n_periods} 个 period,"
        f"5-8 个 fields(必须含至少 1 个 stable + 1 个 evolving + 1 个 conflict),"
        f"events 应对应 KU + KU+M3 cells 的下游需求。"
        f"严格 JSON 输出。"
    )


def synthesize_chain_skeleton(
    expanded_pool: list[Document],
    dims: ScenarioDimensions,
    profile: OpsProfileV3,
    n_periods: int = 10,
    chain_idx: int = 0,
) -> Optional[dict]:
    """用 LLM 生成 chain skeleton JSON。失败返回 None。"""
    msgs = [
        {"role": "system", "content": SKELETON_SYSTEM},
        {"role": "user", "content": _build_skeleton_user_prompt(
            expanded_pool, dims, profile, n_periods, chain_idx
        )},
    ]
    try:
        data = config.chat_json(msgs, temperature=0.5, max_tokens=8192)
    except Exception as e:
        print(f"[skeleton] LLM 异常 chain={chain_idx}: {e}")
        return None
    return data


# ─────────────────────────────────────────────────────────────────────────────
# 2. Step 2: Period 文档合成(自然语言层)
# ─────────────────────────────────────────────────────────────────────────────

DOC_SYNTHESIS_SYSTEM = """你是 corpus 文档合成专家。\
基于 chain skeleton(结构层 ground truth)+ ExpandedSeedPool 风格,\
你要为指定 period 合成一篇自然语言文档。

【硬约束】
1. 文档必须自然提到该 period state 里【所有字段】的当前值
2. 风格、词汇、节奏【严格模仿】samples(同一组织语境)
3. 如果该 period 有 events,文档要自然描述(谁、为什么变)
4. metadata 必须含 date / author / doc_type(取自 skeleton)
5. content 长度 300-800 字,要有【真实细节】(数字、人名、动作)
6. 不要直接列字段表(像 dict 那样),要用叙述性段落,把字段值"埋"进去
7. 如果 skeleton 标记某 event 是【conflict / injection】,文档要【清晰陈述注入值】并给出证据,\
但不要破坏叙述自然性(让被测系统不得不读到这个反常识值)

【输出严格 JSON】(不要 markdown 包裹)
{
  "doc_id": "synth_<random>",
  "title": "...",
  "content": "...(300-800 字自然语言)",
  "metadata": {
    "date": "YYYY-MM-DD",
    "author": "...",
    "doc_type": "..."
  }
}"""


def _build_doc_synthesis_user_prompt(
    skeleton: dict,
    period_state: dict,
    period_events: list,
    period_idx: int,
    samples_summary: str,
    suggested_doc_type: str = "weekly_report",
) -> str:
    events_str = "无变化事件" if not period_events else "\n".join(
        f"  - {e.get('type','?')}: field='{e.get('field','?')}' "
        f"old={e.get('old_value','?')} → new={e.get('new_value','?')} "
        f"trigger='{e.get('trigger','?')}'"
        for e in period_events
    )
    return (
        f"【chain skeleton 主题】{skeleton.get('main_theme','?')}\n"
        f"【chain skeleton fields 定义】\n"
        f"{json.dumps(skeleton.get('fields', []), ensure_ascii=False, indent=2)}\n\n"
        f"【该 period 的 state(必须 100% 体现)】period_idx={period_idx}\n"
        f"{json.dumps(period_state, ensure_ascii=False, indent=2)}\n\n"
        f"【该 period 的 events】\n"
        f"{events_str}\n\n"
        f"【ExpandedSeedPool 风格参考】\n{samples_summary}\n\n"
        f"【建议 doc_type】{suggested_doc_type}\n\n"
        f"请合成 1 篇文档,严格 JSON。"
    )


def synthesize_period_doc(
    skeleton: dict,
    period_idx: int,
    samples: list[Document],
    suggested_doc_type: str = "weekly_report",
) -> Optional[dict]:
    """合成 1 篇文档。返回 ingest_docs[i] 用的 dict 格式(含 content)。"""
    periods = skeleton.get("periods", [])
    if period_idx >= len(periods):
        return None
    period_info = periods[period_idx]
    period_state = period_info.get("state", {})
    # 收集本 period 的 events
    period_events = [
        e for e in skeleton.get("events", [])
        if e.get("period_idx") == period_idx
    ]
    samples_summary = "\n".join(
        f"[doc {i}] doc_type={(d.metadata or {}).get('doc_type','?')} "
        f"title='{d.title}'\n  preview: {(d.content or '')[:250]}..."
        for i, d in enumerate(samples[:3])
    )
    msgs = [
        {"role": "system", "content": DOC_SYNTHESIS_SYSTEM},
        {"role": "user", "content": _build_doc_synthesis_user_prompt(
            skeleton, period_state, period_events, period_idx,
            samples_summary, suggested_doc_type
        )},
    ]
    try:
        data = config.chat_json(msgs, temperature=0.7, max_tokens=4096)
    except Exception as e:
        print(f"[doc-synth] period={period_idx} LLM 异常: {e}")
        return None

    # 组装 ingest_docs 字典(含 content,自包含)
    doc_id = str(data.get("doc_id", f"synth_{uuid.uuid4().hex[:8]}"))
    return {
        "doc_id": doc_id,
        "title": str(data.get("title", "")),
        "content": str(data.get("content", "")),
        "metadata": {
            **(data.get("metadata", {}) or {}),
            "synthetic": True,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# 3. 主入口:synthesize_corpus
# ─────────────────────────────────────────────────────────────────────────────

def _suggest_doc_type(I_channels: list, period_idx: int) -> str:
    """根据 I 通道循环建议 doc_type。"""
    if not I_channels:
        return "long_document"
    return I_channels[period_idx % len(I_channels)]


def synthesize_corpus(
    expanded_pool: list[Document],
    dimensions: ScenarioDimensions,
    profile: OpsProfileV3,
    n_chains: int = 1,
    n_periods_per_chain: int = 10,
    n_docs_per_period: int = 1,
    verbose: bool = True,
) -> list[Chain]:
    """主入口:合成 corpus → list[Chain]。

    每个 chain 的 qas=[] 留给 Stage D 签发。
    """
    chains: list[Chain] = []
    for chain_idx in range(n_chains):
        if verbose:
            print(f"\n[stage_c] === Chain {chain_idx+1}/{n_chains} ===")
            print(f"[stage_c] Step 1: 合成 chain skeleton...")
        skeleton = synthesize_chain_skeleton(
            expanded_pool, dimensions, profile,
            n_periods=n_periods_per_chain, chain_idx=chain_idx,
        )
        if skeleton is None:
            print(f"[stage_c] chain {chain_idx} skeleton 合成失败,跳过")
            continue
        if verbose:
            print(f"[stage_c]   chain_name: {skeleton.get('chain_name')}")
            print(f"[stage_c]   main_theme: {skeleton.get('main_theme')}")
            print(f"[stage_c]   fields ({len(skeleton.get('fields', []))}):")
            for f in skeleton.get("fields", []):
                if f.get("type") == "conflict":
                    print(f"     - {f.get('name')} [{f.get('type')}] "
                          f"default='{f.get('common_default')}' "
                          f"injected='{f.get('injected_value')}'")
                else:
                    print(f"     - {f.get('name')} [{f.get('type')}]")
            print(f"[stage_c]   events: {len(skeleton.get('events', []))}")

        # Step 2: 合成每个 period 的文档
        periods: list[Period] = []
        for period_idx in range(n_periods_per_chain):
            period_info = (skeleton.get("periods") or [{}] * n_periods_per_chain)[
                period_idx if period_idx < len(skeleton.get("periods", [])) else -1
            ]
            period_docs = []
            for doc_idx in range(n_docs_per_period):
                doc_type_hint = _suggest_doc_type(
                    dimensions.I_ingest_channels, period_idx + doc_idx
                )
                if verbose:
                    print(f"[stage_c]   period {period_idx} doc {doc_idx+1}/"
                          f"{n_docs_per_period} ({doc_type_hint})...",
                          end=" ", flush=True)
                doc_dict = synthesize_period_doc(
                    skeleton, period_idx, expanded_pool, doc_type_hint
                )
                if doc_dict is None:
                    print("[失败]")
                    continue
                period_docs.append(doc_dict)
                if verbose:
                    print(f"len={len(doc_dict.get('content',''))}c")
            periods.append(Period(
                period_idx=period_idx,
                date=str(period_info.get("date", "")),
                timestamp=str(period_info.get("date", "")),
                ingest_docs=period_docs,
                state=period_info.get("state", {}),
            ))

        chain = Chain(
            chain_id=str(skeleton.get("chain_id", f"chain_{chain_idx}")),
            chain_name=str(skeleton.get("chain_name", f"chain_{chain_idx}")),
            cluster=skeleton.get("main_theme"),
            period_count=len(periods),
            periods=periods,
            qas=[],
        )
        # 把 skeleton 完整存进 chain.cluster 旁边的 metadata 字段……
        # 简化:直接放进第一个 period 的 state["_skeleton"](便于后续 Stage D 用)
        if periods:
            periods[0].state["_skeleton"] = skeleton
        chains.append(chain)
    return chains


# ─────────────────────────────────────────────────────────────────────────────
# 4. 测试入口
# ─────────────────────────────────────────────────────────────────────────────

def main():
    """W1.5 V3 T6 测试:合成 1 chain × 5 periods(小规模)。"""
    from .schema import RefinedScenarioSpec
    from .stage_a_dimensions import infer_dimensions
    from .stage_b_ops_profile import build_ops_profile

    spec = RefinedScenarioSpec(
        name="office_engineering_weekly",
        description_refined=(
            "我是一名业务线 leader,负责每周审阅 AI 工程团队的迭代进展。"
            "团队产出周报记录项目状态,关键字段包括 P0 缺陷率、Oncall 数量、负责人、"
            "项目里程碑。我需要追踪字段变化、识别过期信息、判断决策依据。"
        ),
        corpus_samples=[
            Document(doc_id="d1", title="AI 工程周报 W17",
                     content="本周 P0 缺陷率 20%(2/10),Oncall 数量 10。负责人:张三。"
                             "本周完成需求评审 3 项。下周计划:启动新索引架构方案。",
                     metadata={"date": "2025-04-17", "doc_type": "周报", "author": "张三"}),
            Document(doc_id="d2", title="AI 工程周报 W18",
                     content="本周 P0 缺陷率 17%(2/12)。Oncall 数量 9(下降 1)。负责人:张三。"
                             "本周完成新索引方案设计评审,进入 prototype。",
                     metadata={"date": "2025-04-24", "doc_type": "周报", "author": "张三"}),
            Document(doc_id="d3", title="AI 工程周报 W19",
                     content="本周 P0 缺陷率 14%。Oncall 数量 8。负责人变更为李四(W19 起)。"
                             "新索引 prototype 上线灰度,迁移 5% 流量。",
                     metadata={"date": "2025-05-01", "doc_type": "周报", "author": "李四"}),
        ],
        perspective="cross_line_leader",
        subject_type="project",
        temporal_pattern="weekly",
        target_size=50,
        cognitive_flavor_hint="mixed",
    )

    # 跑 Stage A
    print("=== Stage A: 维度推断 ===")
    dims = infer_dimensions(spec, use_llm=False)  # 用 heuristic 省 LLM 调用
    print(f"  I={dims.I_ingest_channels}, S={dims.S_memory_subject}, V={dims.V_perspective}, "
          f"T={dims.T_temporal_pattern}, flavor={dims.cognitive_flavor}")

    # 跑 Stage B(W1.5 V3.0:target_size=30 节省合成成本,后续可升到 50)
    print("\n=== Stage B: 配额计划 ===")
    profile = build_ops_profile(dims, target_size=30, enforce_five_organs_full=True)
    print(f"  matched: {profile.profile_name}, cell_quota sum={sum(profile.cell_quota.values())}")
    print(f"  核心 cells:")
    for ck in sorted(profile.cell_quota, key=lambda k: -profile.cell_quota[k])[:5]:
        print(f"    {ck}: {profile.cell_quota[ck]}")

    # 跑 Stage C(W1.5 V3.0:1 chain × 5 periods × 1 doc/period 验证 happy path)
    print("\n=== Stage C: Corpus 合成 ===")
    chains = synthesize_corpus(
        spec.corpus_samples, dims, profile,
        n_chains=1, n_periods_per_chain=5, n_docs_per_period=1,
    )
    print(f"\n=== ✓ 合成完成: {len(chains)} chains ===")
    for c in chains:
        print(f"  chain {c.chain_id}: {c.chain_name}")
        print(f"    periods: {c.period_count}, total docs: {sum(len(p.ingest_docs) for p in c.periods)}")
        for p in c.periods[:2]:
            print(f"    [period {p.period_idx}] date={p.date}")
            for doc in p.ingest_docs:
                print(f"      doc_id={doc.get('doc_id')}, title='{doc.get('title','')[:50]}'")
                print(f"        content_preview: {doc.get('content','')[:200]}...")
                print(f"        metadata: {doc.get('metadata',{})}")


if __name__ == "__main__":
    main()
