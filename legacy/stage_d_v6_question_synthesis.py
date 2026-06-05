"""
pipeline.stage_d_v6_question_synthesis — Stage D V6: 信号竞争 M3 + failmode 预测。

V6 核心转变(redesign_v6.md §2 转变 2):
  M3 不再靠"反常识注入"(Elvis/Pyongyang 已被 dry-run 证伪),改由 corpus 内
  【自然信号竞争】驱动:某字段旧值跨多 period 高频出现、新值仅末 period 低频出现,
  问"最新值"时被测系统记忆里旧值信号更强 → 倾向答错成旧值(conflict defaulting)。

  ★ failmode 语义改为【预测标签】:出题时不强造 ground-truth 失败,而是基于 corpus
    信号特征预测"这题大概率触发 M3",待 W2 跑 baseline 实测验证(标准 A)。

架构(螺旋:复用 V4 质量层,只换 M3 这个器官):
  - M3 cell → synthesize_m3_signal_competition_questions(本文件新增):
      answer / failmode_evidence 从 skeleton 的 signal_competition 字段【确定性】填,
      只用 LLM 自然措辞 question 文本(不靠 LLM 编造答案)。
  - 非 M3 cell(IE/MR/TR/KU-none/ABS)→ 复用 V4 synthesize_questions_for_cell_v4。
  - chain/chains 调度复用 V4(通过 cell_fn 依赖注入,见 stage_d_v4 的 cell_fn 参数)。

跑法:
  cd memory_bench_factory
  ./venv/bin/python -m pipeline.stage_d_v6_question_synthesis
"""
from __future__ import annotations
from pathlib import Path
from typing import Optional
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config

from .schema import Chain, Question, OpsProfileV3
from .stage_d_question_synthesis import _adjust_op_to_quota
from .stage_d_v4_question_synthesis import (
    synthesize_questions_for_cell_v4,
    synthesize_questions_for_chain_v4,
)


# ─────────────────────────────────────────────────────────────────────────────
# 1. M3 自然措辞(LLM 只负责把"问最新值"写得像真实用户问句)
# ─────────────────────────────────────────────────────────────────────────────

M3_PHRASE_SYSTEM = """你是 benchmark 出题助手。给定项目主题 + 若干字段,\
为每个字段写一个自然的中文问句,询问该字段【截至最新记录的当前值】。

【要求】
1. 用"最新 / 目前 / 现任 / 当前 / 现在"等词凸显"要最新值,不是历史值"
2. 问句要像真实用户问的,自然、简洁、口语化
3. 一个字段一个问句,顺序与输入字段严格一一对应

【输出严格 JSON】(不要 markdown 包裹)
{"questions": ["问句1", "问句2", ...]}"""


def _llm_phrase_latest_value_questions(skeleton: dict, plan: list) -> list:
    """给 plan 里每个 signal_competition 字段生成一个自然"最新值"问句。

    失败则回退到模板,保证 len(返回) == len(plan)。
    """
    theme = skeleton.get("main_theme", "")
    fields_desc = [
        {"field_name": f.get("name"), "domain": f.get("domain", "")}
        for f in plan
    ]
    user = (
        f"项目主题:{theme}\n"
        f"字段(按顺序,共 {len(plan)} 个):\n"
        f"{json.dumps(fields_desc, ensure_ascii=False, indent=2)}\n\n"
        f"为每个字段写 1 个'询问最新值'的问句,共 {len(plan)} 个,"
        f"严格 JSON、顺序一一对应。"
    )
    try:
        data = config.chat_json(
            [{"role": "system", "content": M3_PHRASE_SYSTEM},
             {"role": "user", "content": user}],
            temperature=0.5, max_tokens=2048,
        )
        qs = data.get("questions", [])
        if isinstance(qs, list) and len(qs) >= len(plan):
            return [str(q) for q in qs[:len(plan)]]
        print(f"[m3-phrase-v6] LLM 返回数量不足({len(qs)}<{len(plan)}),模板兜底")
    except Exception as e:
        print(f"[m3-phrase-v6] LLM 异常,模板兜底: {e}")
    return [
        f"在「{theme}」中,目前最新的{f.get('name')}是什么?"
        for f in plan
    ]


# ─────────────────────────────────────────────────────────────────────────────
# 2. V6 M3 题生成(信号竞争,确定性 answer + evidence)
# ─────────────────────────────────────────────────────────────────────────────

def synthesize_m3_signal_competition_questions(
    chain: Chain,
    skeleton: dict,
    capability: str,
    n_questions: int,
    qid_counter_start: int,
    remaining_formation: dict,
    remaining_evolution: dict,
    remaining_query: dict,
    max_retries: int = 1,   # 签名对齐 V4 cell_fn(M3 路径不需重试)
) -> tuple:
    """V6 M3:基于 skeleton 的 evolving_signal_competition 字段直接构造"最新值"题。

    answer_by_period / answer_canonical_by_period / failmode_evidence 全部从 skeleton
    的 ground truth 确定性填充(不靠 LLM 编),只用 LLM 给 question 文本自然措辞。

    Returns:
        (questions, qid_counter_after, reject_log)
    """
    if n_questions <= 0:
        return [], qid_counter_start, []

    sc_fields = [
        f for f in skeleton.get("fields", [])
        if f.get("type") == "evolving_signal_competition"
        and f.get("signal_competition")
    ]
    if not sc_fields:
        return [], qid_counter_start, [
            f"({capability},M3) 无 signal_competition 字段,无法出 M3 题"
        ]

    # 循环 sc_fields 直到凑够 n_questions(字段少于配额时复用同字段,措辞不同)
    plan = [sc_fields[i % len(sc_fields)] for i in range(n_questions)]
    phrasings = _llm_phrase_latest_value_questions(skeleton, plan)

    questions: list[Question] = []
    qid_counter = qid_counter_start
    reject_log: list[str] = []
    adjusted = 0

    for i, f in enumerate(plan):
        sc = f.get("signal_competition", {})
        vbp = f.get("values_by_period", {}) or {}
        if not vbp:
            reject_log.append(f"字段 {f.get('name')} 缺 values_by_period")
            continue

        # 确定性 answer:每个 period 的真实最新值(W2 在末 period 问 → 答案=新值)
        answer_by_period = {str(k): [str(v)] for k, v in vbp.items()}
        answer_canonical = {str(k): str(v) for k, v in vbp.items()}

        # V6 核心:信号竞争证据(为何预测 M3)
        evidence = {
            "mechanism": "conflict_defaulting_by_signal_strength",
            "old_value": sc.get("old_value"),
            "old_freq": sc.get("old_freq"),
            "new_value": sc.get("new_value"),
            "new_freq": sc.get("new_freq"),
            "signal_ratio": sc.get("signal_ratio"),
        }
        # failmode_signature:V6 形态(兼容老 validator 的 common_default/injected_value key)
        signature = {
            "type": "conflict_defaulting_signal",
            "common_default": sc.get("old_value"),   # 系统倾向默认的高频旧值
            "injected_value": sc.get("new_value"),   # ★兼容 key;V6 语义=正确的最新值(非"注入")
            "correct_latest": sc.get("new_value"),
            "signal_ratio": sc.get("signal_ratio"),
        }

        # 算子:KU 最新值题 → F2(原子事实)+ E4(显式修补,KU 主用)+ Q1(相似度)
        final_f, adj_f = _adjust_op_to_quota("F2", remaining_formation)
        final_e, adj_e = _adjust_op_to_quota("E4", remaining_evolution)
        final_q, adj_q = _adjust_op_to_quota("Q1", remaining_query)
        if adj_f or adj_e or adj_q:
            adjusted += 1

        local_id = f"{capability}_M3_{qid_counter:04d}"
        qid_counter += 1
        questions.append(Question(
            qid=f"{chain.chain_id}__{local_id}",
            source_chain_id=chain.chain_id,
            question=phrasings[i],
            answer_by_period=answer_by_period,
            answer_canonical_by_period=answer_canonical,
            field_name=f.get("name"),
            capability=capability,
            failmode="M3",
            failmode_signature=signature,
            failmode_evidence=evidence,
            formation_op=final_f or "F2",
            evolution_op=final_e or "E4",
            query_op=final_q or "Q1",
        ))

    if adjusted:
        print(f"      [quota-adjust] {adjusted}/{len(questions)} M3 题算子被裁决")
    return questions, qid_counter, reject_log


# ─────────────────────────────────────────────────────────────────────────────
# 3. cell 调度器(M3 → V6 信号竞争;其它 → V4 质量层)
# ─────────────────────────────────────────────────────────────────────────────

def synthesize_questions_for_cell_v6(
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
) -> tuple:
    """V6 cell 入口:M3 走信号竞争路径,其余复用 V4。签名对齐 V4 cell_fn。"""
    if n_questions <= 0:
        return [], qid_counter_start, []
    if failmode == "M3":
        return synthesize_m3_signal_competition_questions(
            chain, skeleton, capability, n_questions, qid_counter_start,
            remaining_formation, remaining_evolution, remaining_query,
        )
    # 非 M3:复用 V4 质量层(超额生成 + 后处理质检 + 重试)
    return synthesize_questions_for_cell_v4(
        chain, skeleton, capability, failmode, n_questions, qid_counter_start,
        remaining_formation, remaining_evolution, remaining_query,
        max_retries=max_retries,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 4. chain / chains 调度(复用 V4 的配额线程化,只注入 V6 cell_fn)
# ─────────────────────────────────────────────────────────────────────────────

def synthesize_questions_for_chain_v6(
    chain: Chain,
    profile: OpsProfileV3,
    verbose: bool = True,
    max_retries: int = 1,
) -> tuple:
    """V6 chain-level:委托 V4 chain 调度,但 cell 生成用 V6 dispatcher。"""
    return synthesize_questions_for_chain_v4(
        chain, profile, verbose=verbose, max_retries=max_retries,
        cell_fn=synthesize_questions_for_cell_v6,
    )


def synthesize_questions_for_chains_v6(
    chains: list,
    profile: OpsProfileV3,
    verbose: bool = True,
    max_retries: int = 1,
) -> list:
    """V6 chains-level 入口。"""
    for chain in chains:
        qs, _rejects = synthesize_questions_for_chain_v6(
            chain, profile, verbose=verbose, max_retries=max_retries,
        )
        chain.qas = qs
    return chains


# ─────────────────────────────────────────────────────────────────────────────
# 5. 测试入口(Stage A→B→C(v6)→D(v6),验证 M3 信号竞争题质量)
# ─────────────────────────────────────────────────────────────────────────────

def main():
    from .schema import RefinedScenarioSpec, Document
    from .stage_a_dimensions import infer_dimensions
    from .stage_b_ops_profile import build_ops_profile
    from .cell_requirements_table import build_cell_requirements
    from .stage_c_corpus_synthesis_v6 import synthesize_corpus_v6

    spec = RefinedScenarioSpec(
        name="office_v6_stage_d_test",
        description_refined=(
            "我是业务线 leader,每周审阅 AI 工程团队迭代进展。"
            "关键字段:P0 缺陷率、Oncall 数量、负责人、客户对接人、版本。"
        ),
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
    print(f"  cell_quota: {dict(profile.cell_quota)}")
    print(f"  M3 cells: {[(r.cap, r.fm) for r in cell_reqs if r.fm=='M3']}")

    print("\n=== Stage C V6(信号竞争 corpus)===")
    chains = synthesize_corpus_v6(
        spec.corpus_samples, dims, cell_reqs,
        n_chains=1, n_periods_per_chain=5, n_docs_per_period=1,
    )

    print("\n=== Stage D V6(信号竞争 M3 + failmode 预测)===")
    chains = synthesize_questions_for_chains_v6(chains, profile, verbose=True)

    total = sum(len(c.qas) for c in chains)
    print(f"\n=== ✓ V6 Stage D: 共 {total} 题 ===")

    # 重点检查 M3 题
    print("\n=== ★ M3 信号竞争题抽样(V6 核心)===")
    m3_qs = [q for c in chains for q in c.qas if q.failmode == "M3"]
    print(f"  M3 题数: {len(m3_qs)}")
    for q in m3_qs:
        ev = q.failmode_evidence or {}
        last_p = max(q.answer_canonical_by_period.keys(), key=lambda k: int(k)) \
            if q.answer_canonical_by_period else None
        print(f"\n  [{q.qid}]  ({q.formation_op}/{q.evolution_op}/{q.query_op})")
        print(f"    Q: {q.question}")
        print(f"    field: {q.field_name}")
        print(f"    末 period({last_p}) 正确答案: "
              f"{q.answer_canonical_by_period.get(last_p) if last_p else '?'}")
        print(f"    ★ 信号竞争证据: 旧值'{ev.get('old_value')}'({ev.get('old_freq')}x) "
              f"vs 新值'{ev.get('new_value')}'({ev.get('new_freq')}x), "
              f"signal_ratio={ev.get('signal_ratio')}")
        print(f"    预测:系统倾向答旧值'{ev.get('old_value')}'(信号更强)→ M3 触发")

    # 完整性自检
    print("\n=== 完整性自检 ===")
    bad = [q.qid for q in m3_qs if not q.failmode_evidence.get("signal_ratio")]
    print(f"  M3 题缺 signal_ratio: {len(bad)} {bad[:3]}")
    cap_dist = {}
    for c in chains:
        for q in c.qas:
            cap_dist[q.capability] = cap_dist.get(q.capability, 0) + 1
    print(f"  capability 分布: {cap_dist}")


if __name__ == "__main__":
    main()
