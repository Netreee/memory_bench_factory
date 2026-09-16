"""Compatibility imports for frontend checkouts; backend code lives at this root."""
from __future__ import annotations

import importlib
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def forward_module(alias: str, canonical: str) -> None:
    """Resolve an old frontend import or script to the canonical backend module."""
    if not all((ROOT / item).is_file() for item in (
        "pipeline/factory.py", "eval/__init__.py", "config.py",
    )):
        raise ImportError("Memory Forge backend is missing; use the complete repository checkout.")
    sys.path.insert(0, str(ROOT))
    if alias == "__main__":
        runpy.run_module(canonical, run_name="__main__")
        return
    existing = sys.modules.get(canonical)
    expected = ROOT.joinpath(*canonical.split("."))
    expected = expected / "__init__.py" if expected.is_dir() else expected.with_suffix(".py")
    if existing is not None and Path(getattr(existing, "__file__", "")).resolve() != expected.resolve():
        del sys.modules[canonical]
    module = importlib.import_module(canonical)
    sys.modules[alias] = module
