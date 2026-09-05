#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME="memory-forge-live-vpn.service"
SERVICE_SOURCE="$ROOT/scripts/$SERVICE_NAME"
SERVICE_TARGET="/etc/systemd/system/$SERVICE_NAME"
WEB_URL="http://${MEMORY_FORGE_VPN_HOST:-192.168.103.1}:${MEMORY_FORGE_WEB_PORT:-3000}"
API_URL="http://${MEMORY_FORGE_VPN_HOST:-192.168.103.1}:${MEMORY_FORGE_API_PORT:-8791}"

if [[ "$EUID" -ne 0 ]]; then
  echo "请使用 sudo 运行此脚本。" >&2
  exit 1
fi

systemctl stop "$SERVICE_NAME" 2>/dev/null || true

if ss -H -ltn | awk '$4 ~ /:(3000|8791)$/ { found=1 } END { exit !found }'; then
  echo "端口 3000 或 8791 已被其他进程占用，请先停止占用者。" >&2
  ss -H -ltnp | awk '$4 ~ /:(3000|8791)$/' >&2
  exit 1
fi

install -o root -g root -m 0644 "$SERVICE_SOURCE" "$SERVICE_TARGET"
"$ROOT/scripts/allow-live-demo-vpn.sh"

systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"

for _ in $(seq 1 30); do
  if curl --noproxy '*' --fail --silent --max-time 2 "$WEB_URL/" >/dev/null \
    && curl --noproxy '*' --fail --silent --max-time 2 "$API_URL/api/health" >/dev/null; then
    echo "Memory Forge 已启动：$WEB_URL"
    exit 0
  fi
  sleep 1
done

echo "服务未能在 30 秒内就绪，最近日志如下：" >&2
journalctl -u "$SERVICE_NAME" -n 80 --no-pager >&2
exit 1
