#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFAULT_PYTHON="$ROOT/.venv/bin/python"
if [[ ! -x "$DEFAULT_PYTHON" && -x "$ROOT/../venv/bin/python" ]]; then
  DEFAULT_PYTHON="$ROOT/../venv/bin/python"
fi
PYTHON="${MEMORY_FORGE_PYTHON:-$DEFAULT_PYTHON}"
API_PORT="${MEMORY_FORGE_API_PORT:-8791}"
WEB_PORT="${MEMORY_FORGE_WEB_PORT:-3000}"

# 优先使用默认路由对应的物理网络地址，避免误选 VPN、Docker 等虚拟网卡。
detect_public_host() {
  local detected=""
  if command -v ip >/dev/null 2>&1; then
    local route_interface=""
    route_interface="$(ip -4 route show table main default 2>/dev/null | awk '{ for (i = 1; i <= NF; i++) if ($i == "dev") { print $(i + 1); exit } }')"
    if [[ -n "$route_interface" ]]; then
      detected="$(ip -4 -o address show dev "$route_interface" scope global 2>/dev/null | awk '{ split($4, address, "/"); print address[1]; exit }')"
    fi
  fi
  if [[ -z "$detected" ]] && command -v hostname >/dev/null 2>&1; then
    detected="$(hostname -I 2>/dev/null | awk '{ print $1 }')"
  fi
  printf '%s' "${detected:-127.0.0.1}"
}

API_HOST="${MEMORY_FORGE_API_HOST:-0.0.0.0}"
WEB_HOST="${MEMORY_FORGE_WEB_HOST:-0.0.0.0}"
PUBLIC_HOST="${MEMORY_FORGE_PUBLIC_HOST:-$(detect_public_host)}"
WEB_ORIGIN="http://$PUBLIC_HOST:$WEB_PORT"
API_ORIGIN="http://$PUBLIC_HOST:$API_PORT"
ALLOWED_ORIGINS="${MEMORY_FORGE_ALLOWED_ORIGINS:-http://127.0.0.1:$WEB_PORT,http://localhost:$WEB_PORT,$WEB_ORIGIN}"

if [[ "$PUBLIC_HOST" == "0.0.0.0" ]]; then
  echo "MEMORY_FORGE_PUBLIC_HOST 不能是 0.0.0.0，请设置浏览器实际访问的 IP。" >&2
  exit 1
fi

if [[ ! -x "$PYTHON" ]]; then
  echo "找不到 Python 环境：$PYTHON" >&2
  exit 1
fi

if [[ ! -d "$ROOT/node_modules" ]]; then
  echo "前端依赖尚未安装，请先在仓库根目录运行：npm install" >&2
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

cd "$ROOT"
echo "Memory Forge 页面（局域网）：$WEB_ORIGIN"
echo "Memory Forge 页面（本机）：http://127.0.0.1:$WEB_PORT"
echo "Memory Forge API：$API_ORIGIN"
MEMORY_FORGE_WEB_HOST="$WEB_HOST" \
  NEXT_PUBLIC_MEMORY_FORGE_API="$API_ORIGIN" \
  npm run dev -- --host "$WEB_HOST" --port "$WEB_PORT"
