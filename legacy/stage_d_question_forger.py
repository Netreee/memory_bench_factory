"""
pipeline.stage_d_question_forger — Stage D V1: 给现有题签发标签 + 等价集升级。

W1 阶段简化策略:
  我们暂时【复用】OfficeMem 旧 qa 的 (question, field_name, answer_by_period) 主体,
  然后基于启发式规则给每道题自动签发我们 schema 要求的 6 类标签:
    1. formation_op   (F1/F2/F3/F4)            ← 由 field_name / question 关键词推断
    2. evolution_op   (E1/E2/E3/E4)            ← 由 answer 变化模式推断
    3. query_op       (Q1/Q2/Q3)               ← W1 默认 Q1
    4. target_failmode(M1/M2/M3/M4 或 None)    ← 由 task_type + 变化模式推断
    5. task_type      (AR/CR/TTL/LR)           ← 由 answer 变化频率推断
    6. judge_modes    (EM + fuzzy + llm_judge) ← 默认多口径
  并把 answer_by_period 单值升级为【等价表达集合】(避免 M5 格式错位)。

W2+ 计划:用 LLM 真正生成新题,替换这一层启发式签发。

跑法:
  cd memory_bench_factory
  ./venv/bin/python -m pipeline.stage_d_question_forger
"""
from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Optional

from .schema import Chain, Question, OpsProfile
from .stage_c_corpus import OFFICEMEM_BENCHMARK_PATH, load_chains_from_officemem


# ─────────────────────────────────────────────────────────────────────────────
# 子推断 1:task_type (AR / CR / TTL / LR)
# ─────────────────────────────────────────────────────────────────────────────

def _infer_task_type(answer_by_period: dict, period_count: int,
                     question: str, field_name: str) -> str:
    values = list(answer_by_period.values())
    unique = set(values)
    n_unique = len(unique)

    # LR 候选:涉及长摘要 / 跨多 period 聚合
    lr_kws = ["列表", "所有", "全部", "汇总", "统计", "总共", "整体趋势"]
    if any(kw in question for kw in lr_kws):
        return "LR"

    # AR:贯穿无变化
    if n_unique <= 1:
        return "AR"

    # TTL:频繁变化(超过一半 period 都是新值)
    if n_unique >= max(3, period_count // 2):
        return "TTL"

    # 默认 CR(适度变化)
    return "CR"


# ─────────────────────────────────────────────────────────────────────────────
# 子推断 2:evolution_op (E1 / E2 / E3 / E4)
# ─────────────────────────────────────────────────────────────────────────────

def _has_reversal(answer_by_period: dict) -> bool:
    """检测 answer 时序中是否出现过 '回退到曾经出现过的旧值'(非连续重复)。"""
    items = sorted(answer_by_period.items(), key=lambda kv: int(kv[0]))
    last_seen_idx: dict = {}
    for i, (_p, v) in enumerate(items):
        if v in last_seen_idx and i - last_seen_idx[v] > 1:
            return True
        last_seen_idx[v] = i
    return False


def _infer_evolution_op(answer_by_period: dict) -> str:
    unique = set(answer_by_period.values())
    if len(unique) <= 1:
        return "E1"  # 无变化 → 只增不减
    if _has_reversal(answer_by_period):
        return "E4"  # 有回退 → 显式修补
    return "E3"      # 单向演进 → 拓扑融合


# ─────────────────────────────────────────────────────────────────────────────
# 子推断 3:formation_op (F1 / F2 / F3 / F4)
# ─────────────────────────────────────────────────────────────────────────────

def _infer_formation_op(question: str, field_name: str) -> str:
    text = (question or "") + " " + (field_name or "")
    if any(kw in text for kw in ["@", "负责人", "owner", "Owner", "DRI", "PM"]):
        return "F3"  # 实体-关系三元组(有人物 entity)
    if any(kw in text for kw in ["列表", "所有", "全部", "汇总", "整体", "趋势", "总览"]):
        return "F4"  # 多层异构(聚合摘要)
    return "F2"      # 原子事实(默认)


# ─────────────────────────────────────────────────────────────────────────────
# 子推断 4:target_failmode (M1 / M2 / M3 / M4 / None)
# ─────────────────────────────────────────────────────────────────────────────

def _infer_target_failmode(task_type: str, evolution_op: str,
                           answer_by_period: dict) -> Optional[str]:
    """根据 task_type + 变化模式推断本题最可能触发哪个失败模式。

    映射依据 failmode_taxonomy.md 第 4.1 节:
      CR + 数值/百分比类  → M3 (冲突默认化:回退到常识默认值)
      TTL + 频繁变化      → M4 (干扰项改写:同义改写干扰)
      LR + 长摘要         → M2 (证据塌陷)
      AR + 单跳事实       → None (R3 可能 M1 但生成时不强标)
    """
    if task_type == "CR":
        return "M3"
    if task_type == "TTL":
        return "M4"
    if task_type == "LR":
        return "M2"
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 子推断 5:answer_by_period → 等价表达集合
# ─────────────────────────────────────────────────────────────────────────────

_PCT_RE = re.compile(r"^(\d+(?:\.\d+)?)%\s*(.*)$")
_NUM_RE = re.compile(r"^-?\d+(?:\.\d+)?$")


def _build_equiv_set(answer: str) -> list:
    """单值 → 等价表达集(避免 M5 格式错位)。"""
    a = (answer or "").strip()
    if not a:
        return [a]
    equiv: list = [a]

    # 百分数变体:"20%" ↔ "20.0%" ↔ "20 percent" ↔ "百分之二十"
    m = _PCT_RE.match(a)
    if m:
        num = m.group(1)
        suffix = m.group(2).strip()
        suffix_part = f" {suffix}" if suffix else ""
        if "." in num:
            base = num.rstrip("0").rstrip(".") if num.endswith(".0") else num
        else:
            base = num
        variants = {
            f"{base}%{suffix_part}",
            f"{base}.0%{suffix_part}",
            f"{base} percent{suffix_part}",
        }
        equiv.extend(v for v in variants if v not in equiv)

    # 纯数值变体
    if _NUM_RE.match(a):
        try:
            n = float(a)
            int_str = str(int(n)) if n == int(n) else None
            if int_str and int_str != a:
                equiv.append(int_str)
        except ValueError:
            pass

    # 去重保序
    seen = set()
    out = []
    for v in equiv:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _build_answer_equiv(answer_by_period: dict) -> dict:
    """对整个 answer_by_period 升级为等价集合。"""
    return {str(p): _build_equiv_set(v) for p, v in answer_by_period.items()}


# ─────────────────────────────────────────────────────────────────────────────
# 主入口:对一道旧 qa 签发完整标签
# ─────────────────────────────────────────────────────────────────────────────

def forge_question(raw_qa: dict, period_count: int) -> Question:
    """从 OfficeMem 旧 qa dict 签发出带完整标签的 Question 对象。"""
    qid = raw_qa["qid"]
    question = raw_qa.get("question", "")
    field_name = raw_qa.get("field_name", "")
    answer_by_period = raw_qa.get("answer_by_period", {})

    task_type = _infer_task_type(answer_by_period, period_count, question, field_name)
    evolution_op = _infer_evolution_op(answer_by_period)
    formation_op = _infer_formation_op(question, field_name)
    target_failmode = _infer_target_failmode(task_type, evolution_op, answer_by_period)
    answer_equiv = _build_answer_equiv(answer_by_period)

    return Question(
        qid=qid,
        question=question,
        field_name=field_name,
        answer_by_period=answer_equiv,
        formation_op=formation_op,
        evolution_op=evolution_op,
        query_op="Q1",  # W1 默认
        target_failmode=target_failmode,
        task_type=task_type,
        judge_modes=["EM", "fuzzy", "llm_judge"],
    )


def forge_chains_with_labels(
    chains: list[Chain],
    benchmark_path: Path = OFFICEMEM_BENCHMARK_PATH,
) -> list[Chain]:
    """主入口:给 Stage C 加载的 chains 签发带标签的 qa。

    数据源:OfficeMem benchmark 旧 qa 主体(question/field_name/answer_by_period),
    我们启发式签发 (formation_op/evolution_op/query_op/target_failmode/task_type/judge_modes)。
    """
    with open(benchmark_path, encoding="utf-8") as f:
        raw = json.load(f)
    raw_by_chain = {c["chain_id"]: c.get("qas", []) for c in raw}

    for chain in chains:
        raw_qas = raw_by_chain.get(chain.chain_id, [])
        chain.qas = [forge_question(rq, chain.period_count) for rq in raw_qas]
    return chains


# ─────────────────────────────────────────────────────────────────────────────
# 主入口 + 自检
# ─────────────────────────────────────────────────────────────────────────────

def main():
    """W1 Day-4 自检:加载 OfficeMem chains + 签发标签 + 打印分布。"""
    chains = load_chains_from_officemem()
    chains = forge_chains_with_labels(chains)

    print(f"[stage_d] 处理了 {len(chains)} 条 chain")
    print()

    # 全局统计
    from collections import Counter
    all_qa: list = []
    for c in chains:
        all_qa.extend(c.qas)
    print(f"[stage_d] 总 qa: {len(all_qa)}")
    print(f"  by task_type:       {dict(Counter(q.task_type for q in all_qa))}")
    print(f"  by formation_op:    {dict(Counter(q.formation_op for q in all_qa))}")
    print(f"  by evolution_op:    {dict(Counter(q.evolution_op for q in all_qa))}")
    print(f"  by query_op:        {dict(Counter(q.query_op for q in all_qa))}")
    print(f"  by target_failmode: {dict(Counter(q.target_failmode for q in all_qa))}")
    print()

    # 抽 3 道样题展示完整标签
    print("[stage_d] 前 3 道题完整签发结果:")
    for q in all_qa[:3]:
        print(f"  qid={q.qid}")
        print(f"    field_name: {q.field_name}")
        print(f"    question:   {q.question}")
        print(f"    answer_by_period (period 0/1/9):")
        for k in ["0", "1", "9"]:
            if k in q.answer_by_period:
                print(f"      period {k}: {q.answer_by_period[k]}")
        print(f"    labels: F={q.formation_op}, E={q.evolution_op}, "
              f"Q={q.query_op}, task={q.task_type}, "
              f"failmode={q.target_failmode}")
        print()


if __name__ == "__main__":
    main()
