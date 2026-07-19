"""Config-driven smoke runner for MemorySystem adapters."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from eval.smoke.env_loader import load_smoke_environment

load_smoke_environment()

from eval.memory_systems import make_system
from eval.memory_systems._utils import error_info
from eval.smoke.run_one import run_one_task
from eval.smoke.task_schema import load_smoke_task


DEFAULT_CONFIG = Path("eval/smoke/configs/smoke_systems.json")
DEFAULT_TASK_DIR = Path("eval/smoke/tasks")
DEFAULT_OUTPUT_ROOT = Path("output/smoke")


def load_system_config(path: str | Path = DEFAULT_CONFIG) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def configured_systems(config: dict[str, Any], include_targets: bool = True) -> list[str]:
    systems = list(config.get("baselines", []))
    if include_targets:
        systems.extend(config.get("target_systems", []))
    excluded = set(config.get("excluded", []))
    return [system for system in systems if system not in excluded]


def load_tasks(task_dir: str | Path) -> list:
    return [load_smoke_task(path) for path in sorted(Path(task_dir).glob("*.json"))]


def write_init_failure(
    *,
    system_name: str,
    task,
    output_path: str | Path,
    error: dict[str, str],
) -> None:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "system": system_name,
        "task_id": task.task_id,
        "scenario": task.scenario,
        "session_id": None,
        "turn_id": None,
        "user_input": "",
        "env_input": "",
        "memory_write_events": [],
        "retrieve_query": "",
        "retrieved_memory": "",
        "memory_in_prompt": "",
        "prompt": "",
        "agent_output": None,
        "gold_required_memory": task.data["gold"]["required_memory"],
        "success_condition": task.data["gold"]["success_condition"],
        "memory_written": False,
        "memory_retrieved": False,
        "memory_injected": False,
        "memory_used": None,
        "task_success": None,
        "latency_seconds": 0,
        "input_tokens": None,
        "output_tokens": None,
        "error": error,
    }
    output.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")


def run_smoke(
    *,
    systems: list[str],
    tasks: list,
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
    top_k: int = 5,
    finalize_after_session: bool = True,
    skip_finalize: bool = False,
) -> list[Path]:
    """Run each configured system/task pair without aborting on failures."""

    written: list[Path] = []
    root = Path(output_root)
    for system_name in systems:
        for task in tasks:
            output_path = root / system_name / f"{task.task_id}.jsonl"
            try:
                memory = make_system(system_name)
            except Exception as exc:
                write_init_failure(
                    system_name=system_name,
                    task=task,
                    output_path=output_path,
                    error=error_info("init", exc),
                )
                written.append(output_path)
                continue

            run_one_task(
                system_name=system_name,
                memory=memory,
                task=task,
                output_path=output_path,
                top_k=top_k,
                finalize_after_session=finalize_after_session,
                skip_finalize=skip_finalize,
            )
            written.append(output_path)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Run memory-system smoke tests.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--task-dir", default=str(DEFAULT_TASK_DIR))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--systems", default="", help="Comma-separated override.")
    parser.add_argument("--targets", action="store_true", help="Include target systems from config.")
    parser.add_argument("--max-tasks", type=int, default=0, help="Only run the first N tasks.")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument(
        "--defer-finalize",
        action="store_true",
        help="Finalize only once after each task; useful for external systems with slow finalize hooks.",
    )
    parser.add_argument(
        "--skip-finalize",
        action="store_true",
        help="Skip adapter finalize hooks; useful for init/error smoke of external services.",
    )
    args = parser.parse_args()

    config = load_system_config(args.config)
    systems = (
        [item.strip() for item in args.systems.split(",") if item.strip()]
        if args.systems
        else configured_systems(config, include_targets=args.targets)
    )
    tasks = load_tasks(args.task_dir)
    if args.max_tasks:
        tasks = tasks[: args.max_tasks]
    outputs = run_smoke(
        systems=systems,
        tasks=tasks,
        output_root=args.output_root,
        top_k=args.top_k,
        finalize_after_session=not args.defer_finalize,
        skip_finalize=args.skip_finalize,
    )
    print(f"wrote {len(outputs)} smoke log files")


if __name__ == "__main__":
    main()
