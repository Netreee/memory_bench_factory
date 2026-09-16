#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${MEMORY_FORGE_PYTHON:-$ROOT/venv/bin/python}"
if [[ ! -x "$PYTHON" ]]; then
  if [[ -n "${MEMORY_FORGE_PYTHON:-}" ]]; then
    echo "Configured Python does not exist: $PYTHON" >&2
    exit 1
  fi
  python3 -m venv "$ROOT/venv"
fi
"$PYTHON" -m pip install -r "$ROOT/frontend/requirements-demo.txt"
cd "$ROOT/frontend"
npm ci
echo "安装完成。运行 $ROOT/scripts/live-demo.sh。"
