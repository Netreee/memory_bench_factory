#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VPN_INTERFACE="${MEMORY_FORGE_VPN_INTERFACE:-tun0}"
VPN_HOST="${MEMORY_FORGE_VPN_HOST:-192.168.103.1}"

if ! ip -o -4 addr show dev "$VPN_INTERFACE" | grep -Fq "inet $VPN_HOST/"; then
  echo "接口 $VPN_INTERFACE 上不存在地址 $VPN_HOST，请先连接服务器 VPN。" >&2
  exit 1
fi

export MEMORY_FORGE_API_HOST="$VPN_HOST"
export MEMORY_FORGE_WEB_HOST="$VPN_HOST"
export MEMORY_FORGE_PUBLIC_HOST="$VPN_HOST"

exec "$ROOT/scripts/live-demo.sh"
