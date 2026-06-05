"""
pipeline.stage_a_dimensions — Stage A v3: 维度推断 (I, S, V, T, cognitive_flavor)。

输入: RefinedScenarioSpec (Stage 0 输出)
输出: ScenarioDimensions

V3 改动(相对 V1):
  - 输入改为 RefinedScenarioSpec(不再是模糊 ScenarioSpec)
  - 主路径 LLM 推 5 维(LLM-first)
  - 启发式 fallback 保留(LLM 不可用时降级)
  - V tie-breaker(更专的赢:external_analyst > audit_compliance > ... > first_person)
  - T 真分析 timestamp 节律(weekly/monthly/financial_cycle/event_driven/project_phase)
  - 新增 cognitive_flavor 推断(episodic/semantic/procedural-heavy/mixed)

详见 docs/anchors/redesign_v3.md 第 2-3 节、scenario_dimensions.md 第 3 节。

跑法:
  cd memory_bench_factory
  ./venv/bin/python -m pipeline.stage_a_dimensions
"""
from __future__ import annotations
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Optional
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config

from .schema import (
    RefinedScenarioSpec, ScenarioDimensions, Document,
    INGEST_CHANNELS, MEMORY_SUBJECTS, PERSPECTIVES, TEMPORAL_PATTERNS,
    COGNITIVE_FLAVORS,
)


# ─────────────────────────────────────────────────────────────────────────────
# 1. LLM 主路径
# ─────────────────────────────────────────────────────────────────────────────

LLM_INFER_SYSTEM = """你是 memory benchmark 维度推断专家。给定 RefinedScenarioSpec,\
你要推断出 5 个维度,严格按取值范围输出。

【5 个维度的取值范围】

1. I_ingest_channels (list[str]): 文档主要 ingest 通道类型,可多选,取值:
   dialogue / meeting_minutes / weekly_report / long_document /
   email_im / code_diff_pr / ticket_crm / table_dashboard / voice_transcript

2. S_memory_subject (str): 记忆主体,单选,取值:
   individual / customer / project / business_line / team /
   contract_matter / code_repo / product_sku / case / compliance_item

3. V_perspective (str): 评测视角,单选,取值:
   first_person / line_manager / cross_line_leader /
   partner_customer / audit_compliance / successor / external_analyst

4. T_temporal_pattern (str): 时间动力学,单选,取值:
   preference_drift / project_phase / ticket_state_machine /
   version_milestone / financial_cycle / event_driven /
   weekly / monthly / daily / none

5. cognitive_flavor (str): 认知风格 prior,单选,取值:
   episodic-heavy / semantic-heavy / procedural-heavy / mixed

【推断原则】
- 综合 corpus_samples 的 doc_type / content 结构 + description_refined 关键词
- description 含明确指示(如"业务线 leader 视角")严格采纳
- T 看 corpus_samples timestamp 节律(都按周间隔 → weekly)
- cognitive_flavor 启发:多 event 是 episodic-heavy,多 stable fact 是 semantic-heavy,\
多 how-to/workflow 是 procedural-heavy

【输出严格 JSON 格式】(不要 markdown 包裹)
{
  "I_ingest_channels": [...],
  "S_memory_subject": "...",
  "V_perspective": "...",
  "T_temporal_pattern": "...",
  "cognitive_flavor": "...",
  "reasoning": "一句话推断依据"
}"""


def _build_llm_user_prompt(spec: RefinedScenarioSpec) -> str:
    corpus_summary = "\n".join(
        f"[doc {i}] doc_type={(d.metadata or {}).get('doc_type','?')} "
        f"date={(d.metadata or {}).get('date','?')} title='{d.title}'\n"
        f"  content_preview: {(d.content or '')[:300]}..."
        for i, d in enumerate(spec.corpus_samples)
    )
    hint_lines = []
    hint_lines.append(
        f"perspective hint: {spec.perspective}"
        if spec.perspective else "perspective: (open-ended)"
    )
    hint_lines.append(
        f"subject_type hint: {spec.subject_type}"
        if spec.subject_type else "subject_type: (open-ended)"
    )
    hint_lines.append(
        f"temporal_pattern hint: {spec.temporal_pattern}"
        if spec.temporal_pattern else "temporal_pattern: (open-ended)"
    )
    hint_lines.append(
        f"cognitive_flavor hint: {spec.cognitive_flavor_hint}"
        if spec.cognitive_flavor_hint else "cognitive_flavor: (open-ended)"
    )
    hint_str = "\n".join(hint_lines)
    return (
        f"【description_refined】\n{spec.description_refined}\n\n"
        f"【corpus_samples】({len(spec.corpus_samples)} 篇)\n"
        f"{corpus_summary}\n\n"
        f"【Clarifier hints】\n{hint_str}\n\n"
        f"请推断 5 维,严格 JSON 输出。"
    )


def infer_dimensions_llm(spec: RefinedScenarioSpec) -> Optional[ScenarioDimensions]:
    """LLM 主路径。失败返回 None 触发 fallback。"""
    msgs = [
        {"role": "system", "content": LLM_INFER_SYSTEM},
        {"role": "user", "content": _build_llm_user_prompt(spec)},
    ]
    try:
        data = config.chat_json(msgs, temperature=0.3, max_tokens=2048)
    except Exception as e:
        print(f"[stage_a] LLM 异常: {e},触发启发式 fallback")
        return None
    return ScenarioDimensions(
        I_ingest_channels=list(data.get("I_ingest_channels", [])),
        S_memory_subject=str(data.get("S_memory_subject", "")),
        V_perspective=str(data.get("V_perspective", "")),
        T_temporal_pattern=str(data.get("T_temporal_pattern", "")),
        cognitive_flavor=str(data.get("cognitive_flavor", "mixed")),
        inference_notes={
            "source": "llm",
            "reasoning": data.get("reasoning", ""),
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# 2. 启发式 fallback
# ─────────────────────────────────────────────────────────────────────────────

DOC_TYPE_TO_INGEST = {
    "周会纪要": "meeting_minutes", "会议纪要": "meeting_minutes", "纪要": "meeting_minutes",
    "周报": "weekly_report", "日报": "weekly_report", "月报": "weekly_report",
    "述职": "weekly_report",
    "技术设计文档": "long_document", "技术方案": "long_document", "技术文档": "long_document",
    "内部说明文档": "long_document", "说明文档": "long_document", "交接文档": "long_document",
    "需求文档": "long_document", "PRD": "long_document", "项目执行计划": "long_document",
    "项目计划": "long_document", "执行计划": "long_document", "评测报告": "long_document",
    "调研报告": "long_document", "Release Note": "long_document", "OKR": "long_document",
    "复盘": "long_document", "实验记录": "long_document",
    "邮件": "email_im", "Slack": "email_im", "Lark": "email_im", "Mattermost": "email_im",
    "工单": "ticket_crm", "ticket": "ticket_crm",
}


SUBJECT_KEYWORDS = {
    "individual": ["用户", "个人助手", "个人助理", "first-person"],
    "customer": ["客户", "customer", "甲方", "服务对象"],
    "project": ["项目", "工程", "iteration", "迭代", "milestone", "PRD", "RFC"],
    "business_line": ["业务线", "BU", "事业部", "Group", "BG"],
    "team": ["团队", "小组", "team", "组员", "组内"],
    "code_repo": ["仓库", "repo", "代码库", "PR", "merge", "commit"],
    "contract_matter": ["合同", "matter", "case", "案件", "条款"],
    "compliance_item": ["合规事项", "compliance", "审计事项"],
}


PERSPECTIVE_KEYWORDS = {
    "first_person": ["我", "first-person", "用户视角"],
    "line_manager": ["PM", "项目经理", "项目负责人", "直接 lead", "直属上级", "line manager"],
    "cross_line_leader": ["业务线 leader", "业务线leader", "VP", "GM", "高层视角",
                          "跨业务线", "leader 视角"],
    "partner_customer": ["客户视角", "partner", "甲方视角"],
    "audit_compliance": ["审计", "合规", "audit", "compliance"],
    "successor": ["接班", "交接", "successor", "新负责人"],
    "external_analyst": ["外部分析师", "investor", "analyst"],
}

# V tie-breaker:更专的赢
PERSPECTIVE_PRIORITY = {
    "external_analyst": 7,
    "audit_compliance": 6,
    "successor": 5,
    "partner_customer": 4,
    "cross_line_leader": 3,
    "line_manager": 2,
    "first_person": 1,
}


def _heuristic_infer_I(spec: RefinedScenarioSpec) -> list:
    counter: Counter = Counter()
    for d in spec.corpus_samples:
        dt = ((d.metadata or {}).get("doc_type") or "").strip()
        assigned = None
        for key, ch in DOC_TYPE_TO_INGEST.items():
            if key in dt:
                assigned = ch
                break
        if assigned is None:
            assigned = "long_document"  # 保守默认
        counter[assigned] += 1
    return [ch for ch, _ in counter.most_common()]


def _heuristic_infer_S(spec: RefinedScenarioSpec) -> str:
    if spec.subject_type and spec.subject_type in MEMORY_SUBJECTS:
        return spec.subject_type
    text = (
        spec.description_refined
        + "\n"
        + "\n".join(d.content[:2000] for d in spec.corpus_samples)
    )
    scores = {k: 0 for k in MEMORY_SUBJECTS}
    for subj, kws in SUBJECT_KEYWORDS.items():
        for kw in kws:
            scores[subj] += text.count(kw)
    best = max(scores, key=lambda k: scores[k])
    return best if scores[best] > 0 else "project"


def _heuristic_infer_V(spec: RefinedScenarioSpec) -> str:
    """V 推断含 tie-breaker(更专的赢)。"""
    if spec.perspective and spec.perspective in PERSPECTIVES:
        return spec.perspective
    text = spec.description_refined
    scores = {k: 0 for k in PERSPECTIVES}
    for v, kws in PERSPECTIVE_KEYWORDS.items():
        for kw in kws:
            scores[v] += text.count(kw)
    # (score desc, priority desc) 排序
    sorted_v = sorted(
        scores.items(),
        key=lambda kv: (-kv[1], -PERSPECTIVE_PRIORITY.get(kv[0], 0)),
    )
    best = sorted_v[0][0]
    return best if scores[best] > 0 else "first_person"


def _heuristic_infer_T(spec: RefinedScenarioSpec) -> str:
    """T 真分析 timestamp 节律,不再 fallback 默认值。"""
    if spec.temporal_pattern and spec.temporal_pattern in TEMPORAL_PATTERNS:
        return spec.temporal_pattern
    timestamps = []
    for d in spec.corpus_samples:
        ts_str = (d.metadata or {}).get("date") or (d.metadata or {}).get("timestamp")
        if not ts_str:
            continue
        try:
            ts = datetime.fromisoformat(str(ts_str)[:10])
            timestamps.append(ts)
        except (ValueError, TypeError):
            continue
    if len(timestamps) < 2:
        return "event_driven"
    timestamps.sort()
    intervals = [
        (timestamps[i + 1] - timestamps[i]).days
        for i in range(len(timestamps) - 1)
    ]
    avg_interval = sum(intervals) / len(intervals)
    if avg_interval <= 0:
        return "none"
    # 变异系数粗算
    cv = (max(intervals) - min(intervals)) / max(avg_interval, 1)

    if avg_interval <= 1.5 and cv < 1.0:
        return "daily"
    if 5 <= avg_interval <= 9 and cv < 1.0:
        return "weekly"
    if 25 <= avg_interval <= 35 and cv < 1.0:
        return "monthly"
    if 85 <= avg_interval <= 95 and cv < 1.0:
        return "financial_cycle"
    if cv > 1.5:
        return "event_driven"
    return "project_phase"  # 中等间隔但不规则


def _heuristic_infer_cognitive_flavor(I: list, S: str, V: str) -> str:
    """启发式映射 (I, S, V) → cognitive_flavor。"""
    if "code_diff_pr" in I or S == "code_repo":
        return "procedural-heavy"
    has_event_chans = any(ch in I for ch in ("meeting_minutes", "email_im", "ticket_crm"))
    has_stable_chans = any(ch in I for ch in ("weekly_report", "long_document"))
    if has_event_chans and not has_stable_chans:
        return "episodic-heavy"
    if has_stable_chans and S in ("project", "business_line"):
        return "semantic-heavy"
    return "mixed"


def infer_dimensions_heuristic(spec: RefinedScenarioSpec) -> ScenarioDimensions:
    """纯启发式 fallback。"""
    I = _heuristic_infer_I(spec)
    S = _heuristic_infer_S(spec)
    V = _heuristic_infer_V(spec)
    T = _heuristic_infer_T(spec)
    cog = _heuristic_infer_cognitive_flavor(I, S, V)
    return ScenarioDimensions(
        I_ingest_channels=I,
        S_memory_subject=S,
        V_perspective=V,
        T_temporal_pattern=T,
        cognitive_flavor=cog,
        inference_notes={"source": "heuristic"},
    )


# ─────────────────────────────────────────────────────────────────────────────
# 3. 主入口
# ─────────────────────────────────────────────────────────────────────────────

def infer_dimensions(
    spec: RefinedScenarioSpec,
    use_llm: bool = True,
) -> ScenarioDimensions:
    """主入口:LLM 主路径 + 启发式 fallback。"""
    if use_llm:
        dims = infer_dimensions_llm(spec)
        if dims is not None:
            return dims
    return infer_dimensions_heuristic(spec)


# ─────────────────────────────────────────────────────────────────────────────
# 4. 测试
# ─────────────────────────────────────────────────────────────────────────────

def main():
    """W1.5 V3 T3 测试。"""
    spec = RefinedScenarioSpec(
        name="office_engineering_weekly",
        description_refined=(
            "我是一名业务线 leader,负责每周审阅团队的工程迭代进展。"
            "在 W17 的周报里,项目 P0 缺陷率为 20%,负责人是张三;"
            "W18 的周报中,P0 缺陷率下降到 5%,负责人变更为李四。"
            "我必须追踪字段变化,以最新信息为准。"
        ),
        corpus_samples=[
            Document(doc_id="d1", title="AI 工程周报 W17",
                     content="本周 P0 缺陷率 20%(2/10),Oncall 数量 10。负责人:张三。",
                     metadata={"date": "2025-04-17", "doc_type": "周报"}),
            Document(doc_id="d2", title="新索引架构技术方案 v1",
                     content="本方案讨论 PostgreSQL+pgvector 切到 Qdrant。",
                     metadata={"date": "2025-04-20", "doc_type": "技术设计文档"}),
            Document(doc_id="d3", title="AI 工程周报 W18",
                     content="本周 P0 缺陷率 5%。负责人变更为李四。",
                     metadata={"date": "2025-04-24", "doc_type": "周报"}),
        ],
        perspective="cross_line_leader",
        subject_type="project",
        temporal_pattern="weekly",
        target_size=50,
        cognitive_flavor_hint="mixed",
        open_ended_fields=["题型分布", "评测指标"],
    )

    print("=" * 60)
    print("=== Stage A v3:LLM 主路径 ===")
    print("=" * 60)
    dims_llm = infer_dimensions(spec, use_llm=True)
    print(f"I = {dims_llm.I_ingest_channels}")
    print(f"S = {dims_llm.S_memory_subject}")
    print(f"V = {dims_llm.V_perspective}")
    print(f"T = {dims_llm.T_temporal_pattern}")
    print(f"cognitive_flavor = {dims_llm.cognitive_flavor}")
    print(f"notes: {dims_llm.inference_notes}")
    print()

    print("=" * 60)
    print("=== Stage A v3:启发式 fallback(对照) ===")
    print("=" * 60)
    dims_h = infer_dimensions_heuristic(spec)
    print(f"I = {dims_h.I_ingest_channels}")
    print(f"S = {dims_h.S_memory_subject}")
    print(f"V = {dims_h.V_perspective}")
    print(f"T = {dims_h.T_temporal_pattern}")
    print(f"cognitive_flavor = {dims_h.cognitive_flavor}")
    print()

    print("=" * 60)
    print("=== T 节律测试(纯启发式)===")
    print("=" * 60)
    spec_weekly = RefinedScenarioSpec(
        name="t_weekly", description_refined="",
        corpus_samples=[
            Document(doc_id=f"d{i}", title=f"周报 W{i}", content="x",
                     metadata={"date": f"2025-04-{17+i*7:02d}", "doc_type": "周报"})
            for i in range(6)
        ],
    )
    timestamps = [d.metadata["date"] for d in spec_weekly.corpus_samples]
    print(f"timestamps(weekly 间隔): {timestamps}")
    print(f"推断 T = {_heuristic_infer_T(spec_weekly)} (期望 weekly)")
    print()

    spec_event = RefinedScenarioSpec(
        name="t_event", description_refined="",
        corpus_samples=[
            Document(doc_id="d0", title="x", content="x", metadata={"date": "2025-04-01"}),
            Document(doc_id="d1", title="x", content="x", metadata={"date": "2025-04-03"}),
            Document(doc_id="d2", title="x", content="x", metadata={"date": "2025-05-15"}),
            Document(doc_id="d3", title="x", content="x", metadata={"date": "2025-08-20"}),
        ],
    )
    timestamps = [d.metadata["date"] for d in spec_event.corpus_samples]
    print(f"timestamps(不规则): {timestamps}")
    print(f"推断 T = {_heuristic_infer_T(spec_event)} (期望 event_driven)")


if __name__ == "__main__":
    main()
