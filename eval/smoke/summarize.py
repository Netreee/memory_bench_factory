"""Summarize smoke-test JSONL logs."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


SUMMARY_FIELDS = [
    "system",
    "scenario",
    "task_id",
    "write_success",
    "retrieve_success",
    "memory_injected",
    "memory_used",
    "task_success",
    "latency_seconds",
    "num_memory_items",
    "error",
    "main_failure_reason",
]


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                rows.append(
                    {
                        "system": path.parent.name,
                        "task_id": path.stem,
                        "scenario": "",
                        "error": {
                            "stage": "eval",
                            "type": "JSONDecodeError",
                            "message": f"{path}:{line_no}: {exc}",
                        },
                    }
                )
    return rows


def _error_text(error: Any) -> str:
    if not error:
        return ""
    if isinstance(error, dict):
        stage = error.get("stage", "unknown")
        err_type = error.get("type", "Error")
        message = error.get("message", "")
        return f"{stage}:{err_type}:{message}"
    return str(error)


def summarize_log(path: Path) -> dict[str, Any]:
    rows = _read_jsonl(path)
    if not rows:
        return {
            "system": path.parent.name,
            "scenario": "",
            "task_id": path.stem,
            "write_success": False,
            "retrieve_success": False,
            "memory_injected": False,
            "memory_used": None,
            "task_success": None,
            "latency_seconds": 0,
            "num_memory_items": 0,
            "error": "eval:EmptyLog",
            "main_failure_reason": "empty log",
        }

    first = rows[0]
    errors = [_error_text(row.get("error")) for row in rows if row.get("error")]
    retrieved_rows = [row for row in rows if row.get("memory_retrieved")]
    injected_rows = [row for row in rows if row.get("memory_injected")]
    return {
        "system": first.get("system", path.parent.name),
        "scenario": first.get("scenario", ""),
        "task_id": first.get("task_id", path.stem),
        "write_success": any(row.get("memory_written") for row in rows),
        "retrieve_success": bool(retrieved_rows),
        "memory_injected": bool(injected_rows),
        "memory_used": None,
        "task_success": None,
        "latency_seconds": round(sum(float(row.get("latency_seconds") or 0) for row in rows), 6),
        "num_memory_items": len(retrieved_rows),
        "error": " | ".join(errors),
        "main_failure_reason": errors[0] if errors else "",
    }


def summarize_logs(log_root: str | Path) -> list[dict[str, Any]]:
    root = Path(log_root)
    return [summarize_log(path) for path in sorted(root.rglob("*.jsonl"))]


def write_csv(rows: list[dict[str, Any]], output_path: str | Path) -> None:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SUMMARY_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in SUMMARY_FIELDS})


def write_markdown(rows: list[dict[str, Any]], output_path: str | Path) -> None:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "| system | scenario | task_id | write | retrieve | injected | error |",
        "|---|---|---|---:|---:|---:|---|",
    ]
    for row in rows:
        lines.append(
            "| {system} | {scenario} | {task_id} | {write_success} | {retrieve_success} | "
            "{memory_injected} | {main_failure_reason} |".format(**row)
        )
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize smoke JSONL logs.")
    parser.add_argument("--log-root", default="output/smoke")
    parser.add_argument("--csv", default="output/smoke/smoke_summary.csv")
    parser.add_argument("--markdown", default="output/smoke/smoke_report.md")
    args = parser.parse_args()

    rows = summarize_logs(args.log_root)
    write_csv(rows, args.csv)
    write_markdown(rows, args.markdown)
    print(f"summarized {len(rows)} log files")


if __name__ == "__main__":
    main()
