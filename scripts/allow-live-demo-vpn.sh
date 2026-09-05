#!/usr/bin/env bash
set -euo pipefail

VPN_INTERFACE="${MEMORY_FORGE_VPN_INTERFACE:-tun0}"
VPN_NETWORK="${MEMORY_FORGE_VPN_NETWORK:-192.168.103.0/24}"
VPN_HOST="${MEMORY_FORGE_VPN_HOST:-192.168.103.1}"
WEB_PORT="${MEMORY_FORGE_WEB_PORT:-3000}"
API_PORT="${MEMORY_FORGE_API_PORT:-8791}"

if [[ "$EUID" -ne 0 ]]; then
  echo "请使用 sudo 运行此脚本。" >&2
  exit 1
fi

if ! command -v ufw >/dev/null 2>&1; then
  echo "服务器未安装 UFW，未修改防火墙。" >&2
  exit 1
fi

ufw allow in on "$VPN_INTERFACE" from "$VPN_NETWORK" to "$VPN_HOST" port "$WEB_PORT" proto tcp comment "Memory Forge web over VPN"
ufw allow in on "$VPN_INTERFACE" from "$VPN_NETWORK" to "$VPN_HOST" port "$API_PORT" proto tcp comment "Memory Forge API over VPN"
ufw status numbered
