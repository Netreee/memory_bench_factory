#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${MEMORY_FORGE_PYTHON:-$ROOT/venv/bin/python}"
API_PORT="${MEMORY_FORGE_API_PORT:-8791}"
WEB_PORT="${MEMORY_FORGE_WEB_PORT:-3000}"
API_HOST="${MEMORY_FORGE_API_HOST:-127.0.0.1}"
WEB_HOST="${MEMORY_FORGE_WEB_HOST:-127.0.0.1}"
PUBLIC_HOST="${MEMORY_FORGE_PUBLIC_HOST:-$API_HOST}"
WEB_ORIGIN="http://$PUBLIC_HOST:$WEB_PORT"
API_ORIGIN="http://$PUBLIC_HOST:$API_PORT"
ALLOWED_ORIGINS="${MEMORY_FORGE_ALLOWED_ORIGINS:-$WEB_ORIGIN}"

if [[ "$PUBLIC_HOST" == "0.0.0.0" ]]; then
  echo "MEMORY_FORGE_PUBLIC_HOST 不能是 0.0.0.0，请设置浏览器实际访问的 IP。" >&2
  exit 1
fi

if [[ ! -x "$PYTHON" ]]; then
  echo "找不到 Python 环境：$PYTHON" >&2
  exit 1
fi

if [[ ! -d "$ROOT/frontend/node_modules" ]]; then
  echo "前端依赖尚未安装，请先运行：cd $ROOT/frontend && npm install" >&2
  exit 1
fi

cleanup() {
  trap - INT TERM EXIT
  if [[ -n "${API_PID:-}" ]]; then
    kill "$API_PID" 2>/dev/null || true
    wait "$API_PID" 2>/dev/null || true
  fi
}
trap cleanup INT TERM EXIT

cd "$ROOT"
MEMORY_FORGE_ALLOWED_ORIGINS="$ALLOWED_ORIGINS" \
  "$PYTHON" -m uvicorn tools.live_demo_api:app --host "$API_HOST" --port "$API_PORT" &
API_PID=$!

cd "$ROOT/frontend"
echo "Memory Forge 页面：$WEB_ORIGIN"
echo "Memory Forge API：$API_ORIGIN"
MEMORY_FORGE_WEB_HOST="$WEB_HOST" \
  NEXT_PUBLIC_MEMORY_FORGE_API="$API_ORIGIN" \
  npm run dev -- --host "$WEB_HOST" --port "$WEB_PORT"
