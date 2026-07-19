"""Task schema validation for memory-system smoke tests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator


REQUIRED_TOP_LEVEL = {"task_id", "scenario", "description", "sessions", "gold"}
REQUIRED_GOLD_FIELDS = {"required_memory", "success_condition"}
REQUIRED_TURN_FIELDS = {"turn_id", "user"}


@dataclass(frozen=True)
class SmokeTask:
    """Validated smoke task loaded from JSON."""

    path: Path
    data: dict[str, Any]

    @property
    def task_id(self) -> str:
        return str(self.data["task_id"])

    @property
    def scenario(self) -> str:
        return str(self.data["scenario"])


def load_smoke_task(path: str | Path) -> SmokeTask:
    """Load and validate a smoke task JSON file."""

    task_path = Path(path)
    data = json.loads(task_path.read_text(encoding="utf-8"))
    validate_smoke_task(data, source=str(task_path))
    return SmokeTask(path=task_path, data=data)


def validate_smoke_task(task: dict[str, Any], source: str = "<memory>") -> None:
    """Validate the common smoke task contract.

    This intentionally checks only the stable shape needed by the first runner.
    Scenario-specific scoring rules can be added later without changing the
    adapter interface.
    """

    missing = REQUIRED_TOP_LEVEL - set(task)
    if missing:
        raise ValueError(f"{source}: missing top-level fields: {sorted(missing)}")

    if not isinstance(task["sessions"], list) or len(task["sessions"]) < 2:
        raise ValueError(f"{source}: sessions must contain at least two sessions")

    gold = task["gold"]
    if not isinstance(gold, dict):
        raise ValueError(f"{source}: gold must be an object")
    missing_gold = REQUIRED_GOLD_FIELDS - set(gold)
    if missing_gold:
        raise ValueError(f"{source}: missing gold fields: {sorted(missing_gold)}")

    for session_index, session in enumerate(task["sessions"]):
        if not isinstance(session, dict):
            raise ValueError(f"{source}: session #{session_index} must be an object")
        if "session_id" not in session:
            raise ValueError(f"{source}: session #{session_index} missing session_id")
        turns = session.get("turns")
        if not isinstance(turns, list) or not turns:
            raise ValueError(f"{source}: session {session['session_id']} must contain turns")
        for turn_index, turn in enumerate(turns):
            if not isinstance(turn, dict):
                raise ValueError(
                    f"{source}: turn #{turn_index} in session {session['session_id']} must be an object"
                )
            missing_turn = REQUIRED_TURN_FIELDS - set(turn)
            if missing_turn:
                raise ValueError(
                    f"{source}: turn #{turn_index} in session {session['session_id']} "
                    f"missing fields: {sorted(missing_turn)}"
                )


def iter_task_turns(task: SmokeTask | dict[str, Any]) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
    """Yield ``(session, turn)`` pairs in task order."""

    data = task.data if isinstance(task, SmokeTask) else task
    for session in data["sessions"]:
        for turn in session["turns"]:
            yield session, turn
