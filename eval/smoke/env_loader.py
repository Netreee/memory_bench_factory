"""Environment bootstrap for smoke runs.

Loads the workspace-level ``apikey_env`` file without printing secrets and maps
the local DeepSeek/MudaBench variable names to the OpenAI-compatible names used
by memory-system adapters.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path


_LOADED = False


def _candidate_roots() -> list[Path]:
    here = Path(__file__).resolve()
    roots = [Path.cwd(), *Path.cwd().parents, *here.parents]
    seen: set[Path] = set()
    unique: list[Path] = []
    for root in roots:
        if root not in seen:
            seen.add(root)
            unique.append(root)
    return unique


def _parse_env_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None

    patterns = [
        r"^\$env:([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$",
        r"^export\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$",
        r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$",
    ]
    for pattern in patterns:
        match = re.match(pattern, stripped)
        if match:
            key, raw_value = match.groups()
            value = raw_value.strip()
            if (
                len(value) >= 2
                and value[0] == value[-1]
                and value[0] in {"'", '"'}
            ):
                value = value[1:-1]
            return key, value
    return None


def _load_apikey_env() -> Path | None:
    for root in _candidate_roots():
        path = root / "apikey_env"
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            parsed = _parse_env_line(line)
            if parsed is None:
                continue
            key, value = parsed
            os.environ.setdefault(key, value)
        return path
    return None


def _map_compat_vars() -> None:
    mappings = {
        "OPENAI_API_KEY": "DEEPSEEK_API_KEY",
        "OPENAI_BASE_URL": "MUDABENCH_BASE_URL",
        "INGEST_LLM_BASE_URL": "MUDABENCH_BASE_URL",
        "MODEL": "MUDABENCH_EXTRACTION_MODEL",
        "INGEST_LLM_MODEL": "MUDABENCH_EXTRACTION_MODEL",
    }
    for target, source in mappings.items():
        if not os.environ.get(target) and os.environ.get(source):
            os.environ[target] = os.environ[source]

    if not os.environ.get("MEM0_DIR"):
        for root in _candidate_roots():
            package_root = root / "memory_bench_factory"
            if package_root.is_dir():
                runtime = package_root / "output" / "runtime" / "mem0_home"
                runtime.mkdir(parents=True, exist_ok=True)
                os.environ["MEM0_DIR"] = str(runtime)
                break


def _add_local_sources() -> None:
    for root in _candidate_roots():
        for rel in ("mem0", "A-mem"):
            source = root / rel
            if source.is_dir():
                source_text = str(source)
                if source_text not in sys.path:
                    sys.path.insert(0, source_text)


def load_smoke_environment() -> None:
    """Load API env and local source paths once per process."""

    global _LOADED
    if _LOADED:
        return
    _load_apikey_env()
    _map_compat_vars()
    _add_local_sources()
    _LOADED = True
