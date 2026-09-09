#!/usr/bin/env bash
set -Eeuo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root" >&2
  exit 1
fi

. /etc/os-release
if [ "${ID:-}" != "debian" ] || [ "${VERSION_ID:-}" != "13" ]; then
  echo "SurroundCore endpoint requires Debian 13 (Trixie)." >&2
  exit 1
fi

: "${CORE_URL:?Set CORE_URL, e.g. http://10.26.30.20:8080}"
: "${TOKEN:?Set TOKEN to the SurroundCore shared token}"
NAME=${NAME:-$(hostname)}
DEVICE=${DEVICE:-default}

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y ffmpeg alsa-utils python3 ca-certificates
install -d -m 0755 /opt/surroundcore /usr/local/lib/surroundcore
install -m 0755 endpoint/surround-agent.py /usr/local/lib/surroundcore/surround-agent.py
install -m 0644 endpoint/surround-agent.service /etc/systemd/system/surround-agent.service

cat > /opt/surroundcore/.env <<EOF
SURROUNDCORE_TOKEN=${TOKEN}
SURROUNDCORE_CORE_URL=${CORE_URL}
SURROUNDCORE_ENDPOINT_NAME="${NAME}"
SURROUNDCORE_DEFAULT_DEVICE=${DEVICE}
EOF

systemctl daemon-reload
if [ -e /dev/snd ]; then
  systemctl enable --now surround-agent.service
  echo "Endpoint enabled."
  aplay -l || true
else
  echo "No /dev/snd found; endpoint installed but not enabled." >&2
fi

echo "Capabilities: http://$(hostname -I | awk '{print $1}'):8090/v1/capabilities"
