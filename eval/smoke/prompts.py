"""Prompt construction for smoke-test agent calls."""

from __future__ import annotations

from typing import Any


def build_memory_block(retrieved_context: str | list[dict[str, Any]] | None) -> str:
    """Render retrieved memory into a stable prompt section."""

    if not retrieved_context:
        return "(no relevant memory retrieved)"
    if isinstance(retrieved_context, str):
        return retrieved_context.strip() or "(no relevant memory retrieved)"

    lines = []
    for index, item in enumerate(retrieved_context, start=1):
        text = str(item.get("text", "")).strip()
        score = item.get("score")
        prefix = f"[memory {index}]"
        if isinstance(score, (int, float)):
            prefix += f" score={score:.3f}"
        lines.append(f"{prefix} {text}".strip())
    return "\n".join(lines) if lines else "(no relevant memory retrieved)"


def build_agent_prompt(
    *,
    user_input: str,
    env_input: str = "",
    retrieved_context: str | list[dict[str, Any]] | None = None,
    task_description: str = "",
    output_format: str = "Produce the next response or action.",
) -> str:
    """Build the common prompt used by smoke-test runners."""

    sections = [
        "You are an agent solving a multi-session task.",
        "",
        "Relevant memory:",
        build_memory_block(retrieved_context),
        "",
        "Current environment / observation:",
        env_input.strip() or "(none)",
        "",
        "Current user request:",
        user_input.strip(),
    ]
    if task_description:
        sections.extend(["", "Task description:", task_description.strip()])
    sections.extend(
        [
            "",
            "Use the relevant memory only when it is useful.",
            "Do not invent memory that is not provided.",
            output_format.strip(),
        ]
    )
    return "\n".join(sections)
