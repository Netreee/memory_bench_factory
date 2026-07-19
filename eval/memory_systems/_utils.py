"""Shared utilities for memory-system adapters.

Avoids duplicating the same header builder, error struct, default model
names, and workspace path setup across every adapter file.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Default model / embedding constants
# ---------------------------------------------------------------------------

DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"
DEFAULT_LLM_MODEL = "gpt-4o-mini"

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def header(session_id: Any, date: str) -> str:
    """Standard session-marker prefix used across adapters."""
    return f"[周期{session_id} | 日期 {date}] "


def error_info(stage: str, exc: BaseException) -> dict[str, str]:
    """Normalised error struct consumed by smoke runners."""
    return {"stage": stage, "type": type(exc).__name__, "message": str(exc)}


# ---------------------------------------------------------------------------
# Path injection – each adapter can call this so it works both via the
# smoke runner (which calls env_loader first) and standalone.
# ---------------------------------------------------------------------------

_WORKSPACE_PATHS_INJECTED = False


def _ensure_workspace_paths() -> None:
    """Add workspace-level source directories to sys.path once."""
    global _WORKSPACE_PATHS_INJECTED
    if _WORKSPACE_PATHS_INJECTED:
        return

    # The adapter files live in …/memory_bench_factory/eval/memory_systems/
    # so ROOT = memory_bench_factory/ and WORKSPACE_ROOT is the repo root.
    root = Path(__file__).resolve().parent.parent.parent  # memory_bench_factory/
    workspace = root.parent

    for candidate in (root, workspace / "mem0", workspace / "A-mem",
                      workspace / "raptor", workspace / "HippoRAG" / "src",
                      workspace / "MIRIX"):
        if candidate.exists() and str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))

    _WORKSPACE_PATHS_INJECTED = True


# ---------------------------------------------------------------------------
# HF cache helper (used by raptor / hipporag)
# ---------------------------------------------------------------------------


def ensure_hf_home(root_package: Path | None = None) -> str:
    """Set HF_HOME / TRANSFORMERS_CACHE under the package output dir.

    Returns the HF_HOME path so callers can use it if needed.
    """
    if root_package is None:
        root_package = Path(__file__).resolve().parent.parent.parent
    hf = root_package / "output" / "runtime" / "hf_home"
    hf.mkdir(parents=True, exist_ok=True)
    import os
    os.environ.setdefault("HF_HOME", str(hf))
    os.environ.setdefault("TRANSFORMERS_CACHE", str(hf / "transformers"))
    return str(hf)
