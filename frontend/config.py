"""Compatibility entry point; implementation: config."""
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[1]
if not (_ROOT / "backend_bootstrap.py").is_file():
    raise ImportError("Memory Forge backend is missing. Keep frontend inside the complete repository checkout.")
sys.path.insert(0, str(_ROOT))
from backend_bootstrap import forward_module

forward_module(__name__, "config")
