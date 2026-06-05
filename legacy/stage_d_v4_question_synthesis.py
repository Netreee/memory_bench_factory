"""
pipeline.stage_d_v4_question_synthesis — Stage D V4: LLM-as-Curator + 后处理质检 + 重试。

V4 是 V3.1 的"质量层"升级,非接口重构。V3.1 保留作对照(ablation 实验用)。

V4 4 个关键改进(基于 SA1+SA2+SA3 审查的 5 个失败案例):

  ① system prompt 加 5 个 negative examples(V3 失败案例直接写入):
     - M3 反常识强度不足(Minor/Critical 这种 LLM 无强先验的弱反差)
     - MR canonical 不完整(缺推理结果)
     - ABS canonical={} 空 dict
     - failmode_signature 空
     - 算子标签与 corpus 结构不一致(无图谱却标 F3/无层级却标 F4)

  ② self_check 字段(LLM 内自评 5 个布尔/枚举):
     - signature_complete (M3/M4/M5 必带)
     - abs_canonical_filled (ABS 题每 period 都填 sentinel)
     - mr_canonical_complete (MR 题 canonical 含完整推理结果)
     - m3_reverence_strength ("high"/"medium"/"low",必须 high 才通过)
     - operator_corpus_consistent (算子与 corpus 实际结构一致)

  ③ 超额生成 1.3× + 后处理质检:每 cell 生成 max(N+1, 1.3*N) 题,质检后保留 N 道

  ④ cell-level 失败重试 1 次:LLM timeout 或质检 reject 全部 → 重试

V3.1 沿用:
  - qid deterministic counter(防撞库)
  - 三轴算子配额真消费(formation/evolution/query 严格)
  - 算子配额自动裁决

跑法:
  cd memory_bench_factory
  ./venv/bin/python -m pipeline.stage_d_v4_question_synthesis
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
    Chain, Question, OpsProfileV3,
    parse_cell_key, CAPABILITIES, FAILMODES,
)
# 复用 V3.1 的辅助函数和定义
from .stage_d_question_synthesis import (
    CAPABILITY_DEF, FAILMODE_DEF,
    FORMATION_DEF, EVOLUTION_DEF, QUERY_DEF,
    _format_remaining, _adjust_op_to_quota,
)


# ─────────────────────────────────────────────────────────────────────────────
# 1. V4 System Prompt(含 5 个 negative examples + self_check 字段要求)
# ─────────────────────────────────────────────────────────────────────────────

SYNTH_SYSTEM_V4 = """你是 memory benchmark 题目生成专家。给定 chain skeleton + 一个 cell((capability, failmode)),\
生成高质量题目。**这是 V4 版本,启用了严格质检 — 不合格题会被 reject 重生成,请严守下面规则。**

【必填字段】
- question / field_name / answer_by_period / answer_canonical_by_period
- failmode_signature (M3/M4/M5 必填,非这三个 failmode 时填 {{}})
- formation_op / evolution_op / query_op (三轴算子标签)
- rationale (一句话设计依据)
- **self_check** ★ V4 新增,见下面定义

【self_check 字段(必填)】每道题都必须自评:
{{
  "signature_complete": bool,            // M3/M4/M5 题 signature 是否完整(非这三个时填 true)
  "abs_canonical_filled": bool,          // ABS 题 canonical 是否每 period 填了 sentinel(非 ABS 时填 true)
  "mr_canonical_complete": bool,         // MR 题 canonical 是否含完整推理结果(非 MR 时填 true)
  "m3_reverence_strength": "high"|"medium"|"low",  // M3 反常识强度(非 M3 时填 "high" 占位)
  "operator_corpus_consistent": bool     // 算子标签与 corpus 实际结构是否一致
}}

【★★★ V3 失败案例 — V4 严禁重复 ★★★】

==== 案例 1: M3 反常识强度不足(SA3 实测,V3 命门 cell 失效)====
V3 用了 incident_severity "Minor"→"Critical" 作 M3 题。失败原因:
  • Minor 和 Critical 在 incident 领域**都是常见值**,LLM 没强先验
  • corpus 叙事"已从 Minor 系统性升级到 Critical"还自圆其说,反而坐实 injected
  • simpleMem × R3 实测 accuracy ≈ 95-100%,根本复现不了毕设 10.1% 的观察

V4 规则:
  • **M3 题的 common_default 必须是 LLM 训练分布频率 > 90% 的常识值**,
    例如:首都/CEO 姓名/数值反差 ≥ 5×(20% vs 100%, 5% vs 50% 等)
  • **injected_value 必须明显反常识**(领域内罕见或显然错误)
  • **如果 skeleton 的 conflict 字段是 "Minor/Critical" / "High/Low" / "True/False" / "5% vs 10%"
    这种弱反差,直接放弃这个字段**,改用 KU 题型(标 failmode=None),不要硬出 M3
  • self_check.m3_reverence_strength 必须填 "high",否则会被 reject

正确范例:
  field=annual_sla_target, common_default="99.95%", injected_value="60%" → 数值差 40pp,强反常识
  field=ceo_name, common_default="Tim Cook (Apple)", injected_value="Elvis Presley" → 跨领域反差

==== 案例 2: MR canonical 不完整 ====
V3 出了"哪个周期 X 最高,负责人是谁",canonical = {{"0":"张三","1":"张三"}} — 缺答案!
  • MR 问的是"哪个周期",canonical 应该指出**具体哪个周期**且给出完整推理结果

V4 规则:
  • MR 题 canonical 必须给出**完整推理结果**,例如:
    canonical = {{"2": "周期 2(oncall=15,最高;负责人李四)"}}
  • 或者给每个 period 完整记录:
    canonical = {{"0": "oncall=10,张三", "1": "oncall=12,张三", "2": "oncall=15,李四(最高)"}}
  • self_check.mr_canonical_complete 必须 true

==== 案例 3: ABS canonical 空 dict ====
V3 出了 ABS 题但 canonical = {{}} — 不可判分!

V4 规则:
  • ABS 题每个 period 必须填一个拒答 sentinel
  • 支持的 sentinel: "INSUFFICIENT_EVIDENCE" / "未提及" / "信息不足" / "I don't know"
  • 例如(3 periods):
    canonical = {{"0": "INSUFFICIENT_EVIDENCE", "1": "INSUFFICIENT_EVIDENCE", "2": "INSUFFICIENT_EVIDENCE"}}
  • self_check.abs_canonical_filled 必须 true

==== 案例 4: failmode_signature 为空 ====
V3 出了 failmode=M3 但 signature = {{}} — 没法 W2 验证!

V4 规则:M3 / M4 / M5 题必填完整 signature:
  M3: {{"type":"conflict_defaulting", "common_default":..., "injected_value":...}}
  M4: {{"type":"paraphrase_distractor", "verbatim_span":..., "distractor_options":[≥3 个改写]}}
  M5: {{"type":"format_mismatch", "equivalent_set":[≥7 个变体]}}
  self_check.signature_complete 必须 true

==== 案例 5: 算子标签与 corpus 结构不一致 ====
V3 在周报场景(纯文本,无图谱/层级摘要)标了 F4 — F4 算子根本不可用!

V4 规则:看 corpus 实际结构选算子:
  - **纯文本(周报、文档、邮件)**: F1(切片) / F2(原子事实) 主用,F3 罕见,F4 几乎不用
  - **多文档异构(周报+邮件+技术方案+评审纪要)**: F3(三元组) 可用,F4(分层) 需要明确的层级摘要才能用
  - **代码 repo 或图谱**: F3 / F4 主用
  - Q 算子类似:Q1(相似度)默认,Q2(图遍历)需要图结构,Q3(生成路由)适合 ABS / 复杂决策
  - self_check.operator_corpus_consistent 必须 true

【capability 题型设计指南】
- IE: 单 period 单字段回填
- MR: 跨 ≥2 period 综合(canonical 必须完整,见案例 2)
- TR: 时序推理(变化时机/排序)
- KU: 识别字段最新值
- ABS: false premise / corpus 未提及(canonical 必须 sentinel,见案例 3)

【三轴算子定义】
Formation:
{formation_def}

Evolution:
{evolution_def}

Query:
{query_def}

【★ 当前算子配额剩余】(优先选剩余多的,严禁选已用完=0 的)
formation_remaining: {formation_remaining}
evolution_remaining: {evolution_remaining}
query_remaining: {query_remaining}

【输出严格 JSON】(不要 markdown 包裹)
{{
  "questions": [
    {{
      "question": "...",
      "field_name": "...",
      "answer_by_period": {{...}},
      "answer_canonical_by_period": {{...}},
      "failmode_signature": {{...}},
      "formation_op": "F2",
      "evolution_op": "E4" or null,
      "query_op": "Q1",
      "rationale": "...",
      "self_check": {{
        "signature_complete": true,
        "abs_canonical_filled": true,
        "mr_canonical_complete": true,
        "m3_reverence_strength": "high",
        "operator_corpus_consistent": true
      }}
    }}
  ]
}}

**V4 提醒**:不要为了凑数生成低质量题。如果 corpus 不支持 M3(没有强反常识 conflict 字段),
**减少题数或改 failmode 为 None** — 后处理会重试,而不会接受垃圾题。"""


def _build_system_prompt_v4(
    remaining_formation: dict,
    remaining_evolution: dict,
    remaining_query: dict,
) -> str:
    formation_def_str = "\n".join(f"  {k}: {v}" for k, v in FORMATION_DEF.items())
    evolution_def_str = "\n".join(f"  {k}: {v}" for k, v in EVOLUTION_DEF.items())
    query_def_str = "\n".join(f"  {k}: {v}" for k, v in QUERY_DEF.items())
    return SYNTH_SYSTEM_V4.format(
        formation_def=formation_def_str,
        evolution_def=evolution_def_str,
        query_def=query_def_str,
        formation_remaining=_format_remaining(remaining_formation),
        evolution_remaining=_format_remaining(remaining_evolution),
        query_remaining=_format_remaining(remaining_query),
    )


def _build_user_prompt_v4(
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
        f"严格 JSON 输出 {n_questions} 道题(超额一点更好,后处理会筛)。"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 2. 后处理质检(V4 核心)
# ─────────────────────────────────────────────────────────────────────────────

ABSTAIN_TOKENS = (
    "insufficient", "未提及", "不知道", "abstain", "no info",
    "信息不足", "无法回答", "无信息", "i don't",
)


def _v4_quality_check(rq: dict, capability: str, failmode: Optional[str]) -> tuple:
    """V4 后处理质检。返回 (passed: bool, reject_reason: str)。"""
    sc = rq.get("self_check") or {}

    # 1. failmode_signature 完整性(M3/M4/M5)
    if failmode in ("M3", "M4", "M5"):
        sig = rq.get("failmode_signature") or {}
        if not sig:
            return False, f"{failmode} signature 为空"
        if failmode == "M3":
            if not (sig.get("common_default") and sig.get("injected_value")):
                return False, "M3 signature 缺 common_default/injected_value"
            # M3 反常识强度自检
            strength = sc.get("m3_reverence_strength", "low")
            if strength != "high":
                return False, f"M3 m3_reverence_strength={strength} (要求 high)"
        elif failmode == "M4":
            if not sig.get("verbatim_span"):
                return False, "M4 signature 缺 verbatim_span"
            distractors = sig.get("distractor_options") or []
            if len(distractors) < 3:
                return False, f"M4 distractor_options 不足 3 (实际 {len(distractors)})"
        elif failmode == "M5":
            equiv = sig.get("equivalent_set") or []
            if len(equiv) < 5:  # 略放松到 5(SA3 报告说 ≥7,但 5 是底线)
                return False, f"M5 equivalent_set 不足 5 (实际 {len(equiv)})"
        # self_check.signature_complete
        if sc.get("signature_complete") is False:
            return False, "self_check.signature_complete=false"

    # 2. ABS 题 canonical 必须每 period 填 sentinel
    if capability == "ABS":
        canon = rq.get("answer_canonical_by_period") or {}
        if not canon:
            return False, "ABS canonical 为空 dict"
        for p, v in canon.items():
            v_lower = str(v).lower()
            if not any(t in v_lower for t in ABSTAIN_TOKENS):
                return False, f"ABS canonical period={p} 值='{v}' 不是 sentinel"
        if sc.get("abs_canonical_filled") is False:
            return False, "self_check.abs_canonical_filled=false"

    # 3. MR 题 canonical 必须含完整推理
    if capability == "MR":
        canon = rq.get("answer_canonical_by_period") or {}
        if not canon:
            return False, "MR canonical 缺"
        if sc.get("mr_canonical_complete") is False:
            return False, "self_check.mr_canonical_complete=false"

    # 4. 算子与 corpus 一致性
    if sc.get("operator_corpus_consistent") is False:
        return False, "self_check.operator_corpus_consistent=false"

    return True, "ok"


# ─────────────────────────────────────────────────────────────────────────────
# 3. cell-level 生成(超额生成 + 质检 + 重试)
# ─────────────────────────────────────────────────────────────────────────────

def synthesize_questions_for_cell_v4(
    chain: Chain,
    skeleton: dict,
    capability: str,
    failmode: Optional[str],
    n_questions: int,
    qid_counter_start: int,
    remaining_formation: dict,
    remaining_evolution: dict,
    remaining_query: dict,
    max_retries: int = 1,
    overshoot_ratio: float = 1.3,
) -> tuple:
    """V4 增强:超额生成 + 质检 + 重试。

    Returns:
        (questions, qid_counter_after, reject_log)
    """
    if n_questions <= 0:
        return [], qid_counter_start, []

    accepted: list[Question] = []
    qid_counter = qid_counter_start
    reject_log: list[str] = []
    adjusted = 0

    for attempt in range(max_retries + 1):
        need = n_questions - len(accepted)
        if need <= 0:
            break
        # 超额生成 1.3×
        gen_n = max(need + 1, int(need * overshoot_ratio))

        msgs = [
            {"role": "system", "content": _build_system_prompt_v4(
                remaining_formation, remaining_evolution, remaining_query
            )},
            {"role": "user", "content": _build_user_prompt_v4(
                chain, skeleton, capability, failmode, gen_n
            )},
        ]
        try:
            data = config.chat_json(msgs, temperature=0.6, max_tokens=8192)
        except Exception as e:
            reject_log.append(f"attempt {attempt+1} LLM 异常: {e}")
            continue

        raw_qs = data.get("questions", [])
        for rq in raw_qs:
            if len(accepted) >= n_questions:
                break
            passed, reason = _v4_quality_check(rq, capability, failmode)
            if not passed:
                reject_log.append(reason)
                continue

            # 通过质检 → 三轴算子裁决 + qid 分配
            local_id = f"{capability}_{failmode or 'none'}_{qid_counter:04d}"
            qid_counter += 1

            chosen_f = str(rq.get("formation_op", "F2"))
            chosen_e = rq.get("evolution_op")
            chosen_q = str(rq.get("query_op", "Q1"))

            final_f, adj_f = _adjust_op_to_quota(chosen_f, remaining_formation)
            final_e, adj_e = _adjust_op_to_quota(chosen_e, remaining_evolution)
            final_q, adj_q = _adjust_op_to_quota(chosen_q, remaining_query)
            if adj_f or adj_e or adj_q:
                adjusted += 1

            accepted.append(Question(
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
        print(f"      [quota-adjust] {adjusted}/{len(accepted)} 题算子被裁决")
    if reject_log:
        print(f"      [v4-reject] {len(reject_log)} 题被 reject, 前 3 个原因: {reject_log[:3]}")

    return accepted[:n_questions], qid_counter, reject_log


# ─────────────────────────────────────────────────────────────────────────────
# 4. chain-level + chains-level
# ─────────────────────────────────────────────────────────────────────────────

def synthesize_questions_for_chain_v4(
    chain: Chain,
    profile: OpsProfileV3,
    verbose: bool = True,
    max_retries: int = 1,
    cell_fn=None,
) -> tuple:
    """V4 chain-level 入口。Returns (questions, global_reject_log)。

    cell_fn: cell 级生成器(依赖注入),默认 V4 自己;V6 注入信号竞争 dispatcher。
             签名须为 (chain, skeleton, cap, fm, n, qid_counter,
                      rem_f, rem_e, rem_q, max_retries=...) -> (qs, qid_counter, reject_log)
    """
    if cell_fn is None:
        cell_fn = synthesize_questions_for_cell_v4
    skeleton = None
    if chain.periods and isinstance(chain.periods[0].state, dict):
        skeleton = chain.periods[0].state.get("_skeleton")
    if not skeleton:
        print(f"[v4] chain {chain.chain_id} 缺 skeleton,跳过")
        return [], []

    remaining_f = dict(profile.formation_quota)
    remaining_e = dict(profile.evolution_quota)
    remaining_q = dict(profile.query_quota)
    qid_counter = 0

    all_qs: list[Question] = []
    all_rejects: list[str] = []
    sorted_cells = sorted(
        profile.cell_quota.items(), key=lambda kv: (-kv[1], kv[0])
    )
    if verbose:
        print(f"[v4] chain {chain.chain_id}: {len(sorted_cells)} 个 cell 待签发")
        print(f"[v4]   初始算子配额: F={dict(remaining_f)}")
        print(f"[v4]                 E={dict(remaining_e)}")
        print(f"[v4]                 Q={dict(remaining_q)}")
    for ck, n in sorted_cells:
        if n <= 0:
            continue
        cap, fm = parse_cell_key(ck)
        if verbose:
            print(f"[v4]   cell ({cap},{fm or 'none'}): 配额 {n} 题",
                  end=" ", flush=True)
        qs, qid_counter, reject_log = cell_fn(
            chain, skeleton, cap, fm, n, qid_counter,
            remaining_f, remaining_e, remaining_q,
            max_retries=max_retries,
        )
        if verbose:
            print(f"→ 生成 {len(qs)} 题, reject {len(reject_log)}")
        all_qs.extend(qs)
        all_rejects.extend(reject_log)

    if verbose:
        print(f"[v4]   剩余算子配额(应接近 0):")
        print(f"          F={dict(remaining_f)}")
        print(f"          E={dict(remaining_e)}")
        print(f"          Q={dict(remaining_q)}")
        if all_rejects:
            print(f"[v4]   全 chain reject 总数: {len(all_rejects)}")

    return all_qs, all_rejects


def synthesize_questions_for_chains_v4(
    chains: list[Chain],
    profile: OpsProfileV3,
    verbose: bool = True,
    max_retries: int = 1,
) -> list[Chain]:
    """V4 chains-level 入口。"""
    for chain in chains:
        qs, _rejects = synthesize_questions_for_chain_v4(
            chain, profile, verbose=verbose, max_retries=max_retries,
        )
        chain.qas = qs
    return chains


# ─────────────────────────────────────────────────────────────────────────────
# 5. 测试入口(V3.1 vs V4 对比)
# ─────────────────────────────────────────────────────────────────────────────

def main():
    """W1.5 V4 测试:Stage A→B→C→D V4 链路,验证质量改进。"""
    from .schema import RefinedScenarioSpec, Document, compute_stats, Benchmark
    from .stage_a_dimensions import infer_dimensions
    from .stage_b_ops_profile import build_ops_profile
    from .stage_c_corpus_synthesis import synthesize_corpus

    spec = RefinedScenarioSpec(
        name="office_engineering_v4",
        description_refined=(
            "我是业务线 leader,每周审阅 AI 工程团队迭代进展。"
            "关键字段:P0 缺陷率、Oncall 数量、负责人、SLA 达标率、项目里程碑。"
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
        target_size=10,
        cognitive_flavor_hint="mixed",
    )

    print("=== Stage A ===")
    dims = infer_dimensions(spec, use_llm=False)
    print(f"  I={dims.I_ingest_channels}, V={dims.V_perspective}")

    print("\n=== Stage B (target_size=10) ===")
    profile = build_ops_profile(dims, target_size=10, enforce_five_organs_full=False)
    print(f"  profile={profile.profile_name}, cell_quota sum={sum(profile.cell_quota.values())}")
    top = sorted(profile.cell_quota.items(), key=lambda x: -x[1])[:5]
    print(f"  top cells: {top}")

    print("\n=== Stage C (2 periods 节省成本) ===")
    chains = synthesize_corpus(
        spec.corpus_samples, dims, profile,
        n_chains=1, n_periods_per_chain=2, n_docs_per_period=1,
        verbose=True,
    )

    print(f"\n=== Stage D V4 (LLM-as-Curator + 后处理质检 + 重试) ===")
    chains_v4 = synthesize_questions_for_chains_v4(chains, profile, verbose=True, max_retries=1)

    total_qs = sum(len(c.qas) for c in chains_v4)
    print(f"\n=== ✓ V4 总题数: {total_qs} ===")

    # 算子分布 + 配额对比
    f_actual = Counter(q.formation_op for c in chains_v4 for q in c.qas)
    e_actual = Counter(q.evolution_op or "None" for c in chains_v4 for q in c.qas)
    q_actual = Counter(q.query_op for c in chains_v4 for q in c.qas)
    print(f"\n=== V4 算子配额实际 vs 期望 ===")
    print(f"  formation 期望 {profile.formation_quota}")
    print(f"  formation 实际 {dict(f_actual)}")
    print(f"  query     期望 {profile.query_quota}")
    print(f"  query     实际 {dict(q_actual)}")

    # 失败模式签名完整性(SA3 重点)
    print(f"\n=== failmode_signature 完整性(V3 vs V4 关键改进点)===")
    sig_filled = 0
    sig_missing = 0
    for c in chains_v4:
        for q in c.qas:
            if q.failmode in ("M3", "M4", "M5"):
                if q.failmode_signature:
                    sig_filled += 1
                else:
                    sig_missing += 1
                    print(f"  ⚠ {q.qid}: failmode={q.failmode} 但 signature 空")
    print(f"  填了 signature: {sig_filled} / {sig_filled + sig_missing}")

    # KU/M3 题反常识强度抽样
    print(f"\n=== KU/M3 题(SIGMOD 命门)抽样 ===")
    for c in chains_v4:
        for q in c.qas:
            if q.failmode == "M3":
                print(f"  [{q.qid}]")
                print(f"    field: {q.field_name}")
                print(f"    Q: {q.question[:120]}")
                print(f"    common_default: {q.failmode_signature.get('common_default')}")
                print(f"    injected_value: {q.failmode_signature.get('injected_value')}")


if __name__ == "__main__":
    main()
