"""
pipeline.stage_0_refinement — Stage 0: Spec Refinement Layer (Clarifier)。

实现 LangGraph open_deep_research 的两步走范式:
  Step 1: clarify_step → {need_clarification: bool, question, verification}
  Step 2: compile_step → RefinedScenarioSpec

5 原则(Brief Writer 硬约束,抄自 LangGraph):
  1. Max specificity — 尽可能具体
  2. Fill unstated as open-ended NOT guess — 用户没说的标 open-ended
  3. Avoid unwarranted assumptions — 不无依据假设
  4. First person — 第一人称改写
  5. Cite corpus concretely — 引用 corpus 具体细节

硬约束:
  - 硬上限 2 轮 clarification
  - 一次最多 6 问 markdown 列表
  - 缩写/术语必问
  - open_ended_fields 字段可 audit(支持 W4 Clarifier ablation)

详见 docs/anchors/redesign_v3.md 第 4.1 节 + survey/spec_refinement/REPORT.md。

跑法:
  cd memory_bench_factory
  ./venv/bin/python -m pipeline.stage_0_refinement
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import json
import sys

# config 在项目根目录(memory_bench_factory/),非 pipeline 包内
# 这里用 sys.path 显式加,确保 -m pipeline.xxx 调用时能找到
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
import config

from .schema import RawScenarioInput, RefinedScenarioSpec, Document


# ─────────────────────────────────────────────────────────────────────────────
# Clarifier 输出
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ClarifyOutput:
    """Clarifier Step 1 输出(LangGraph `ClarifyWithUser` 三字段)。"""
    need_clarification: bool
    question: str = ""        # markdown 列表(若 need_clarification=True)
    verification: str = ""    # 简短确认(若 need_clarification=False)


# ─────────────────────────────────────────────────────────────────────────────
# System Prompts(5 原则硬约束)
# ─────────────────────────────────────────────────────────────────────────────

CLARIFY_SYSTEM = """你是 memory benchmark 设计专家。用户想合成一套 memory 评测 benchmark,\
但他的描述可能不清晰、不完整、不结构化。你的任务:判断当前输入是否足够清晰,如果不够,\
问最多 6 个澄清问题(markdown 列表)。

【绝对规则】
1. 缺字段【不要猜】,标 open-ended 等用户补
2. 如果已经问过一轮,几乎不再问(only ASK IF ABSOLUTELY NECESSARY)
3. 一次问最多 6 个问题,markdown 列表格式
4. 缩写/术语用户没解释清楚的,必须问
5. 不要重复问已知信息(corpus_samples 已暴露的细节不问)
6. 优先问的字段(按重要性排序):评测视角 / 记忆主体 / 时间动力学 / cognitive flavor / 期望题量

【输出严格 JSON 格式】(不要 markdown 包裹)
{
  "need_clarification": true | false,
  "question": "若需追问,markdown 列表形式问题清单(空字符串 if false)",
  "verification": "若不需追问,简短确认看到了什么(空字符串 if true)"
}"""


COMPILE_SYSTEM = """你是 memory benchmark 设计专家。用户已完成澄清对话,\
你需要把对话编译成结构化的 RefinedScenarioSpec(JSON)。

【五原则】(LangGraph 风格 Brief Writer 硬约束)
1. Max specificity — 尽可能具体,不要模糊
2. Fill unstated as open-ended NOT guess — 用户没说的字段名加入 open_ended_fields,不要脑补默认值
3. Avoid unwarranted assumptions — 不无依据假设
4. First person — description_refined 用第一人称改写
5. Cite corpus concretely — description 内引用 corpus_samples 里的具体细节

【字段取值约束】
- perspective ∈ {first_person, line_manager, cross_line_leader, partner_customer, audit_compliance, successor, external_analyst}
- subject_type ∈ {individual, customer, project, business_line, team, contract_matter, code_repo, product_sku, case, compliance_item}
- temporal_pattern ∈ {preference_drift, project_phase, ticket_state_machine, version_milestone, financial_cycle, event_driven, weekly, monthly, daily, none}
- cognitive_flavor_hint ∈ {episodic-heavy, semantic-heavy, procedural-heavy, mixed} 或 null

【输出严格 JSON 格式】(不要 markdown 包裹)
{
  "name": "场景名(蛇形小写,如 'office_engineering_weekly')",
  "description_refined": "1-3 段第一人称改写后的清晰描述",
  "perspective": "...",
  "subject_type": "...",
  "temporal_pattern": "...",
  "target_size": 50,
  "cognitive_flavor_hint": "..." or null,
  "open_ended_fields": ["列出用户没明说的字段名"]
}"""


# ─────────────────────────────────────────────────────────────────────────────
# Prompt 工具
# ─────────────────────────────────────────────────────────────────────────────

def _summarize_corpus(corpus_samples: list, max_chars_per_doc: int = 500) -> str:
    """对每篇 doc 做精简摘要 inject 进 prompt。"""
    if not corpus_samples:
        return "(无 corpus_samples)"
    lines = []
    for i, d in enumerate(corpus_samples):
        meta_str = json.dumps(d.metadata or {}, ensure_ascii=False)[:200]
        content_preview = (d.content or "")[:max_chars_per_doc].replace("\n", " ")
        lines.append(
            f"[doc {i}] title='{d.title}' meta={meta_str}\n"
            f"  preview: {content_preview}..."
        )
    return "\n".join(lines)


def _build_clarify_user_prompt(raw: RawScenarioInput, history: list) -> str:
    corpus_summary = _summarize_corpus(raw.corpus_samples)
    history_str = ""
    if history:
        history_str = "\n\n【已有对话历史】\n" + "\n".join(
            f"轮 {i+1} 我问: {h['question'][:300]}\n"
            f"轮 {i+1} 用户答: {h.get('answer','(待回答)')[:300]}"
            for i, h in enumerate(history)
        )
    optional_hints = json.dumps(raw.optional_hints or {}, ensure_ascii=False)
    return (
        f"【场景 description】\n{raw.description}\n\n"
        f"【corpus_samples 摘要】({len(raw.corpus_samples)} 篇)\n"
        f"{corpus_summary}\n\n"
        f"【optional_hints】{optional_hints}"
        f"{history_str}\n\n"
        f"请判断是否需要追问。严格输出 JSON。"
    )


def _build_compile_user_prompt(raw: RawScenarioInput, history: list) -> str:
    corpus_summary = _summarize_corpus(raw.corpus_samples, max_chars_per_doc=800)
    history_str = ""
    if history:
        history_str = "\n\n【澄清对话历史】\n" + "\n".join(
            f"轮 {i+1} 我问: {h['question']}\n轮 {i+1} 用户答: {h.get('answer','(未答)')}"
            for i, h in enumerate(history)
        )
    return (
        f"【原始 description】\n{raw.description}\n\n"
        f"【corpus_samples 摘要】({len(raw.corpus_samples)} 篇)\n"
        f"{corpus_summary}"
        f"{history_str}\n\n"
        f"请按字段约束编译成 RefinedScenarioSpec JSON。"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Clarifier 主类
# ─────────────────────────────────────────────────────────────────────────────

class Clarifier:
    """两步走 Clarifier:clarify_step + compile_step。"""

    def __init__(self, max_rounds: int = 2):
        self.max_rounds = max_rounds

    def clarify_step(self, raw: RawScenarioInput, history: list) -> ClarifyOutput:
        """一轮 clarify;LLM 返回 need_clarification + question + verification。"""
        # 硬上限触发:强制 stop
        if history and len(history) >= self.max_rounds:
            return ClarifyOutput(
                need_clarification=False,
                verification="已达 max_rounds=2,剩余字段标 open-ended"
            )
        msgs = [
            {"role": "system", "content": CLARIFY_SYSTEM},
            {"role": "user", "content": _build_clarify_user_prompt(raw, history)},
        ]
        try:
            data = config.chat_json(msgs, temperature=0.3, max_tokens=2048)
        except Exception as e:
            print(f"[clarifier] LLM 异常,降级到 need=False: {e}")
            return ClarifyOutput(need_clarification=False,
                                 verification=f"LLM fallback: {e}")
        return ClarifyOutput(
            need_clarification=bool(data.get("need_clarification", False)),
            question=str(data.get("question", "")).strip(),
            verification=str(data.get("verification", "")).strip(),
        )

    def compile_step(self, raw: RawScenarioInput, history: list) -> RefinedScenarioSpec:
        """编译成 RefinedScenarioSpec。"""
        msgs = [
            {"role": "system", "content": COMPILE_SYSTEM},
            {"role": "user", "content": _build_compile_user_prompt(raw, history)},
        ]
        try:
            data = config.chat_json(msgs, temperature=0.3, max_tokens=4096)
        except Exception as e:
            print(f"[compile] LLM 异常,返回 minimal fallback spec: {e}")
            return RefinedScenarioSpec(
                name="fallback_scenario",
                description_refined=raw.description,
                corpus_samples=raw.corpus_samples,
                open_ended_fields=["all"],
                clarification_history=history,
            )
        return RefinedScenarioSpec(
            name=str(data.get("name", "unnamed_scenario")),
            description_refined=str(data.get("description_refined", raw.description)),
            corpus_samples=raw.corpus_samples,
            perspective=str(data.get("perspective", "")),
            subject_type=str(data.get("subject_type", "")),
            temporal_pattern=str(data.get("temporal_pattern", "")),
            target_size=int(data.get("target_size", 50)),
            cognitive_flavor_hint=data.get("cognitive_flavor_hint"),
            open_ended_fields=list(data.get("open_ended_fields", [])),
            clarification_history=history,
        )


# ─────────────────────────────────────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────────────────────────────────────

def refine_scenario(
    raw: RawScenarioInput,
    interactive: bool = False,
    auto_answers: Optional[dict] = None,
    verbose: bool = True,
) -> RefinedScenarioSpec:
    """主入口:从 RawScenarioInput 跑 Clarifier 循环 → RefinedScenarioSpec。

    Args:
        interactive: True 用 input() 跟用户交互(W1.5 实际用法)
                     False 用 auto_answers dict 自动回答(测试用)
        auto_answers: {round_idx: 答案字符串},仅 interactive=False 用
    """
    clarifier = Clarifier()
    history: list = []
    # 最多 max_rounds 次追问 + 1 次最终判定(强制 stop)
    for round_idx in range(clarifier.max_rounds + 1):
        out = clarifier.clarify_step(raw, history)
        if not out.need_clarification:
            if verbose:
                print(f"[refine] 不需追问。verification: {out.verification[:150]}")
            break
        if verbose:
            print(f"\n[refine] 第 {round_idx+1} 轮追问:")
            print(out.question)
        # 获取用户回答
        if interactive:
            try:
                answer = input("\n[你的回答]:\n").strip()
            except EOFError:
                answer = "(无输入)"
        else:
            answer = (auto_answers or {}).get(round_idx, "(测试模式无回答)")
            if verbose:
                print(f"\n[auto answer]: {answer}")
        history.append({"question": out.question, "answer": answer})

    if verbose:
        print(f"\n[refine] 完成 {len(history)} 轮澄清,开始编译 RefinedScenarioSpec...")
    spec = clarifier.compile_step(raw, history)
    return spec


# ─────────────────────────────────────────────────────────────────────────────
# 测试入口(模糊 description + 自动回答 → RefinedScenarioSpec)
# ─────────────────────────────────────────────────────────────────────────────

def main():
    """W1.5 V3 T2 测试:模糊 description + 自动回答 → 完整 RefinedScenarioSpec。"""
    raw = RawScenarioInput(
        description="办公场景的 memory benchmark",  # 故意模糊
        corpus_samples=[
            Document(
                doc_id="d1",
                title="AI 工程周报 W17",
                content="本周 P0 缺陷率 20%(2/10),Oncall 数量 10。"
                        "负责人:张三。下周计划:启动新索引架构方案。",
                metadata={"date": "2025-04-17", "doc_type": "周报", "author": "张三"},
            ),
            Document(
                doc_id="d2",
                title="新索引架构技术方案 v1",
                content="本方案讨论 PostgreSQL+pgvector 切到 Qdrant 的可行性。"
                        "评估维度:检索延迟、运维复杂度、迁移成本。",
                metadata={"date": "2025-04-20", "doc_type": "技术设计文档", "author": "李四"},
            ),
            Document(
                doc_id="d3",
                title="AI 工程周报 W18",
                content="本周 P0 缺陷率 5%。Oncall 数量 9。负责人变更为李四。"
                        "新索引架构方案已立项,开始 prototype。",
                metadata={"date": "2025-04-24", "doc_type": "周报", "author": "李四"},
            ),
        ],
    )
    # 自动回答(模拟真实用户)
    auto_answers = {
        0: "我希望站在【业务线 leader】视角,关注项目交付状态和字段变化。"
           "场景是 IT 团队的工程周迭代,周期是每周。"
           "我特别关注:① 字段是否随时间变化(冲突修正)② leader 能否识别过期信息(知识更新)。"
           "期望题量:50 题。认知风格:semantic-heavy(项目状态语义)+ 部分 episodic(事件)。",
    }
    spec = refine_scenario(raw, interactive=False, auto_answers=auto_answers)

    print()
    print("=" * 60)
    print("✓ RefinedScenarioSpec 编译完成")
    print("=" * 60)
    print(f"name: {spec.name}")
    print(f"description_refined: {spec.description_refined[:300]}...")
    print(f"perspective: {spec.perspective}")
    print(f"subject_type: {spec.subject_type}")
    print(f"temporal_pattern: {spec.temporal_pattern}")
    print(f"target_size: {spec.target_size}")
    print(f"cognitive_flavor_hint: {spec.cognitive_flavor_hint}")
    print(f"open_ended_fields: {spec.open_ended_fields}")
    print(f"clarification_history: {len(spec.clarification_history)} 轮")


if __name__ == "__main__":
    main()
