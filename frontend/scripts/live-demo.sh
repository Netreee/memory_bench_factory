#!/usr/bin/env bash
set -euo pipefail

BACKEND_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if [[ ! -f "$BACKEND_ROOT/backend_bootstrap.py" || ! -f "$BACKEND_ROOT/scripts/live-demo.sh" ]]; then
  echo "Memory Forge backend is missing. Keep frontend inside the complete repository checkout." >&2
  exit 1
fi
exec bash "$BACKEND_ROOT/scripts/live-demo.sh" "$@"
