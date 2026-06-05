"""
pipeline.stage_a_seed_expansion — Stage A': Seed Bias 检测 + 自动多样化。

用户输入的 5 篇 corpus_samples 可能严重偏向 spec 的某个子集(例:全是周报,缺会议纪要)。
本阶段:
  1. check_seed_coverage:用 LLM 给每篇 sample 在 5 维空间打位置,识别 missing_cells
  2. expand_seeds_via_llm:针对 missing_cells 用 LLM 合成新 seed(类 Self-Instruct 自举)

合成的新 seed 必须:
  ① 与 description_refined 一致
  ② 风格与原 samples 相近(不能跳脱)
  ③ 落在缺失维度组合
  ④ 含合理的 metadata(timestamp / author / doc_type)

详见 docs/anchors/redesign_v3.md 第 4.3 节。

跑法:
  cd memory_bench_factory
  ./venv/bin/python -m pipeline.stage_a_seed_expansion
"""
from __future__ import annotations
from collections import Counter
from pathlib import Path
from typing import Optional
import json
import math
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config

from .schema import (
    RefinedScenarioSpec, ScenarioDimensions, Document, SeedCoverageReport,
    INGEST_CHANNELS, MEMORY_SUBJECTS, PERSPECTIVES,
)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Coverage 检测
# ─────────────────────────────────────────────────────────────────────────────

COVERAGE_SYSTEM = """你是 corpus 分布分析专家。给定一个 RefinedScenarioSpec 和它的 corpus_samples,\
你要给【每篇 sample】在 5 维场景空间的位置打标签,并识别 spec 应该覆盖但 samples 没覆盖的"位置"。

【维度取值范围】
- I (ingest channel,单选):dialogue / meeting_minutes / weekly_report / long_document / \
email_im / code_diff_pr / ticket_crm / table_dashboard / voice_transcript
- S (memory subject,单选):individual / customer / project / business_line / team / \
contract_matter / code_repo / product_sku / case / compliance_item
- V (perspective,单选):first_person / line_manager / cross_line_leader / partner_customer / \
audit_compliance / successor / external_analyst

【任务】
1. 给每篇 sample 打 (I, S, V) 三维位置标签
2. 根据 description_refined 推断 spec 应该覆盖哪些 (I, S, V) 组合
3. 找出"应该覆盖但 samples 没覆盖"的组合 = missing_cells

【输出严格 JSON】(不要 markdown 包裹)
{
  "per_doc_position": [
    {"doc_id": "...", "I": "...", "S": "...", "V": "..."},
    ...
  ],
  "implied_cells_from_spec": [
    {"I": "...", "S": "...", "V": "..."},
    ...
  ],
  "missing_cells": [
    {"I": "...", "S": "...", "V": "...", "rationale": "为什么这个组合应该被覆盖"},
    ...
  ]
}"""


def _build_coverage_user_prompt(spec: RefinedScenarioSpec) -> str:
    corpus_lines = []
    for i, d in enumerate(spec.corpus_samples):
        meta = d.metadata or {}
        corpus_lines.append(
            f"[doc {i}] doc_id={d.doc_id} "
            f"doc_type={meta.get('doc_type','?')} "
            f"date={meta.get('date','?')} "
            f"author={meta.get('author','?')}\n"
            f"  title='{d.title}'\n"
            f"  content_preview: {(d.content or '')[:300]}..."
        )
    corpus_summary = "\n".join(corpus_lines)
    return (
        f"【description_refined】\n{spec.description_refined}\n\n"
        f"【corpus_samples】({len(spec.corpus_samples)} 篇)\n"
        f"{corpus_summary}\n\n"
        f"【已推断的 Stage A 维度提示】\n"
        f"perspective: {spec.perspective}\n"
        f"subject_type: {spec.subject_type}\n"
        f"temporal_pattern: {spec.temporal_pattern}\n\n"
        f"请按维度严格分析。"
    )


def _cell_to_tuple(c: dict) -> tuple:
    return (c.get("I", ""), c.get("S", ""), c.get("V", ""))


def _calc_bias_score(per_doc_positions: list) -> float:
    """基于 (I, S, V) 三维联合分布的 entropy 算 bias_score。

    完全均衡 = 1.0, 完全偏 = 0.0
    """
    if not per_doc_positions:
        return 0.0
    cells = [_cell_to_tuple(p) for p in per_doc_positions]
    counter = Counter(cells)
    n = len(cells)
    # 实际 entropy
    entropy = 0.0
    for count in counter.values():
        p = count / n
        if p > 0:
            entropy -= p * math.log2(p)
    # 最大可能 entropy(假设 n 篇文档全部分布在 n 个不同 cell)
    max_entropy = math.log2(max(n, 1)) if n > 1 else 1.0
    return entropy / max_entropy if max_entropy > 0 else 0.0


def check_seed_coverage(spec: RefinedScenarioSpec) -> SeedCoverageReport:
    """用 LLM 检测 corpus_samples 在 5 维空间的覆盖。"""
    msgs = [
        {"role": "system", "content": COVERAGE_SYSTEM},
        {"role": "user", "content": _build_coverage_user_prompt(spec)},
    ]
    try:
        data = config.chat_json(msgs, temperature=0.3, max_tokens=3072)
    except Exception as e:
        print(f"[coverage] LLM 异常: {e},返回 minimal report")
        return SeedCoverageReport(
            covered_cells=[], missing_cells=[], bias_score=0.0,
            per_doc_position=[],
        )

    per_doc = list(data.get("per_doc_position", []))
    implied = list(data.get("implied_cells_from_spec", []))
    missing = list(data.get("missing_cells", []))
    covered = [_cell_to_tuple(p) for p in per_doc]
    bias_score = _calc_bias_score(per_doc)
    return SeedCoverageReport(
        covered_cells=covered,
        missing_cells=[_cell_to_tuple(m) for m in missing],
        bias_score=bias_score,
        per_doc_position=per_doc,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 2. Seed 自举合成
# ─────────────────────────────────────────────────────────────────────────────

EXPAND_SYSTEM = """你是文档分布扩展专家(类 Self-Instruct 自举)。\
现在用户给了 N 篇原始 samples,但它们在 (I, S, V) 维度空间分布不均。\
你需要为指定的【缺失维度组合】合成 1 篇新文档。

【硬约束】
1. 必须与 description_refined 主旨一致
2. 必须风格与原 samples 相近(同一组织/同一项目语境),不能跳脱
3. 必须严格落在指定的 (I, S, V) 组合
4. 必须含合理的 metadata:date(ISO YYYY-MM-DD,跟 samples 时间窗一致)/ \
author(从 samples 抽或新名)/ doc_type(符合 I 的具体类型)
5. content 长度 300-800 字,要有真实细节(字段名、数值、人名)

【ingest channel → doc_type 映射(参考)】
- meeting_minutes → "周会纪要" / "项目评审纪要"
- weekly_report → "周报" / "日报" / "月报"
- long_document → "技术设计文档" / "PRD" / "复盘报告" / "交接文档"
- email_im → "邮件" / "Slack 消息"
- ticket_crm → "工单" / "客户反馈"

【输出严格 JSON】
{
  "doc_id": "synthetic_<short_uuid>",
  "title": "...",
  "content": "...(300-800 字)",
  "metadata": {
    "date": "YYYY-MM-DD",
    "author": "...",
    "doc_type": "..."
  }
}"""


def _build_expand_user_prompt(
    spec: RefinedScenarioSpec,
    samples_summary: str,
    target_cell: tuple,
    idx: int,
) -> str:
    I, S, V = target_cell
    return (
        f"【description_refined】\n{spec.description_refined}\n\n"
        f"【原始 samples 摘要】\n{samples_summary}\n\n"
        f"【需要合成的缺失维度组合】(第 {idx+1} 篇 expansion)\n"
        f"I (ingest channel) = {I}\n"
        f"S (memory subject) = {S}\n"
        f"V (perspective) = {V}\n\n"
        f"请合成 1 篇新文档,严格 JSON 输出。"
    )


def _summarize_samples_brief(samples: list, max_per_doc: int = 200) -> str:
    lines = []
    for i, d in enumerate(samples):
        meta = d.metadata or {}
        lines.append(
            f"[doc {i}] doc_type={meta.get('doc_type','?')} "
            f"date={meta.get('date','?')} "
            f"title='{d.title}'\n"
            f"  preview: {(d.content or '')[:max_per_doc]}..."
        )
    return "\n".join(lines)


def expand_seeds_via_llm(
    spec: RefinedScenarioSpec,
    missing_cells: list,
    max_new: int = 5,
) -> list[Document]:
    """针对 missing_cells,用 LLM 自举合成 max_new 篇新 seed。"""
    samples_summary = _summarize_samples_brief(spec.corpus_samples)
    new_docs: list[Document] = []
    for idx, cell in enumerate(missing_cells[:max_new]):
        msgs = [
            {"role": "system", "content": EXPAND_SYSTEM},
            {"role": "user", "content": _build_expand_user_prompt(
                spec, samples_summary, cell, idx
            )},
        ]
        try:
            data = config.chat_json(msgs, temperature=0.7, max_tokens=4096)
        except Exception as e:
            print(f"[expand] cell={cell} LLM 异常: {e},跳过")
            continue
        doc_id = str(data.get("doc_id", f"synthetic_{idx}"))
        new_docs.append(Document(
            doc_id=doc_id,
            title=str(data.get("title", "")),
            content=str(data.get("content", "")),
            metadata={
                **(data.get("metadata", {}) or {}),
                "synthetic": True,           # ★ 显式标记
                "target_cell": list(cell),    # 标记它服务于哪个 cell
            },
        ))
    return new_docs


# ─────────────────────────────────────────────────────────────────────────────
# 3. 主入口:expand_scenario_seeds
# ─────────────────────────────────────────────────────────────────────────────

def expand_scenario_seeds(
    spec: RefinedScenarioSpec,
    max_new_seeds: int = 5,
    bias_threshold: float = 0.7,
) -> tuple[list[Document], SeedCoverageReport]:
    """主入口:check_seed_coverage + expand_seeds_via_llm。

    Returns:
        (expanded_pool, report):
          - expanded_pool = 原 samples + 新合成的 seeds
          - report = 检测报告(可 audit)
    """
    print(f"[expand] 检测 corpus_samples 覆盖({len(spec.corpus_samples)} 篇)...")
    report = check_seed_coverage(spec)
    print(f"[expand] bias_score: {report.bias_score:.3f}"
          f"(<{bias_threshold} 触发合成)")
    print(f"[expand] missing_cells: {len(report.missing_cells)}")
    for c in report.missing_cells:
        print(f"  - {c}")

    if report.bias_score >= bias_threshold and not report.missing_cells:
        print("[expand] 覆盖均衡,无需合成新 seed")
        return list(spec.corpus_samples), report

    print(f"[expand] 开始合成 max {max_new_seeds} 篇新 seed...")
    new_docs = expand_seeds_via_llm(spec, report.missing_cells, max_new=max_new_seeds)
    print(f"[expand] 合成完成 {len(new_docs)} 篇新 seed")
    return list(spec.corpus_samples) + new_docs, report


# ─────────────────────────────────────────────────────────────────────────────
# 4. 测试入口:故意偏 5 篇全周报
# ─────────────────────────────────────────────────────────────────────────────

def main():
    """W1.5 V3 T4 测试:5 篇全周报(故意偏)→ 检测 + 合成新 seed。"""
    biased_spec = RefinedScenarioSpec(
        name="office_engineering_weekly",
        description_refined=(
            "我是一名业务线 leader,负责每周审阅团队工程迭代。"
            "团队产出:周报(项目状态)、会议纪要(评审决策)、技术方案(架构演进)、"
            "交接文档(人员变动)。我需要追踪字段变化和决策依据。"
        ),
        corpus_samples=[
            Document(doc_id=f"d{i}", title=f"AI 工程周报 W{i+17}",
                     content=f"本周 P0 缺陷率 {20-i*3}%。负责人:张三(W{20}起为李四)。",
                     metadata={"date": f"2025-04-{17+i*7:02d}",
                              "doc_type": "周报",
                              "author": "张三"})
            for i in range(5)  # 5 篇全周报!I 完全单一
        ],
        perspective="cross_line_leader",
        subject_type="project",
        temporal_pattern="weekly",
        target_size=50,
        cognitive_flavor_hint="mixed",
    )

    print("=" * 60)
    print("=== 输入:5 篇全周报(故意偏)===")
    print("=" * 60)
    for d in biased_spec.corpus_samples:
        print(f"  {d.doc_id}: {d.title} [{d.metadata.get('doc_type')}]")
    print()

    expanded_pool, report = expand_scenario_seeds(
        biased_spec, max_new_seeds=3, bias_threshold=0.7,
    )

    print()
    print("=" * 60)
    print(f"=== Expanded Seed Pool: {len(expanded_pool)} 篇 ===")
    print("=" * 60)
    for d in expanded_pool:
        synth_mark = " [SYNTHETIC]" if d.metadata.get("synthetic") else ""
        print(f"  {d.doc_id}: {d.title} [{d.metadata.get('doc_type')}]{synth_mark}")
        if d.metadata.get("synthetic"):
            print(f"    target_cell: {d.metadata.get('target_cell')}")
            print(f"    content_preview: {d.content[:200]}...")

    print()
    print("=" * 60)
    print("=== Coverage Report ===")
    print("=" * 60)
    print(f"bias_score: {report.bias_score:.3f}")
    print(f"per_doc_position:")
    for p in report.per_doc_position:
        print(f"  - {p.get('doc_id')}: I={p.get('I')}, S={p.get('S')}, V={p.get('V')}")
    print(f"missing_cells:")
    for c in report.missing_cells:
        print(f"  - {c}")


if __name__ == "__main__":
    main()
