"""
eval.judge — 判分(EM/子串 + LLM 兜底)+ M3 信号竞争触发分类。

判分对齐 OfficeMem:先字面(EM/子串/去格式),不中再 LLM 兜底。
M3 额外分类:答案命中【旧值】(信号竞争触发 = 答错)还是【新值】(正确)。
"""
from __future__ import annotations
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config


def _norm(s) -> str:
    """归一化:去空白、去标点、小写、全角→半角百分号等。"""
    s = str(s or "").strip().lower()
    s = s.replace("％", "%").replace(" ", "").replace("　", "")
    s = re.sub(r"[，,。.、;:；：!！?？\"'「」『』()()【】\[\]]", "", s)
    return s


def literal_match(pred: str, gold_set: list) -> bool:
    """字面判分:pred 与等价集任一值 EM 或互为子串。"""
    p = _norm(pred)
    if not p:
        return False
    for g in gold_set:
        g = _norm(g)
        if not g:
            continue
        if g == p or g in p or p in g:
            return True
    return False


JUDGE_SYSTEM = """你是判分员。给定【问题】【标准答案集(任一即正确)】【模型答案】,\
判断模型答案是否在语义上正确命中标准答案之一。

【输出严格 JSON】{"correct": true 或 false, "reason": "一句话"}"""


def llm_judge(question: str, gold_set: list, pred: str) -> bool:
    msgs = [
        {"role": "system", "content": JUDGE_SYSTEM},
        {"role": "user", "content": (
            f"【问题】{question}\n"
            f"【标准答案集】{gold_set}\n"
            f"【模型答案】{pred}\n\n"
            f"判断模型答案是否正确,严格 JSON。"
        )},
    ]
    try:
        data = config.chat_json(msgs, temperature=0.0, max_tokens=1024)
        return bool(data.get("correct", False))
    except Exception:
        return False


def judge(question: str, gold_set: list, pred: str, use_llm: bool = True) -> bool:
    """先字面后 LLM。gold_set: list[str] 等价集。"""
    if literal_match(pred, gold_set):
        return True
    if use_llm:
        return llm_judge(question, gold_set, pred)
    return False


def classify_m3(pred: str, evidence: dict) -> str:
    """M3 信号竞争结果分类。

    返回:
      'correct_new'      — 命中新值(正确,M3 未触发)
      'm3_triggered_old' — 命中旧值(★ 信号竞争触发 = 答成高频旧值)
      'both'             — 新旧都提到(模糊)
      'other'            — 都没命中
    """
    old_v = _norm(evidence.get("old_value"))
    new_v = _norm(evidence.get("new_value"))
    p = _norm(pred)
    has_old = bool(old_v) and old_v in p
    has_new = bool(new_v) and new_v in p
    if has_new and not has_old:
        return "correct_new"
    if has_old and not has_new:
        return "m3_triggered_old"
    if has_old and has_new:
        return "both"
    return "other"
