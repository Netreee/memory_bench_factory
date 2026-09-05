#!/usr/bin/env python3
"""在盲审结论返回后，用原始 world 与 corpus 亲核结构依赖是否完整。

该脚本只用于主审裁决，不属于 reviewer 可见盲包。它按实体、字段、时点比较
世界事件与 corpus 的 fact_refs，并重算聚合题、时序题和多跳题的关键依赖。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any


def load_json(path: Path) -> Any:
    """读取一个 UTF-8 JSON 文件。"""

    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def numeric(value: Any) -> float:
    """把百分比、人数和普通数字转换为可比较数值。"""

    match = re.search(r"-?\d+(?:\.\d+)?", str(value))
    if not match:
        raise ValueError(f"not numeric: {value!r}")
    return float(match.group())


def normalize(value: Any) -> str:
    """去掉空白与常见单位差异，供答案值比较。"""

    return re.sub(r"[\s人%％]", "", str(value))


def main() -> None:
    """输出字段级缺失事实、受影响题和可见语料重算结果。"""

    if len(sys.argv) != 3:
        raise SystemExit("usage: adjudicate_dependencies.py RUN_DIR OUT_JSON")
    run_dir = Path(sys.argv[1]).resolve()
    out_path = Path(sys.argv[2]).resolve()

    world = load_json(run_dir / "02_world.json")["entities"]
    corpus = load_json(run_dir / "05_corpus.json")["corpus"]["sessions"]
    questions = load_json(run_dir / "06_grounded_questions.json")

    rendered: set[tuple[int, str]] = set()
    for session in corpus:
        for document in session.get("docs") or []:
            for fact_ref in document.get("fact_refs") or []:
                rendered.add((session["session_id"], fact_ref))

    def fact_key(entity: str, field: str) -> str:
        return f"{entity}.{field}"

    def is_rendered(entity: str, field: str, event: dict[str, Any]) -> bool:
        return (event["session"], fact_key(entity, field)) in rendered

    def events(entity: str, field: str) -> list[dict[str, Any]]:
        return list((world.get(entity) or {}).get(field) or [])

    def event_at(entity: str, field: str, at_week: int) -> dict[str, Any] | None:
        eligible = [event for event in events(entity, field) if event["session"] <= at_week]
        return eligible[-1] if eligible else None

    expected_atoms = []
    for entity, fields in world.items():
        for field, history in fields.items():
            for event in history:
                expected_atoms.append(
                    {
                        "session": event["session"],
                        "entity": entity,
                        "field": field,
                        "value": event.get("value"),
                        "op": event.get("op"),
                    }
                )
    missing_atoms = [
        atom
        for atom in expected_atoms
        if (atom["session"], fact_key(atom["entity"], atom["field"])) not in rendered
    ]

    affected: list[dict[str, Any]] = []
    recomputed_mr: list[dict[str, Any]] = []
    for index, question in enumerate(questions, 1):
        qid = f"Q{index:03d}"
        capability = question.get("capability")
        entity = question.get("entity")
        field = question.get("field")
        gt = question.get("gt")

        if capability == "TR":
            target_session = int(gt["week"]) - 1
            history = events(entity, field)
            target_index = next(
                (i for i, event in enumerate(history) if event["session"] == target_session),
                None,
            )
            dependencies = []
            if target_index is not None:
                if target_index > 0:
                    dependencies.append(history[target_index - 1])
                dependencies.append(history[target_index])
            missing = [
                event for event in dependencies if not is_rendered(entity, field, event)
            ]
            if missing:
                affected.append(
                    {
                        "qid": qid,
                        "capability": capability,
                        "reason": "时序变化前后事实不完整",
                        "missing_dependencies": missing,
                    }
                )

        elif capability == "L2_multihop":
            current = entity
            at_week = int((question.get("aux") or {}).get("at_week", 0))
            dependencies = []
            broken = None
            for hop_field in (question.get("aux") or {}).get("path") or []:
                event = event_at(current, hop_field, at_week)
                if event is None:
                    broken = {"entity": current, "field": hop_field, "reason": "world 中无事件"}
                    break
                dependencies.append({"entity": current, "field": hop_field, **event})
                if not is_rendered(current, hop_field, event):
                    broken = {"entity": current, "field": hop_field, **event}
                    break
                current = event.get("value")
            if broken:
                affected.append(
                    {
                        "qid": qid,
                        "capability": capability,
                        "reason": "多跳路径中间事实未渲染",
                        "missing_dependencies": [broken],
                    }
                )

        elif capability == "MR":
            visible = [
                event
                for event in events(entity, field)
                if event.get("value") is not None
                and event.get("op") != "EXPIRE"
                and is_rendered(entity, field, event)
            ]
            agg = (question.get("aux") or {}).get("agg")
            if visible and agg in {"min", "max"}:
                chooser = min if agg == "min" else max
                selected = chooser(visible, key=lambda event: numeric(event["value"]))
                observed = selected["value"]
                expected = gt.get("value") if isinstance(gt, dict) else gt
                row = {
                    "qid": qid,
                    "entity": entity,
                    "field": field,
                    "agg": agg,
                    "gold": expected,
                    "visible_recomputed": observed,
                    "same": normalize(expected) == normalize(observed),
                    "visible_sessions": [event["session"] for event in visible],
                }
                recomputed_mr.append(row)
                if not row["same"]:
                    affected.append(
                        {
                            "qid": qid,
                            "capability": capability,
                            "reason": "按可见 corpus 重算聚合值与 gold 不同",
                            "gold": expected,
                            "visible_recomputed": observed,
                        }
                    )

        elif capability == "L3_order":
            missing = [
                event
                for event in gt
                if (event["session"], fact_key(entity, event["field"])) not in rendered
            ]
            if missing:
                affected.append(
                    {
                        "qid": qid,
                        "capability": capability,
                        "reason": "排序事件未完整渲染",
                        "missing_dependencies": missing,
                    }
                )

    summary: dict[str, int] = {}
    for row in affected:
        summary[row["capability"]] = summary.get(row["capability"], 0) + 1

    result = {
        "world_atoms": len(expected_atoms),
        "missing_world_atoms": len(missing_atoms),
        "missing_atoms": missing_atoms,
        "affected_questions": affected,
        "affected_by_capability": summary,
        "mr_recomputed": recomputed_mr,
    }
    with out_path.open("w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    print(json.dumps({key: result[key] for key in ("world_atoms", "missing_world_atoms", "affected_by_capability")}, ensure_ascii=False, indent=2))
    print("affected qids:", [row["qid"] for row in affected])


if __name__ == "__main__":
    main()
