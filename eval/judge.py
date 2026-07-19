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
    except Exception as e:
        print(f"[judge] llm_judge 调用失败(计为错): {type(e).__name__}: {str(e)[:80]}")
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


# ─────────────────────────────────────────────────────────────────────────────
# 七线判分地基:按【声明的 capability】分派 mode,不碰字段名、不猜中文子串。
#   value   — 命中等价值串(IE/KU/MR/PREEXPIRE/TR/L2_multihop/L4/L5/L7)
#   refusal — 正确 = 拒答/查无/已停统,而非编造一个具体值(L6/FORGET/ABS)
#   order   — 正确 = 复现 gold 的时间先后(L3_order)
# 单一真源 = q["capability"](题库声明的题类);gt 是代码烘焙的 ground-truth。
# ─────────────────────────────────────────────────────────────────────────────

_VALUE_CAPS = {"IE", "KU", "MR", "PREEXPIRE", "TR", "L2_multihop",
               "L4_preference", "L5_conflict", "L7_consolidation"}
_REFUSAL_CAPS = {"L6_refusal", "FORGET", "ABS"}
_ORDER_CAPS = {"L3_order"}

# 拒答类措辞(literal 快路;LLM 兜底语义)。本版【两类 None 都收"拒答"为正确】——
# 考点是【不编造值】;停统 vs 从未涉及 的细分(协议区分)留待后续,此处从宽(见报告 caveat)。
_REFUSAL_MARKERS = [
    "无此项", "查无", "无此记录", "没有此", "没有记录", "没有相关", "无相关", "无记录",
    "信息不足", "资料不足", "无法确定", "无法回答", "无法得知", "不知道", "未提及", "未提到",
    "没有提到", "没有提及", "未找到", "查不到", "找不到", "不存在", "无从", "未跟踪", "未统计",
    "已停止", "停止统计", "不再统计", "不再跟踪", "不再追踪", "已停更", "停更", "不再更新",
]


def _value_golds(cap: str, gt) -> list:
    """把各 capability 的 gt 抽成可判分的等价值串集(list[str])。"""
    if cap in ("KU", "PREEXPIRE", "L2_multihop",
               "L4_preference", "L5_conflict", "L7_consolidation"):
        return [str(gt)] if isinstance(gt, str) and gt.strip() else []
    if cap in ("IE", "MR"):
        v = (gt or {}).get("value")
        return [str(v)] if v not in (None, "") else []
    if cap == "TR":
        # 变更题:命中【目标值 / 变更日期 / 变更周(第N周)】任一,即正确识别了该变更事件。
        out = []
        for k in ("to", "date", "week"):
            v = (gt or {}).get(k)
            if v not in (None, ""):
                out.append(str(v))
        w = (gt or {}).get("week")
        if w not in (None, ""):
            out.append(f"第{w}周")
        return out
    return []


def _render_order(gt) -> str:
    """L3 有序事件 list → '字段=值 → 字段=值 → ...'(按 gt 给定的时间先后)。"""
    if not isinstance(gt, list):
        return ""
    parts = []
    for e in gt:
        if isinstance(e, dict):
            f, v = e.get("field", ""), e.get("value", "")
            parts.append(f"{f}={v}" if v != "" else str(f))
        else:
            parts.append(str(e))
    return " → ".join(parts)


def judge_spec(q: dict) -> tuple:
    """判分规约。返回 (mode, gold, expect)。
    mode∈{value,refusal,order,None};gold=判分目标;expect=拒答类的期望措辞(仅 refusal)。"""
    cap = q.get("capability")
    gt = q.get("gt")
    if cap in _REFUSAL_CAPS:
        expect = "已停止统计/不再跟踪" if cap == "FORGET" else "无此项/查无此记录"
        return ("refusal", None, expect)
    if cap in _ORDER_CAPS:
        return ("order", _render_order(gt), None)
    if cap in _VALUE_CAPS:
        return ("value", _value_golds(cap, gt), None)
    return (None, None, None)   # 未知题类 → 不可判分


def is_judgeable(q: dict) -> bool:
    mode, gold, _ = judge_spec(q)
    if mode == "value":
        return bool(gold)
    return mode in ("refusal", "order")


def gold_display(q: dict):
    """给报表/落盘用的可读 gold(value→等价集;refusal→期望措辞;order→顺序串)。"""
    mode, gold, expect = judge_spec(q)
    if mode == "refusal":
        return f"(拒答:{expect})"
    if mode == "order":
        return gold
    return gold


def _looks_refusal(pred: str) -> bool:
    p = _norm(pred)
    if not p:
        return False
    return any(_norm(m) in p for m in _REFUSAL_MARKERS)


_REFUSAL_JUDGE_SYS = """你在判一道【拒答题】。正确行为 = 模型【拒绝作答/表示查无/表示已停止统计】,\
而不是编造一个具体值。
给定【问题】【期望的拒答含义】【模型答案】:
- 若模型答案表达了"查无/无此项/信息不足/已停止统计/不再跟踪"等拒答语义 → correct=true;
- 若模型给出了一个具体的值/人名/数字(即没有拒答)→ correct=false。
【输出严格 JSON】{"correct": true 或 false, "reason": "一句话"}"""


def _judge_refusal(question: str, expect: str, pred: str, use_llm: bool = True) -> bool:
    if _looks_refusal(pred):
        return True
    if not use_llm:
        return False
    msgs = [
        {"role": "system", "content": _REFUSAL_JUDGE_SYS},
        {"role": "user", "content": (
            f"【问题】{question}\n【期望的拒答含义】{expect}\n【模型答案】{pred}\n\n严格JSON。")},
    ]
    try:
        return bool(config.chat_json(msgs, temperature=0.0, max_tokens=512).get("correct", False))
    except Exception as e:
        print(f"[judge] _judge_refusal 调用失败(计为错): {type(e).__name__}: {str(e)[:80]}")
        return False


_ORDER_JUDGE_SYS = """你在判一道【时间排序题】。给定【问题】【正确顺序(从早到晚)】【模型答案】,\
判断模型答案给出的事件先后顺序是否与正确顺序一致(只看相对先后,措辞/格式不限)。
- 顺序完全一致 → correct=true;
- 有任意一对事件先后颠倒,或漏给/答非所问 → correct=false。
【输出严格 JSON】{"correct": true 或 false, "reason": "一句话"}"""


def _judge_order(question: str, gold_seq: str, pred: str, use_llm: bool = True) -> bool:
    if not use_llm:
        return _norm(gold_seq) in _norm(pred)   # 退化:仅当原样复现
    msgs = [
        {"role": "system", "content": _ORDER_JUDGE_SYS},
        {"role": "user", "content": (
            f"【问题】{question}\n【正确顺序(从早到晚)】{gold_seq}\n【模型答案】{pred}\n\n严格JSON。")},
    ]
    try:
        return bool(config.chat_json(msgs, temperature=0.0, max_tokens=512).get("correct", False))
    except Exception as e:
        print(f"[judge] _judge_order 调用失败(计为错): {type(e).__name__}: {str(e)[:80]}")
        return False


def judge_answer(q: dict, pred: str, use_llm: bool = True) -> bool:
    """七线统一判分入口。按 q['capability'] 声明分派到 value/refusal/order。"""
    mode, gold, expect = judge_spec(q)
    question = q.get("question", "")
    if mode == "value":
        return judge(question, gold, pred, use_llm=use_llm)
    if mode == "refusal":
        return _judge_refusal(question, expect, pred, use_llm=use_llm)
    if mode == "order":
        return _judge_order(question, gold, pred, use_llm=use_llm)
    return False
