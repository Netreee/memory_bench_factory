"""
dry_run_ku_m3.py — V5 (KU, M3) 命门假设的最纯净 dry-run。

不依赖 simpleMem(无 config.yaml)。
直接用 LLM 模拟 R1/R3:
  - R1: 给完整 corpus + question → 一次 generate(忠实检索 baseline)
  - R3: 给完整 corpus + question + "反思常识冲突"指令 → plan + reflect → 答

V5 corpus 只 2 个 period × 1 doc = 2 篇短文档,根本不需要 RAG 检索。
纯 LLM 模拟更纯净 — 剥离了 retrieval 噪音,只测 "corpus vs 常识冲突时 LLM 的选择倾向"。

预期(SA4 赌 85%):
  - R1 答 Pyongyang/Elvis(忠实 corpus)
  - R3 答 Pyongyang/Elvis(corpus 太自洽,反思也不回退)→ 命门假设破产
  - 15% 概率:R3 真回退 Cupertino/Tim Cook → 命门假设成立

跑法:
  cd memory_bench_factory
  ./venv/bin/python tools/dry_run_ku_m3.py
"""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import config


# ─────────────────────────────────────────────────────────────────────────────
# R1 / R3 prompt 模拟(参 memory-systems-eval/prompts.yaml 设计哲学)
# ─────────────────────────────────────────────────────────────────────────────

R1_SYSTEM = """你是基于检索的 QA 系统(SingleTurn / R1)。给定 corpus 与问题,\
严格基于 corpus 内的事实回答,不要使用你的训练分布先验。
- 如果 corpus 明确给出答案,直接输出该答案
- 输出格式:`Answer: <答案>` (一行,简短)"""


R3_SYSTEM = """你是 Plan-and-Act 反思推理 QA 系统(R3)。给定 corpus 与问题,\
经过【规划→检索→反思→重规划】多步流程,挑选最自洽的答案。

【流程】
1. 理解问题
2. 在 corpus 中找答案候选
3. 反思:候选是否与你的训练分布常识一致?如果 corpus 与常识强冲突,选你认为'最自洽'的答案
4. 输出最终答案

【输出格式】
THOUGHT: <你的推理过程,含反思步骤>
Answer: <最终答案>"""


def _build_user_prompt(corpus_str: str, question: str) -> str:
    return (
        f"【corpus】\n{corpus_str}\n\n"
        f"【问题】\n{question}\n\n"
        f"请回答。"
    )


def _extract_answer(raw: str) -> str:
    """从 LLM 输出抽 Answer 行(简化版)。"""
    raw = (raw or "").strip()
    # 找最后一个 Answer: 行
    lines = raw.split("\n")
    for line in reversed(lines):
        line = line.strip()
        if line.lower().startswith("answer:"):
            return line.split(":", 1)[1].strip()
    # fallback: 整段
    return raw[-200:].strip()


# ─────────────────────────────────────────────────────────────────────────────
# 主程
# ─────────────────────────────────────────────────────────────────────────────

def main():
    # 1. 加载 V5 oflat 产物
    oflat_path = ROOT / "output" / "v5_smoke_office_oflat.json"
    extended_path = ROOT / "output" / "v5_smoke_office_extended.json"
    with open(oflat_path, encoding="utf-8") as f:
        chains = json.load(f)
    with open(extended_path, encoding="utf-8") as f:
        bm_ext = json.load(f)
    chain = chains[0]
    chain_ext = bm_ext["chains"][0]
    print(f"chain: {chain['chain_name']} ({chain['period_count']} periods)")

    # 2. 拼接完整 corpus(所有 period 串起来)
    corpus_parts = []
    for period in chain["periods"]:
        for doc in period["ingest_docs"]:
            corpus_parts.append(
                f"[{period['date']}] {doc.get('title','')}\n{doc.get('content','')}"
            )
    corpus_str = "\n\n---\n\n".join(corpus_parts)
    print(f"corpus total chars: {len(corpus_str)}")

    # 3. 找 KU+M3 题(用 extended 版本拿 failmode_signature)
    ku_m3_qas = []
    for q_ext in chain_ext["qas"]:
        if q_ext.get("failmode") == "M3" and q_ext.get("capability") == "KU":
            ku_m3_qas.append(q_ext)
    print(f"找到 {len(ku_m3_qas)} 道 KU+M3 题\n")

    # 4. 逐题跑 R1/R3
    results = []
    for qa in ku_m3_qas:
        print("=" * 60)
        print(f"qid: {qa['qid']}")
        print(f"Q: {qa['question']}")
        sig = qa.get("failmode_signature", {})
        common_default = sig.get("common_default", "?")
        injected_value = sig.get("injected_value", "?")
        print(f"signature: common_default={common_default!r}, injected_value={injected_value!r}")
        print(f"canonical_by_period: {qa.get('answer_canonical_by_period', {})}")
        print()

        user_prompt = _build_user_prompt(corpus_str, qa["question"])

        # R1
        print("[R1] 跑 SingleTurn ...", end=" ", flush=True)
        r1_msgs = [
            {"role": "system", "content": R1_SYSTEM},
            {"role": "user", "content": user_prompt},
        ]
        try:
            r1_raw = config.chat(r1_msgs, temperature=0.0, max_tokens=2048)
            r1_ans = _extract_answer(r1_raw)
            print("✓")
        except Exception as e:
            r1_raw = f"[LLM 失败: {e}]"
            r1_ans = "[error]"
            print(f"✗ ({e})")
        print(f"  R1 answer extracted: {r1_ans[:150]!r}")

        # R3
        print("[R3] 跑 PlanAndAct ...", end=" ", flush=True)
        r3_msgs = [
            {"role": "system", "content": R3_SYSTEM},
            {"role": "user", "content": user_prompt},
        ]
        try:
            r3_raw = config.chat(r3_msgs, temperature=0.0, max_tokens=2048)
            r3_ans = _extract_answer(r3_raw)
            print("✓")
        except Exception as e:
            r3_raw = f"[LLM 失败: {e}]"
            r3_ans = "[error]"
            print(f"✗ ({e})")
        print(f"  R3 answer extracted: {r3_ans[:150]!r}")
        if r3_ans != _extract_answer(r3_raw)[:150]:
            pass
        # 打印 R3 完整推理(看反思过程)
        print(f"\n  --- R3 full raw output ---")
        print(f"  {r3_raw[:1200]}")
        print(f"  --- end R3 ---")

        # 5. 判定 M3 是否触发
        # M3 触发 = R1 答 injected_value 且 R3 答 common_default(回退默认)
        r1_says_injected = injected_value.lower() in r1_ans.lower()
        r1_says_default = common_default.lower() in r1_ans.lower()
        r3_says_injected = injected_value.lower() in r3_ans.lower()
        r3_says_default = common_default.lower() in r3_ans.lower()

        m3_triggered = r1_says_injected and r3_says_default and not r3_says_injected

        print(f"\n  ★ 命门判定:")
        print(f"    R1 包含 injected({injected_value!r}): {r1_says_injected}")
        print(f"    R1 包含 default ({common_default!r}): {r1_says_default}")
        print(f"    R3 包含 injected({injected_value!r}): {r3_says_injected}")
        print(f"    R3 包含 default ({common_default!r}): {r3_says_default}")
        print(f"    ★ M3 默认化触发? {'✓ 是(命门假设成立)' if m3_triggered else '✗ 否(命门假设失败)'}")

        results.append({
            "qid": qa["qid"],
            "common_default": common_default,
            "injected_value": injected_value,
            "r1_says_injected": r1_says_injected,
            "r1_says_default": r1_says_default,
            "r3_says_injected": r3_says_injected,
            "r3_says_default": r3_says_default,
            "m3_triggered": m3_triggered,
            "r1_ans": r1_ans,
            "r3_ans": r3_ans,
        })
        print()

    # 6. 汇总
    print("=" * 60)
    print("=== 汇总 ===")
    n_triggered = sum(1 for r in results if r["m3_triggered"])
    n_total = len(results)
    trigger_rate = n_triggered / n_total if n_total else 0.0
    print(f"M3 触发率: {n_triggered}/{n_total} = {trigger_rate*100:.0f}%")
    print(f"  (期望 ≥ 60% → 命门假设成立 → V5 进 W2)")
    print(f"  (SA4 赌 85% 不触发 → 命门假设破产 → V5 corpus 要重设计)")
    print()
    for r in results:
        flag = "✓" if r["m3_triggered"] else "✗"
        print(f"  {flag} {r['qid']}: R1='{r['r1_ans'][:60]}' R3='{r['r3_ans'][:60]}'")


if __name__ == "__main__":
    main()
