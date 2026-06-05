"""
eval.baseline_r1 — R1 单轮 baseline(= SingleTurnAdaptor)。

检索 top-k 片段 → 一次 LLM 合成答案。最朴素的 RAG QA,正是【信号竞争】最容易
打中的对象:它不做多步反思/时序校正,top-k 里旧值多就答旧值。
"""
from __future__ import annotations
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config


R1_SYSTEM = """你是一个基于检索记忆的问答助手。

【规则】
1. 只依据下面提供的【检索片段】回答,不要使用片段外的知识,不要编造。
2. 若片段之间信息冲突,凭你的判断给出最可信的那一个答案。
3. 若片段里确实没有可回答的信息,回答"信息不足"。
4. 答案要【极简】:只给最终值(一个词、人名、数字或短语),不要解释、不要复述问题。"""


def r1_answer(question: str, snippets: list, max_tokens: int = 2048) -> str:
    """R1 单轮合成。snippets: list[str]。返回精简答案字符串。"""
    if not snippets:
        ctx = "(无检索结果)"
    else:
        ctx = "\n\n".join(f"[片段{i+1}] {s}" for i, s in enumerate(snippets))
    msgs = [
        {"role": "system", "content": R1_SYSTEM},
        {"role": "user", "content": (
            f"【检索片段】\n{ctx}\n\n"
            f"【问题】{question}\n\n"
            f"请只给最终答案(极简):"
        )},
    ]
    try:
        ans = config.chat(msgs, temperature=0.0, max_tokens=max_tokens)
    except Exception as e:
        return f"[R1_ERROR:{type(e).__name__}]"
    return (ans or "").strip()
