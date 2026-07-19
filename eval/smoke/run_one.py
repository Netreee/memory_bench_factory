"""Run one smoke task against one MemorySystem."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from eval.smoke.env_loader import load_smoke_environment

load_smoke_environment()

from eval.memory_systems import make_system
from eval.memory_systems._utils import error_info
from eval.memory_systems.base import MemorySystem
from eval.smoke.prompts import build_agent_prompt, build_memory_block
from eval.smoke.task_schema import SmokeTask, iter_task_turns, load_smoke_task


def _turn_doc(task: SmokeTask, session: dict[str, Any], turn: dict[str, Any]) -> str:
    parts = [
        f"task_id: {task.task_id}",
        f"scenario: {task.scenario}",
        f"session_id: {session['session_id']}",
        f"turn_id: {turn['turn_id']}",
        f"user: {turn.get('user', '')}",
    ]
    if turn.get("env"):
        parts.append(f"env: {turn['env']}")
    if turn.get("expected_memory_write"):
        parts.append(f"expected_memory_write: {turn['expected_memory_write']}")
    if turn.get("expected_memory_use"):
        parts.append(f"expected_memory_use: {turn['expected_memory_use']}")
    return "\n".join(parts)


def _session_payload(task: SmokeTask, session: dict[str, Any], session_index: int) -> dict[str, Any]:
    return {
        "session_id": session_index + 1,
        "source_session_id": session["session_id"],
        "date": session.get("date") or f"1970-01-{session_index + 1:02d}",
        "docs": [_turn_doc(task, session, turn) for turn in session["turns"]],
    }


def _context_error(context: str) -> dict[str, str] | None:
    text = (context or "").strip()
    if text.startswith("(检索失败") or text.startswith("(retrieve failed"):
        return {"stage": "retrieve", "type": "AdapterReturnedFailure", "message": text}
    return None


def _has_effective_memory(context: str, memory_block: str) -> bool:
    text = (context or "").strip()
    if not text or text in {"(无检索结果)", "(no retrieval results)"}:
        return False
    if text.startswith("(检索失败") or text.startswith("(retrieve failed"):
        return False
    return bool(memory_block and "(no relevant memory retrieved)" not in memory_block)


def run_one_task(
    *,
    system_name: str,
    memory: MemorySystem,
    task: SmokeTask,
    output_path: str | Path,
    top_k: int = 5,
    save_full_prompt: bool = True,
    finalize_after_session: bool = True,
    skip_finalize: bool = False,
) -> list[dict[str, Any]]:
    """Run a validated smoke task and write one JSONL row per turn.

    The runner only ingests sessions after their turns have been logged. That
    keeps later questions from seeing their own expected answer while still
    testing cross-session recall.
    """

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []

    reset_error = None
    try:
        memory.reset()
    except Exception as exc:  # pragma: no cover - exercised by external adapters
        reset_error = error_info("reset", exc)

    for session_index, session in enumerate(task.data["sessions"]):
        session_docs = _session_payload(task, session, session_index)

        for _, turn in iter_task_turns({"sessions": [session]}):
            started = time.time()
            retrieved_context = ""
            retrieve_error = None
            try:
                retrieved_context = memory.retrieve(str(turn["user"]), top_k=top_k)
            except Exception as exc:  # pragma: no cover - exercised by external adapters
                retrieve_error = error_info("retrieve", exc)

            memory_block = build_memory_block(retrieved_context)
            prompt = build_agent_prompt(
                user_input=str(turn["user"]),
                env_input=str(turn.get("env", "")),
                retrieved_context=retrieved_context,
                task_description=str(task.data.get("description", "")),
            )
            context_error = _context_error(retrieved_context)
            has_effective_memory = _has_effective_memory(retrieved_context, memory_block)
            error = retrieve_error or context_error or reset_error
            record = {
                "system": system_name,
                "task_id": task.task_id,
                "scenario": task.scenario,
                "session_id": session["session_id"],
                "turn_id": turn["turn_id"],
                "user_input": turn.get("user", ""),
                "env_input": turn.get("env", ""),
                "memory_write_events": [
                    {"type": "session_doc", "content": doc} for doc in session_docs["docs"]
                ],
                "retrieve_query": turn.get("user", ""),
                "retrieved_memory": retrieved_context,
                "memory_in_prompt": memory_block,
                "prompt": prompt if save_full_prompt else "",
                "agent_output": None,
                "gold_required_memory": task.data["gold"]["required_memory"],
                "success_condition": task.data["gold"]["success_condition"],
                "memory_written": bool(session_docs["docs"]),
                "memory_retrieved": has_effective_memory,
                "memory_injected": has_effective_memory,
                "memory_used": None,
                "task_success": None,
                "latency_seconds": round(time.time() - started, 6),
                "input_tokens": None,
                "output_tokens": None,
                "error": error,
            }
            records.append(record)

        try:
            memory.ingest_session(session_docs)
            if finalize_after_session and not skip_finalize:
                memory.finalize_ingest()
        except Exception as exc:  # pragma: no cover - exercised by external adapters
            ingest_error = error_info("ingest", exc)
            if records:
                existing = records[-1].get("error")
                if existing:
                    # Preserve the first error (usually retrieve), annotate the ingest follow-up.
                    records[-1]["error"] = dict(existing, ingest_error=ingest_error)
                else:
                    records[-1]["error"] = ingest_error

    if not finalize_after_session and not skip_finalize:
        try:
            memory.finalize_ingest()
        except Exception as exc:  # pragma: no cover - exercised by external adapters
            finalize_error = error_info("finalize_ingest", exc)
            if records:
                existing = records[-1].get("error")
                if existing:
                    records[-1]["error"] = dict(existing, finalize_error=finalize_error)
                else:
                    records[-1]["error"] = finalize_error

    with output.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    return records


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one memory smoke task.")
    parser.add_argument("--system", default="fullcontext")
    parser.add_argument("--task", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    task = load_smoke_task(args.task)
    memory = make_system(args.system)
    run_one_task(
        system_name=args.system,
        memory=memory,
        task=task,
        output_path=args.output,
        top_k=args.top_k,
    )


if __name__ == "__main__":
    main()
