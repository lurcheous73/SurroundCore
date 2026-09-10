#!/usr/bin/env bash
set -Eeuo pipefail

# Copyright (c) 2026 Christopher Swain
# Author: Kev n Chris
# License: SurroundCore No-Commercial-Exploitation Licence (SC-NCE) 1.2

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root (or with sudo)." >&2
  exit 1
fi

source /etc/os-release
if [[ "${ID:-}" != "debian" || "${VERSION_ID:-}" != "13" ]]; then
  echo "SurroundCore endpoint requires Debian 13 (Trixie)." >&2
  echo "Detected: ${PRETTY_NAME:-unknown}" >&2
  exit 1
fi

: "${CORE_URL:?Set CORE_URL, for example http://192.168.1.50:8080}"
: "${TOKEN:?Set TOKEN to the SurroundCore shared token}"

NAME="${NAME:-$(hostname)}"
DEVICE="${DEVICE:-default}"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

apt update
DEBIAN_FRONTEND=noninteractive apt install -y \
  python3 \
  ffmpeg \
  alsa-utils \
  ca-certificates

install -d -m 0755 /etc/surroundcore /usr/local/lib/surroundcore
install -m 0755 "$SRC_DIR/endpoint/surround-agent.py" /usr/local/lib/surroundcore/surround-agent.py
install -m 0644 "$SRC_DIR/endpoint/surround-agent.service" /etc/systemd/system/surround-agent.service

umask 077
cat <<EOF >/etc/surroundcore/endpoint.env
SURROUNDCORE_TOKEN=${TOKEN}
SURROUNDCORE_CORE_URL=${CORE_URL}
SURROUNDCORE_ENDPOINT_NAME=${NAME}
SURROUNDCORE_DEFAULT_DEVICE=${DEVICE}
EOF
chmod 600 /etc/surroundcore/endpoint.env

systemctl daemon-reload

if [[ -e /dev/snd ]]; then
  systemctl enable --now surround-agent.service
  sleep 1
  if ! systemctl is-active --quiet surround-agent.service; then
    echo "Endpoint service failed to start." >&2
    systemctl --no-pager --full status surround-agent.service || true
    exit 1
  fi
else
  systemctl disable surround-agent.service 2>/dev/null || true
  echo "No /dev/snd found; endpoint installed but not started." >&2
fi

IP="$(hostname -I | awk '{print $1}')"
echo
echo "SurroundCore endpoint installed."
echo "Endpoint name: ${NAME}"
echo "Core:          ${CORE_URL}"
echo "ALSA device:   ${DEVICE}"
echo "Capabilities:  http://${IP}:8090/v1/capabilities"
echo
aplay -l 2>/dev/null || true
