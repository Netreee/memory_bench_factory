#!/usr/bin/env python3
"""机械验收一个 00–06 Benchmark Run 是否具备完整、可追溯的出厂条件。

输入可以是 run_id 或 Run 目录；输出逐项 PASS/FAIL/WARN，并以退出码 0/1
表示是否通过硬门。该工具只读产物，不调用模型，也不修改 Run。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.world_blueprint import (WorldBlueprintError,
                                      normalize_world_blueprint,
                                      structure_signature)


REQUIRED_ARTIFACTS = [
    "00_input.json",
    "01_whitepaper.json",
    "02_world.json",
    "03_orders.json",
    "03_well_posed_report.json",
    "04_questions.json",
    "05_corpus.json",
    "06_grounded_questions.json",
    "06_grounding_report.json",
    "manifest.json",
]


class Audit:
    """收集硬门和警告，并统一打印审计结论。"""

    def __init__(self) -> None:
        self.failures: list[str] = []
        self.warnings: list[str] = []
        self.passes: list[str] = []

    def check(self, condition: bool, message: str) -> None:
        (self.passes if condition else self.failures).append(message)

    def warn(self, condition: bool, message: str) -> None:
        if not condition:
            self.warnings.append(message)

    def print(self) -> None:
        for message in self.passes:
            print(f"PASS  {message}")
        for message in self.warnings:
            print(f"WARN  {message}")
        for message in self.failures:
            print(f"FAIL  {message}")
        print(
            f"\nSUMMARY pass={len(self.passes)} warn={len(self.warnings)} "
            f"fail={len(self.failures)} status={'PASS' if not self.failures else 'FAIL'}"
        )


def _read_json(path: Path):
    """读取 UTF-8 JSON；异常由调用方转成清晰的硬门失败。"""
    return json.loads(path.read_text(encoding="utf-8"))


def _as_list(value, nested_key: str | None = None) -> list:
    """兼容直接数组以及带 corpus/sessions 等包装的历史产物。"""
    if isinstance(value, list):
        return value
    if nested_key and isinstance(value, dict) and isinstance(value.get(nested_key), list):
        return value[nested_key]
    return []


def _resolve_run(value: str) -> Path:
    """将 run_id 或目录解析为绝对 Run 路径。"""
    path = Path(value).expanduser()
    if path.is_dir():
        return path.resolve()
    return (Path(__file__).resolve().parent.parent / "output" / "runs" / value).resolve()


def audit_run(run_dir: Path) -> Audit:
    """执行完整静态验收，返回包含全部证据的 Audit。"""
    audit = Audit()
    audit.check(run_dir.is_dir(), f"Run 目录存在：{run_dir}")
    if not run_dir.is_dir():
        return audit

    missing = [name for name in REQUIRED_ARTIFACTS if not (run_dir / name).is_file()]
    audit.check(not missing, "00–06 与 manifest 十项产物齐全" if not missing else f"缺少产物：{missing}")
    if missing:
        return audit

    data: dict[str, object] = {}
    for name in REQUIRED_ARTIFACTS:
        try:
            data[name] = _read_json(run_dir / name)
        except (OSError, json.JSONDecodeError) as exc:
            audit.failures.append(f"{name} 不是有效 JSON：{exc}")
    if audit.failures:
        return audit

    manifest = data["manifest.json"]
    whitepaper = data["01_whitepaper.json"]
    world = data["02_world.json"]
    audit.check(isinstance(manifest, dict) and manifest.get("status") == "done", "manifest.status=done")

    blueprint = None
    try:
        blueprint = normalize_world_blueprint(whitepaper)
        audit.check(not blueprint.get("legacy_adapter"), "使用显式 world_blueprint，而非 legacy 适配")
    except (WorldBlueprintError, TypeError, ValueError) as exc:
        audit.failures.append(f"world_blueprint 非法：{exc}")

    entities = world.get("entities", {}) if isinstance(world, dict) else {}
    entity_types = world.get("entity_types", {}) if isinstance(world, dict) else {}
    relations = _as_list(world.get("relations", []) if isinstance(world, dict) else [])
    events = _as_list(world.get("events", []) if isinstance(world, dict) else [])
    cascades = _as_list(world.get("cascades", []) if isinstance(world, dict) else [])
    audit.check(bool(entities), "真值世界含实体")
    audit.check(bool(entity_types) and set(entity_types) == set(entities), "每个实体都有且仅有一个类型")
    audit.check(bool(relations), "世界含关系实例")
    audit.check(bool(events), "世界含领域事件实例")

    if blueprint:
        world_blueprint = world.get("world_blueprint") if isinstance(world, dict) else None
        audit.check(
            isinstance(world_blueprint, dict)
            and structure_signature(world_blueprint) == structure_signature(blueprint),
            "白皮书与世界使用同一份冻结结构签名",
        )
        declared_types = {item.get("id") for item in blueprint.get("entity_types", [])}
        audit.check(set(entity_types.values()) <= declared_types, "实体类型均来自冻结蓝图")

        relation_counts = Counter(item.get("type") for item in relations)
        for declaration in blueprint.get("relation_types", []):
            relation_id = declaration.get("id")
            floor = int(declaration.get("min_count", 1) or 1)
            audit.check(relation_counts[relation_id] >= floor,
                        f"关系 {relation_id} 实例数 {relation_counts[relation_id]}≥{floor}")

        event_counts = Counter(item.get("type") for item in events)
        for declaration in blueprint.get("event_types", []):
            event_id = declaration.get("id")
            floor = int(declaration.get("min_count", 1) or 1)
            audit.check(event_counts[event_id] >= floor,
                        f"事件 {event_id} 实例数 {event_counts[event_id]}≥{floor}")

        cascade_counts = Counter(item.get("rule_id") for item in cascades)
        for rule in blueprint.get("causal_rules", []):
            rule_id = rule.get("id")
            audit.check(cascade_counts[rule_id] >= 1, f"因果规则 {rule_id} 有真实事件对见证")

        expected_sessions = int((blueprint.get("temporal_model") or {}).get("n_sessions", 0) or 0)
        audit.check(int(world.get("n_sessions", 0) or 0) == expected_sessions,
                    f"世界时间片数量与蓝图一致（{expected_sessions}）")

    orders = _as_list(data["03_orders.json"])
    questions = _as_list(data["04_questions.json"])
    grounded = _as_list(data["06_grounded_questions.json"])
    report = data["06_grounding_report.json"]
    audit.check(bool(orders), "订单非空")
    audit.check(bool(questions) and all(item.get("question") and "gt" in item for item in questions),
                "题库非空且每题含题面与机械 gold")
    audit.check(bool(grounded), "接地后题库非空")
    report_grounded = ((report.get("overall") or {}).get("grounded")
                       if isinstance(report, dict) else None)
    audit.check(report_grounded == len(grounded), "接地报告数量与 06 题库一致")

    algo = manifest.get("algo", {}) if isinstance(manifest, dict) else {}
    target = algo.get("targetspec", {}) if isinstance(algo, dict) else {}
    min_questions = int(target.get("min_questions", 0) or 0)
    audit.check(algo.get("met_status") == "MET", f"闭环状态为 MET（实际 {algo.get('met_status')}）")
    audit.check(len(grounded) >= min_questions, f"接地题数 {len(grounded)}≥总下限 {min_questions}")
    by_line = report.get("by_line", {}) if isinstance(report, dict) else {}
    for line_id, floor in (target.get("per_line_min", {}) or {}).items():
        count = int((by_line.get(line_id) or {}).get("grounded", 0) or 0)
        audit.check(count >= int(floor), f"{line_id} 接地题数 {count}≥下限 {floor}")

    corpus_wrapper = data["05_corpus.json"]
    corpus = corpus_wrapper.get("corpus", corpus_wrapper) if isinstance(corpus_wrapper, dict) else {}
    sessions = _as_list(corpus, "sessions")
    docs = [doc for session in sessions for doc in _as_list(session.get("docs", []))]
    doc_ids = [doc.get("doc_id") for doc in docs]
    audit.check(bool(sessions) and bool(docs), "语料含时间片与文档")
    audit.check(None not in doc_ids and len(doc_ids) == len(set(doc_ids)), "所有 doc_id 存在且唯一")
    signal_docs = [doc for doc in docs if "_sig_" in str(doc.get("doc_id", ""))]
    filler_docs = [doc for doc in docs if doc.get("is_filler") is True]
    audit.check(bool(signal_docs) and all(doc.get("fact_refs") for doc in signal_docs),
                "信号文档非空且全部带 fact_refs")
    audit.check(all(not doc.get("fact_refs") for doc in filler_docs), "filler 全部不携带 fact_refs")

    tracked_terms = set(entities)
    tracked_terms.update(field for fields in entities.values() for field in fields)
    filler_text = "\n".join(str(doc.get("content", "")) for doc in filler_docs)
    leaks = sorted(term for term in tracked_terms if term and term in filler_text)
    audit.check(not leaks, "filler 未触碰被追踪实体/字段" if not leaks else f"filler 泄漏追踪词：{leaks[:10]}")

    if manifest.get("scenario") == "game":
        office_markers = ["OA系统", "办公区", "行政部", "人力资源部", "员工培训", "公司员工", "食堂管理部"]
        crossed = [marker for marker in office_markers if marker in filler_text]
        audit.check(not crossed, "游戏 filler 未串入现代办公模板" if not crossed else f"游戏 filler 跨域：{crossed}")

    filler_ratio = len(filler_docs) / len(docs) if docs else 0.0
    audit.warn(filler_ratio <= 0.8, f"filler 文档占比偏高：{filler_ratio:.1%}")
    audit.warn(not report.get("drops"), f"接地闸丢弃 {report.get('n_dropped', 0)} 题，需人工抽检原因")
    return audit


def main() -> None:
    parser = argparse.ArgumentParser(description="机械验收一个 Memory Forge 00–06 Run")
    parser.add_argument("run", help="run_id 或 Run 目录")
    args = parser.parse_args()
    run_dir = _resolve_run(args.run)
    print(f"AUDIT {run_dir}\n")
    result = audit_run(run_dir)
    result.print()
    raise SystemExit(0 if not result.failures else 1)


if __name__ == "__main__":
    main()
