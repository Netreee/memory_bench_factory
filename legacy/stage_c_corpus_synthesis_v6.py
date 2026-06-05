"""
pipeline.stage_c_corpus_synthesis_v6 — Stage C V6: 自然合成 + 信号竞争。

V6 核心突破(redesign_v6.md §2 转变 2):
  失败模式不靠白名单反常识注入,改靠 corpus 内【自然信号竞争】。
  M3 = 字段自然演进(负责人 张三→李四),但旧值在多 period 高频出现、新值只在最后低频出现
       → 问'最新值'时系统记忆里旧值信号更强,倾向答错成旧值(conflict defaulting)。

vs V5:
  - 删白名单约束(不再 Apple→Pyongyang)
  - skeleton 字段加 "evolving_signal_competition" 类型(M3 材料)
  - period 文档合成复用 V3(忠实体现 state 即自动实现信号竞争)

跑法:
  cd memory_bench_factory
  ./venv/bin/python -m pipeline.stage_c_corpus_synthesis_v6
"""
from __future__ import annotations
from pathlib import Path
from typing import Optional
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config

from .schema import (
    Document, Chain, Period, ScenarioDimensions, OpsProfileV3, CellRequirement,
)
# 复用 V3 的 doc_type 推断(period 文档合成用 V6 专用函数,见下方 synthesize_period_doc_v6)
from .stage_c_corpus_synthesis import _suggest_doc_type


SKELETON_SYSTEM_V6 = """你是 memory benchmark corpus 合成专家。\
为一个测试 chain 设计【结构骨架】(skeleton),作为后续生成文档和题目的 ground truth。

【V6 设计原则 — 自然合成,信号竞争(不用反常识!)】
1. 每个 chain 围绕一个【主题】(项目/产品线/客户案例)
2. 设计 5-8 个【字段(fields)】,每个字段有类型:
   - "stable": 整个 chain 值不变(用于 IE/AR 题)
   - "evolving": 跨 period 自然演进,新值替代旧值(用于 KU 普通题)
   - "evolving_signal_competition": ★ 用于 (KU/TR, M3) 题。字段自然演进,
     但【旧值在多个 period 高频出现,新值只在最后 1-2 个 period 低频出现】。
     这样问"最新值"时,被测系统记忆里旧值信号更强 → 容易答错成旧值(conflict defaulting)。

3. ★【信号竞争字段硬约束】(M3 材料,核心):
   - 必须是【自然、合理】的字段演进(负责人交接、版本升级、指标变化、状态切换)
   - 旧值/新值都是【该场景里完全正常的值】 — 严禁反常识!严禁 Apple→Pyongyang 这种!
   - 旧值在前 N-1 个 period 反复出现,新值只在最后 1 个 period 出现
   - signal_ratio = 旧值出现 period 数 / 新值出现 period 数,应 ≥ 3
   - 例:负责人 张三(period 0~3 都是张三)→ 李四(仅 period 4)→ signal_ratio=4
   - ★【优先用人名/专有名词/有区分度的类别值】作 signal_competition 字段,
     避免纯数字(15/10 这类数字易与其他字段的数字混淆,破坏信号计数)

4. 每个 period 的 state 列出所有字段当前值
5. events 记录字段变化(【自然触发】,如"项目交接"/"版本发布",不用合规剧情/制裁清单这种戏剧化包装)
6. ★【种子扰动 — 破除黏滞】不要照搬【风格参考样本】里的具体值(人名/公司名/数值)。
   样本只供参考文风与字段类型;请为本 chain 生成【一套全新的、合理的】具体值
   (换不同人名、不同数值起点与轨迹),确保多个 chain 之间彼此独立、不雷同。

【输出严格 JSON】(不要 markdown 包裹)
{
  "chain_id": "chain_<short_id>",
  "chain_name": "...",
  "main_theme": "...",
  "fields": [
    {
      "name": "...",
      "type": "stable" | "evolving" | "evolving_signal_competition",
      "domain": "字段取值领域",
      "values_by_period": {"0": "v0", "1": "v1", ...},
      "signal_competition": {
        "old_value": "...", "old_freq": <旧值出现 period 数>,
        "new_value": "...", "new_freq": <新值出现 period 数>,
        "signal_ratio": <old_freq/new_freq>
      }
    }
  ],
  "periods": [
    {"period_idx": 0, "date": "YYYY-MM-DD", "state": {"field_name": "value", ...}}
  ],
  "events": [
    {"period_idx": int, "type": "update", "field": "...",
     "old_value": "...", "new_value": "...", "trigger": "自然事件简述"}
  ]
}"""


def _format_cell_hints_v6(cell_requirements: list) -> str:
    """格式化 cell 题型 hint(V6 无白名单)+ 计算需要多少 signal_competition 字段。"""
    lines = []
    m3_cells = []
    for req in cell_requirements:
        lines.append(
            f"- ({req.cap}, {req.fm or 'None'}) quota={req.quota}: "
            f"required={req.required_field_types}"
        )
        if req.fm == "M3":
            m3_cells.append((req.cap, req.quota))
    hint = "\n".join(lines)
    if m3_cells:
        m3_total = sum(q for _, q in m3_cells)
        # 每道 M3 题尽量配一个不同的 signal_competition 字段(增加 M3 覆盖多样性),
        # 但封顶 3 个,避免在单一周报场景里堆砌过多不自然的演进字段。
        n_sc = max(1, min(m3_total, 3))
        hint += (
            f"\n\n★ 共 {m3_total} 道 M3 题(来自 {[c for c, _ in m3_cells]})"
            f" → 必须设计 ≥{n_sc} 个【不同语义】的 'evolving_signal_competition' 字段作 M3 材料"
            f"(如负责人、对接人、版本、状态等彼此独立的演进字段)"
        )
    return hint


def _build_skeleton_user_prompt_v6(
    expanded_pool: list,
    dims: ScenarioDimensions,
    cell_requirements: list,
    n_periods: int,
    chain_idx: int,
) -> str:
    samples_summary = "\n".join(
        f"[doc {i}] doc_type={(d.metadata or {}).get('doc_type','?')} "
        f"date={(d.metadata or {}).get('date','?')} title='{d.title}'\n"
        f"  preview: {(d.content or '')[:250]}..."
        for i, d in enumerate(expanded_pool[:5])
    )
    cell_hints = _format_cell_hints_v6(cell_requirements)
    return (
        f"【场景维度】\n"
        f"I={dims.I_ingest_channels}, S={dims.S_memory_subject}, "
        f"V={dims.V_perspective}, T={dims.T_temporal_pattern}\n\n"
        f"【ExpandedSeedPool 摘要】({len(expanded_pool)} 篇)\n{samples_summary}\n\n"
        f"【cell 题型 hint(V6 无白名单)】\n{cell_hints}\n\n"
        f"【任务】\n"
        f"为 chain {chain_idx+1} 设计 skeleton:{n_periods} 个 period,5-8 个 fields,"
        f"含 ≥1 stable + ≥1 evolving + 按 M3 配额设计 evolving_signal_competition 字段。"
        f"所有字段值都是场景里正常的值(不用反常识)。严格 JSON 输出。"
    )


def synthesize_chain_skeleton_v6(
    expanded_pool: list,
    dims: ScenarioDimensions,
    cell_requirements: list,
    n_periods: int = 10,
    chain_idx: int = 0,
) -> Optional[dict]:
    msgs = [
        {"role": "system", "content": SKELETON_SYSTEM_V6},
        {"role": "user", "content": _build_skeleton_user_prompt_v6(
            expanded_pool, dims, cell_requirements, n_periods, chain_idx,
        )},
    ]
    try:
        data = config.chat_json(msgs, temperature=0.5, max_tokens=8192)
    except Exception as e:
        print(f"[skeleton-v6] LLM 异常 chain={chain_idx}: {e}")
        return None
    return data


# ─────────────────────────────────────────────────────────────────────────────
# V6 专用 period 文档合成(信号竞争:严格按 period 写,严禁回顾/展望)
# ─────────────────────────────────────────────────────────────────────────────

DOC_SYNTHESIS_SYSTEM_V6 = """你是 corpus 文档合成专家。基于 chain skeleton 某 period 的 state,\
合成一篇自然语言文档。

【硬约束】
1. 文档自然提到该 period state 里【所有字段的当前值】
2. ★【信号竞争铁律】对标注为 signal_competition 的字段:
   - 只写【当前 period 的值】
   - 严禁提及该字段的历史值或未来值
   - 严禁出现"从 X 变为 Y" / "将交接给" / "此前是" / "由 X 接替" 这类回顾或展望
   - 例:当前 period 负责人是"张三" → 文档只能出现"张三",严禁出现"李四"
   - (这是制造旧值高频/新值低频信号竞争的核心机制,违反则 benchmark 失效)
3. ★【单次提及铁律】每个 signal_competition 字段的当前值在本文档中【只自然提及 1 次】,
   严禁同一文档内反复重复该值(反复提及会在其所在 period 虚增信号,
   削弱"旧值跨多 period 高频 / 新值单 period 低频"的竞争结构 → benchmark 失效)
4. 风格、词汇、节奏模仿 samples(同一组织语境)
5. content 300-800 字,叙述性段落(不要列字段表)
6. 数字类字段写清楚归属(如"平均响应时间 15ms"),避免与其他数字混淆

【输出严格 JSON】
{"doc_id":"synth_<id>","title":"...","content":"...","metadata":{"date":"...","author":"...","doc_type":"..."}}"""


def _build_doc_user_prompt_v6(skeleton, period_state, period_idx,
                               sc_field_names, samples_summary, doc_type):
    sc_note = "(本 period 无 signal_competition 字段)"
    if sc_field_names:
        sc_lines = []
        for fn in sc_field_names:
            cur_val = period_state.get(fn, "?")
            sc_lines.append(f"  - {fn}: 当前值='{cur_val}'(★只写这个值,严禁提其他 period 的值)")
        sc_note = "★ 信号竞争字段(严禁回顾/展望):\n" + "\n".join(sc_lines)
    return (
        f"【chain 主题】{skeleton.get('main_theme','?')}\n"
        f"【period {period_idx} 的 state(必须 100% 体现当前值)】\n"
        f"{json.dumps(period_state, ensure_ascii=False, indent=2)}\n\n"
        f"{sc_note}\n\n"
        f"【风格参考】\n{samples_summary}\n\n"
        f"【建议 doc_type】{doc_type}\n\n"
        f"请合成 1 篇文档,严格 JSON。"
    )


def synthesize_period_doc_v6(skeleton, period_idx, samples, doc_type="weekly_report"):
    """V6 专用:signal_competition 字段严格按 period 写,严禁回顾/展望。"""
    import uuid
    periods = skeleton.get("periods", [])
    if period_idx >= len(periods):
        return None
    period_state = periods[period_idx].get("state", {})
    sc_field_names = [f.get("name") for f in skeleton.get("fields", [])
                      if f.get("type") == "evolving_signal_competition"]
    samples_summary = "\n".join(
        f"[doc {i}] title='{d.title}' preview:{(d.content or '')[:200]}..."
        for i, d in enumerate(samples[:3])
    )
    msgs = [
        {"role": "system", "content": DOC_SYNTHESIS_SYSTEM_V6},
        {"role": "user", "content": _build_doc_user_prompt_v6(
            skeleton, period_state, period_idx, sc_field_names, samples_summary, doc_type)},
    ]
    try:
        data = config.chat_json(msgs, temperature=0.7, max_tokens=4096)
    except Exception as e:
        print(f"[doc-synth-v6] period={period_idx} LLM 异常: {e}")
        return None
    doc_id = str(data.get("doc_id", f"synth_v6_{uuid.uuid4().hex[:8]}"))
    return {
        "doc_id": doc_id,
        "title": str(data.get("title", "")),
        "content": str(data.get("content", "")),
        "metadata": {**(data.get("metadata", {}) or {}), "synthetic": True},
    }


def synthesize_corpus_v6(
    expanded_pool: list,
    dimensions: ScenarioDimensions,
    cell_requirements: list,
    n_chains: int = 1,
    n_periods_per_chain: int = 10,
    n_docs_per_period: int = 1,
    verbose: bool = True,
) -> list:
    """V6 主入口:自然合成 corpus(信号竞争代替白名单)。"""
    chains: list = []
    for chain_idx in range(n_chains):
        if verbose:
            print(f"\n[stage_c_v6] === Chain {chain_idx+1}/{n_chains} ===")
            print(f"[stage_c_v6] Step 1: V6 skeleton 合成(自然 + 信号竞争)...")
        skeleton = synthesize_chain_skeleton_v6(
            expanded_pool, dimensions, cell_requirements,
            n_periods=n_periods_per_chain, chain_idx=chain_idx,
        )
        if skeleton is None:
            print(f"[stage_c_v6] chain {chain_idx} skeleton 失败,跳过")
            continue
        if verbose:
            print(f"[stage_c_v6]   chain_name: {skeleton.get('chain_name')}")
            print(f"[stage_c_v6]   main_theme: {skeleton.get('main_theme')}")
            print(f"[stage_c_v6]   fields ({len(skeleton.get('fields', []))}):")
            for f in skeleton.get("fields", []):
                if f.get("type") == "evolving_signal_competition":
                    sc = f.get("signal_competition", {})
                    print(f"     - ★ {f.get('name')} [signal_competition] "
                          f"old='{sc.get('old_value')}'({sc.get('old_freq')}x) "
                          f"new='{sc.get('new_value')}'({sc.get('new_freq')}x) "
                          f"ratio={sc.get('signal_ratio')}")
                else:
                    print(f"     - {f.get('name')} [{f.get('type')}]")
            print(f"[stage_c_v6]   events: {len(skeleton.get('events', []))}")

        # Step 2: period 文档合成(V6 专用:signal_competition 字段严格按 period 写)
        periods: list = []
        period_info_list = skeleton.get("periods", [])
        for period_idx in range(n_periods_per_chain):
            period_info = period_info_list[period_idx] if period_idx < len(period_info_list) else {}
            period_docs = []
            for doc_idx in range(n_docs_per_period):
                doc_type_hint = _suggest_doc_type(
                    dimensions.I_ingest_channels, period_idx + doc_idx
                )
                if verbose:
                    print(f"[stage_c_v6]   period {period_idx} doc {doc_idx+1}/"
                          f"{n_docs_per_period} ({doc_type_hint})...",
                          end=" ", flush=True)
                doc_dict = synthesize_period_doc_v6(
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
            chain_id=str(skeleton.get("chain_id", f"chain_v6_{chain_idx}")),
            chain_name=str(skeleton.get("chain_name", f"chain_v6_{chain_idx}")),
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
    """V6 T2 测试:Stage A → B → C V6,看信号竞争字段是否自然生成。"""
    from .schema import RefinedScenarioSpec
    from .stage_a_dimensions import infer_dimensions
    from .stage_b_ops_profile import build_ops_profile
    from .cell_requirements_table import build_cell_requirements

    spec = RefinedScenarioSpec(
        name="office_v6_test",
        description_refined="AI 工程团队周报,业务线 leader 视角,关注字段变化与负责人交接",
        corpus_samples=[
            Document(doc_id="d1", title="AI 工程周报 W17",
                     content="本周 P0 缺陷率 20%。Oncall 数量 10。负责人:张三。",
                     metadata={"date": "2025-04-17", "doc_type": "周报", "author": "张三"}),
            Document(doc_id="d2", title="AI 工程周报 W18",
                     content="本周 P0 缺陷率 17%。Oncall 数量 9。负责人:张三。",
                     metadata={"date": "2025-04-24", "doc_type": "周报", "author": "张三"}),
        ],
        perspective="cross_line_leader",
        subject_type="project",
        temporal_pattern="weekly",
        target_size=10,
    )

    print("=== Stage A ===")
    dims = infer_dimensions(spec, use_llm=False)
    print("\n=== Stage B ===")
    profile = build_ops_profile(dims, target_size=10, enforce_five_organs_full=False)
    cell_reqs = build_cell_requirements(profile)
    print(f"  {len(cell_reqs)} cell requirements, "
          f"M3 cells: {[(r.cap, r.fm) for r in cell_reqs if r.fm=='M3']}")

    print("\n=== Stage C V6(信号竞争)===")
    chains = synthesize_corpus_v6(
        spec.corpus_samples, dims, cell_reqs,
        n_chains=1, n_periods_per_chain=5, n_docs_per_period=1,
    )

    print(f"\n=== ✓ V6 Stage C: {len(chains)} chains ===")
    for c in chains:
        skeleton = c.periods[0].state.get("_skeleton", {})
        sc_fields = [f for f in skeleton.get("fields", [])
                     if f.get("type") == "evolving_signal_competition"]
        print(f"\n  chain: {c.chain_name}")
        print(f"  ★★ 信号竞争字段 {len(sc_fields)} 个(V6 M3 材料,无反常识):")
        for f in sc_fields:
            sc = f.get("signal_competition", {})
            print(f"    - {f.get('name')}: {sc.get('old_value')}({sc.get('old_freq')}x) "
                  f"→ {sc.get('new_value')}({sc.get('new_freq')}x), "
                  f"ratio={sc.get('signal_ratio')}")
            print(f"      values_by_period: {f.get('values_by_period')}")
        # 验证 doc 文本里旧值/新值频率
        print(f"  --- 验证 corpus 文本信号频率 ---")
        for f in sc_fields:
            sc = f.get("signal_competition", {})
            old_v, new_v = str(sc.get("old_value", "")), str(sc.get("new_value", ""))
            old_count = sum(
                doc.get("content", "").count(old_v)
                for p in c.periods for doc in p.ingest_docs
            ) if old_v else 0
            new_count = sum(
                doc.get("content", "").count(new_v)
                for p in c.periods for doc in p.ingest_docs
            ) if new_v else 0
            print(f"    {f.get('name')}: 文本里 '{old_v}' 出现 {old_count} 次, "
                  f"'{new_v}' 出现 {new_count} 次 "
                  f"(实际 signal_ratio={old_count/max(1,new_count):.1f})")


if __name__ == "__main__":
    main()
