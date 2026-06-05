"""
pipeline.stage_d_question_synthesis — Stage D V3.1: cell_quota + 三轴算子配额双重消费。

V3.1 改动(相对 V3 patch,非 V4 重构):
  - **qid 改用 chain-level deterministic counter**(取消 4-hex 撞库风险,SA2 P0-4)
  - **三轴算子配额(F/E/Q)真消费**(SA2 P1-10 + SA3 算子标签准确率 38.9% 的根源):
    ① synthesize_questions_for_chain 维护 remaining_{formation,evolution,query} counter
    ② LLM system prompt 注入"当前算子配额剩余" + 完整 F/E/Q 算子定义
    ③ 后处理裁决:LLM 选了已用完的算子 → 自动 fallback 到剩余最多的算子
  - **ABS 题 prompt 硬约束** canonical 必填 sentinel(SA3 P0)

V4 留给下一迭代:LLM-as-Curator + 多轮自检 + M3 反常识强度自检 + MR 多 period gold 校验。

跑法:
  cd memory_bench_factory
  ./venv/bin/python -m pipeline.stage_d_question_synthesis
"""
from __future__ import annotations
from pathlib import Path
from typing import Optional
from collections import Counter
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config

from .schema import (
    Chain, Period, Question, OpsProfileV3,
    parse_cell_key, CAPABILITIES, FAILMODES,
)


# ─────────────────────────────────────────────────────────────────────────────
# 1. 算子/能力/失败模式 定义(给 LLM 选标签参考)
# ─────────────────────────────────────────────────────────────────────────────

CAPABILITY_DEF = {
    "IE": "Information Extraction — 单 period 内字段值回填(如'本周 P0 缺陷率是多少')",
    "MR": "Multi-Session Reasoning — 跨 period 综合(如'10 周内 P0 最高的是哪周')",
    "TR": "Temporal Reasoning — 时序推理(如'X 字段在哪两个 period 之间反转')",
    "KU": "Knowledge Update — 识别字段更新(如'最新的负责人是谁,而非旧负责人')",
    "ABS": "Abstention — 识别 false premise / 应拒答的题(如问 corpus 里没说的字段)",
    "LRU": "Long-Range Understanding — 跨长上下文整体推理",
}

FAILMODE_DEF = {
    "M1": "Plan over-decomposition — 看似多跳实则单跳的事实题,引诱 R3 过度拆解",
    "M2": "Evidence collapse — 答案在叶节点细节但与高层摘要语义偏离,R3 多步检索会偏离",
    "M3": "Conflict defaulting — 反常识 gold + 训练分布常识默认值并存,R3 反思会挑常识 → 错",
    "M4": "Paraphrase distractor — 原文逐字 vs 同义改写,R3 反思偏向语义近似 → 选错",
    "M5": "Format mismatch — 系统输出格式与 gold 字面不一致(如 UK vs United Kingdom),"
          "测评测口径鲁棒性",
}

FORMATION_DEF = {
    "F1": "切片:文本切成 chunk,存为向量或文本块。适合简单字面事实题(无需关系建模)。",
    "F2": "原子事实抽取:LLM 抽取(entity, attribute, value)三元组。适合精确字段查询题。",
    "F3": "实体-关系三元组抽取:显式建模实体节点+关系边。适合多 hop 关联题(必须知道实体间关系)。",
    "F4": "多层异构结构:聚类摘要/社区分割/多层级树。适合既需细节又需全局摘要的题(corpus 必须有摘要层级)。",
}

EVOLUTION_DEF = {
    "E1": "只增不减:新数据直接追加,冲突靠时间戳过滤。适合 IE/MR 题(无版本演化)。",
    "E2": "缓存置换:LRU/FIFO 或基于热度的内存页调度。适合考'选择性遗忘'的题。",
    "E3": "拓扑融合:节点合并(Entity Resolution)或增边修改。适合多实体合并/同名消歧题。",
    "E4": "显式修补:检测冲突后覆盖式更新或 Edit Path。适合知识更新/字段被覆盖的题(KU 主用)。",
}

QUERY_DEF = {
    "Q1": "相似度检索:dense embedding 或 BM25。适合单跳事实题(IE/简单 KU)。",
    "Q2": "图遍历:PPR/BFS 图上游走或摘要树遍历。适合多跳关联题(MR/复杂 TR)。",
    "Q3": "生成路由:先 LLM 生成中间变量(子问题/SQL/Clue),再用它去查。适合 ABS 拒答 / 复杂决策题。",
}


# ─────────────────────────────────────────────────────────────────────────────
# 2. 配额裁决(LLM 选的算子如已用完 → fallback 到剩余多的)
# ─────────────────────────────────────────────────────────────────────────────

def _format_remaining(quota: dict) -> str:
    """格式化算子剩余配额给 LLM 看。"""
    items = [(k, v) for k, v in quota.items() if v > 0]
    return ", ".join(f"{k}={v}" for k, v in items) or "(全部已用完)"


def _adjust_op_to_quota(
    chosen_op: Optional[str],
    remaining: dict,
) -> tuple:
    """LLM 选的算子如已用完,自动 fallback 到剩余最多的;同时扣减 counter。

    返回 (final_op, was_adjusted)。
    """
    if chosen_op is None:
        return None, False
    if remaining.get(chosen_op, 0) > 0:
        remaining[chosen_op] -= 1
        return chosen_op, False
    # 已用完 → 选剩余最多的
    available = [(op, n) for op, n in remaining.items() if n > 0]
    if not available:
        # 全部用完(理论上不应发生,除非 quota sum < 生成题数)
        return chosen_op, False
    available.sort(key=lambda x: -x[1])
    new_op = available[0][0]
    remaining[new_op] -= 1
    return new_op, True


# ─────────────────────────────────────────────────────────────────────────────
# 3. Prompt 构造
# ─────────────────────────────────────────────────────────────────────────────

SYNTH_SYSTEM_TEMPLATE = """你是 memory benchmark 题目生成专家。给定一个 chain skeleton 和一个 cell((capability, failmode)),\
生成指定数量的题目,严格符合 cell 要求,**并按【算子配额引导】选择三轴算子标签**。

【每道题必填字段】
- question: 中文问句
- field_name: 涉及的字段名(从 skeleton.fields 取)
- answer_by_period: dict {{period_idx_str: list[str] 等价集}}
- answer_canonical_by_period: dict {{period_idx_str: str 单值标准答案}}
- failmode_signature: 失败模式指纹(M3 必填 common_default+injected_value;M4 必填 distractor_options)
- formation_op / evolution_op / query_op: 三轴算子标签(必看下面定义 + ★配额引导)
- rationale: 一句话设计依据

【capability 题型指南】
- IE: 单 period 单字段回填(question 提到具体 period 或 timestamp)
- MR: 跨 ≥2 period 综合(最大值/总和/比较)
- TR: 时序推理(变化时机/排序/相对)
- KU: 识别字段最新值(往往含'最新'/'现在'/'当前'等词)
- ABS ★【硬约束】: 问 corpus 里【未提及】的字段或 false premise
       - answer_canonical_by_period 必须为每个 period 填一个【拒答 sentinel】
       - 推荐值: "INSUFFICIENT_EVIDENCE" 或 "未提及" 或 "信息不足"
       - 严禁返回空 dict {{}};严禁填具体数值或事实

【failmode 设计指南】
- M1: 看似 2 hop 实际 1 hop 的事实题
- M2: 答案在叶节点单文档细节,但摘要级语义会被误导
- M3 ★ 核心: 必须基于 skeleton 中 type='conflict' 的字段。
       answer 用 injected_value;failmode_signature 必填:
       {{"type":"conflict_defaulting", "common_default":..., "injected_value":...}}
- M4: question 含 paraphrasable 子句,answer 等价集包含逐字 + 同义改写,
       failmode_signature: {{"type":"paraphrase_distractor",
       "verbatim_span": "...", "distractor_options": ["..."]}}
- M5: answer 等价集 ≥7 个变体(数字/百分号/中英文 etc.),
       failmode_signature: {{"type":"format_mismatch", "equivalent_set": [...]}}
- None: signature={{}}

【三轴算子选择指南】(必看!严格按定义选,不要凭关键词触发)

Formation 算子(F1-F4):
{formation_def}

Evolution 算子(E1-E4):
{evolution_def}

Query 算子(Q1-Q3):
{query_def}

【★ 当前算子配额剩余】(优先选剩余多的!如某算子已用完=0,严禁选择!)
formation_remaining: {formation_remaining}
evolution_remaining: {evolution_remaining}
query_remaining: {query_remaining}

【输出严格 JSON】(不要 markdown 包裹)
{{
  "questions": [
    {{
      "question": "...",
      "field_name": "...",
      "answer_by_period": {{"0":["..."]}},
      "answer_canonical_by_period": {{"0":"..."}},
      "failmode_signature": {{...}},
      "formation_op": "F2",
      "evolution_op": "E4" or null,
      "query_op": "Q1",
      "rationale": "..."
    }}
  ]
}}"""


def _build_system_prompt(
    remaining_formation: dict,
    remaining_evolution: dict,
    remaining_query: dict,
) -> str:
    """动态注入算子定义 + 当前剩余配额。"""
    formation_def_str = "\n".join(f"  {k}: {v}" for k, v in FORMATION_DEF.items())
    evolution_def_str = "\n".join(f"  {k}: {v}" for k, v in EVOLUTION_DEF.items())
    query_def_str = "\n".join(f"  {k}: {v}" for k, v in QUERY_DEF.items())
    return SYNTH_SYSTEM_TEMPLATE.format(
        formation_def=formation_def_str,
        evolution_def=evolution_def_str,
        query_def=query_def_str,
        formation_remaining=_format_remaining(remaining_formation),
        evolution_remaining=_format_remaining(remaining_evolution),
        query_remaining=_format_remaining(remaining_query),
    )


def _build_user_prompt(
    chain: Chain,
    skeleton: dict,
    capability: str,
    failmode: Optional[str],
    n_questions: int,
) -> str:
    fields_json = json.dumps(skeleton.get("fields", []), ensure_ascii=False, indent=2)
    periods_state = [
        {"period_idx": p.period_idx, "date": p.date, "state": p.state}
        for p in chain.periods
    ]
    for ps in periods_state:
        if "_skeleton" in ps.get("state", {}):
            ps["state"] = {k: v for k, v in ps["state"].items() if k != "_skeleton"}
    state_json = json.dumps(periods_state, ensure_ascii=False, indent=2)
    events_json = json.dumps(skeleton.get("events", []), ensure_ascii=False, indent=2)

    return (
        f"【chain skeleton】chain_id={chain.chain_id}, theme={skeleton.get('main_theme','?')}\n\n"
        f"【fields 定义】\n{fields_json}\n\n"
        f"【periods state】\n{state_json}\n\n"
        f"【events 时间线】\n{events_json}\n\n"
        f"【本次任务】\n"
        f"生成 {n_questions} 道题,严格符合 cell:\n"
        f"  capability = {capability} ({CAPABILITY_DEF.get(capability,'')})\n"
        f"  failmode   = {failmode or 'None'} "
        f"({FAILMODE_DEF.get(failmode,'(普通题)') if failmode else '(普通题)'})\n\n"
        f"严格 JSON 输出 {n_questions} 道题,三轴算子标签遵循算子配额引导。"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 4. cell-level 题目生成(含算子配额裁决 + qid deterministic counter)
# ─────────────────────────────────────────────────────────────────────────────

def synthesize_questions_for_cell(
    chain: Chain,
    skeleton: dict,
    capability: str,
    failmode: Optional[str],
    n_questions: int,
    qid_counter_start: int,
    remaining_formation: dict,
    remaining_evolution: dict,
    remaining_query: dict,
) -> tuple:
    """对一个 cell 调 LLM 生成 N 道题。

    Returns:
        (questions, qid_counter_after_this_cell)
    """
    if n_questions <= 0:
        return [], qid_counter_start

    msgs = [
        {"role": "system", "content": _build_system_prompt(
            remaining_formation, remaining_evolution, remaining_query
        )},
        {"role": "user", "content": _build_user_prompt(
            chain, skeleton, capability, failmode, n_questions
        )},
    ]
    try:
        data = config.chat_json(msgs, temperature=0.6, max_tokens=8192)
    except Exception as e:
        print(f"[synth] cell=({capability},{failmode}) LLM 异常: {e}")
        return [], qid_counter_start

    raw_qs = data.get("questions", [])
    out: list[Question] = []
    counter = qid_counter_start
    adjusted = 0

    for rq in raw_qs[:n_questions]:
        # qid: deterministic counter,chain-level 全局唯一(P0-4 fix)
        local_id = f"{capability}_{failmode or 'none'}_{counter:04d}"
        counter += 1

        # 三轴算子裁决(SA2 P1-10 fix)
        chosen_f = str(rq.get("formation_op", "F2"))
        chosen_e = rq.get("evolution_op")
        chosen_q = str(rq.get("query_op", "Q1"))

        final_f, adj_f = _adjust_op_to_quota(chosen_f, remaining_formation)
        final_e, adj_e = _adjust_op_to_quota(chosen_e, remaining_evolution)
        final_q, adj_q = _adjust_op_to_quota(chosen_q, remaining_query)
        if adj_f or adj_e or adj_q:
            adjusted += 1

        out.append(Question(
            qid=f"{chain.chain_id}__{local_id}",
            source_chain_id=chain.chain_id,
            question=str(rq.get("question", "")),
            answer_by_period={
                str(k): list(v) if isinstance(v, list) else [str(v)]
                for k, v in (rq.get("answer_by_period") or {}).items()
            },
            answer_canonical_by_period={
                str(k): str(v)
                for k, v in (rq.get("answer_canonical_by_period") or {}).items()
            },
            field_name=rq.get("field_name"),
            capability=capability,
            failmode=failmode,
            failmode_signature=rq.get("failmode_signature") or {},
            formation_op=final_f or "F2",
            evolution_op=final_e,
            query_op=final_q or "Q1",
        ))

    if adjusted > 0:
        print(f"      [quota-adjust] {adjusted}/{len(out)} 题算子被裁决到剩余配额")

    return out, counter


# ─────────────────────────────────────────────────────────────────────────────
# 5. chain-level + chains-level 主入口
# ─────────────────────────────────────────────────────────────────────────────

def synthesize_questions_for_chain(
    chain: Chain,
    profile: OpsProfileV3,
    verbose: bool = True,
) -> list[Question]:
    """对单个 chain 按 cell_quota 全部签发题目,同时消费三轴算子配额。"""
    skeleton = None
    if chain.periods and isinstance(chain.periods[0].state, dict):
        skeleton = chain.periods[0].state.get("_skeleton")
    if not skeleton:
        print(f"[synth] chain {chain.chain_id} 缺 skeleton,跳过")
        return []

    # 初始化三轴算子配额 counter(本 chain 独立)
    remaining_f = dict(profile.formation_quota)
    remaining_e = dict(profile.evolution_quota)
    remaining_q = dict(profile.query_quota)
    qid_counter = 0  # chain-level deterministic counter

    all_qs: list[Question] = []
    sorted_cells = sorted(
        profile.cell_quota.items(), key=lambda kv: (-kv[1], kv[0])
    )
    if verbose:
        print(f"[stage_d] chain {chain.chain_id}: {len(sorted_cells)} 个 cell 待签发")
        print(f"[stage_d]   初始算子配额: F={dict(remaining_f)}")
        print(f"[stage_d]                 E={dict(remaining_e)}")
        print(f"[stage_d]                 Q={dict(remaining_q)}")
    for ck, n in sorted_cells:
        if n <= 0:
            continue
        cap, fm = parse_cell_key(ck)
        if verbose:
            print(f"[stage_d]   cell ({cap},{fm or 'none'}): 配额 {n} 题",
                  end=" ", flush=True)
        qs, qid_counter = synthesize_questions_for_cell(
            chain, skeleton, cap, fm, n, qid_counter,
            remaining_f, remaining_e, remaining_q,
        )
        if verbose:
            print(f"→ 生成 {len(qs)} 题")
        all_qs.extend(qs)

    if verbose:
        print(f"[stage_d]   剩余算子配额(应接近 0):")
        print(f"                F={dict(remaining_f)}")
        print(f"                E={dict(remaining_e)}")
        print(f"                Q={dict(remaining_q)}")

    return all_qs


def synthesize_questions_for_chains(
    chains: list[Chain],
    profile: OpsProfileV3,
    verbose: bool = True,
) -> list[Chain]:
    """对所有 chain 签发题目。"""
    for chain in chains:
        qs = synthesize_questions_for_chain(chain, profile, verbose=verbose)
        chain.qas = qs
    return chains


# ─────────────────────────────────────────────────────────────────────────────
# 6. 测试入口
# ─────────────────────────────────────────────────────────────────────────────

def main():
    """W1.5 V3.1 patch 测试:Stage A→B→C→D 链路,验证三轴算子真消费。"""
    from .schema import RefinedScenarioSpec, Document, compute_stats, Benchmark
    from .stage_a_dimensions import infer_dimensions
    from .stage_b_ops_profile import build_ops_profile
    from .stage_c_corpus_synthesis import synthesize_corpus

    spec = RefinedScenarioSpec(
        name="office_engineering_weekly",
        description_refined=(
            "我是业务线 leader,每周审阅 AI 工程团队迭代进展。"
            "关键字段:P0 缺陷率、Oncall 数量、负责人、项目里程碑、SLA 达标率。"
        ),
        corpus_samples=[
            Document(doc_id="d1", title="AI 工程周报 W17",
                     content="本周 P0 缺陷率 20%(2/10),Oncall 数量 10。负责人:张三。",
                     metadata={"date": "2025-04-17", "doc_type": "周报"}),
            Document(doc_id="d2", title="AI 工程周报 W18",
                     content="本周 P0 缺陷率 17%。Oncall 数量 9。负责人:张三。",
                     metadata={"date": "2025-04-24", "doc_type": "周报"}),
        ],
        perspective="cross_line_leader",
        subject_type="project",
        temporal_pattern="weekly",
        target_size=15,
        cognitive_flavor_hint="mixed",
    )

    print("=== Stage A ===")
    dims = infer_dimensions(spec, use_llm=False)
    print(f"  I={dims.I_ingest_channels}, V={dims.V_perspective}, T={dims.T_temporal_pattern}")

    print("\n=== Stage B (target_size=15) ===")
    profile = build_ops_profile(dims, target_size=15, enforce_five_organs_full=True)
    print(f"  profile={profile.profile_name}, cell_quota sum={sum(profile.cell_quota.values())}")
    print(f"  formation_quota: {profile.formation_quota}")
    print(f"  evolution_quota: {profile.evolution_quota}")
    print(f"  query_quota: {profile.query_quota}")

    print("\n=== Stage C (3 periods) ===")
    chains = synthesize_corpus(
        spec.corpus_samples, dims, profile,
        n_chains=1, n_periods_per_chain=3, n_docs_per_period=1,
        verbose=True,
    )

    print(f"\n=== Stage D V3.1 (cell_quota + 三轴算子真消费) ===")
    chains = synthesize_questions_for_chains(chains, profile, verbose=True)

    total_qs = sum(len(c.qas) for c in chains)
    print(f"\n=== ✓ 总题数: {total_qs} ===")

    # 算子配额实际 vs 期望
    f_actual = Counter(q.formation_op for c in chains for q in c.qas)
    e_actual = Counter(q.evolution_op or "None" for c in chains for q in c.qas)
    q_actual = Counter(q.query_op for c in chains for q in c.qas)
    print(f"\n=== 算子配额实际 vs 期望(★ patch C 验证)===")
    print(f"  formation 期望 {profile.formation_quota}")
    print(f"  formation 实际 {dict(f_actual)}")
    print(f"  evolution 期望 {profile.evolution_quota}")
    print(f"  evolution 实际 {dict(e_actual)}")
    print(f"  query     期望 {profile.query_quota}")
    print(f"  query     实际 {dict(q_actual)}")

    # qid 唯一性验证(★ patch A.3 验证)
    all_qids = [q.qid for c in chains for q in c.qas]
    n_unique = len(set(all_qids))
    print(f"\n=== qid 唯一性(★ patch A.3 验证)===")
    print(f"  总 qid 数: {len(all_qids)}, unique: {n_unique}")
    print(f"  {'✓ 全部唯一' if len(all_qids) == n_unique else '✗ 有重复!'}")


if __name__ == "__main__":
    main()
