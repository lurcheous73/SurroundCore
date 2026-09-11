#!/usr/bin/env bash
set -Eeuo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Run as root" >&2; exit 1; }
SRC=${SURROUNDCORE_SOURCE:-$(cd "$(dirname "$0")/.." && pwd)}
ENV_SRC=${SURROUNDCORE_ENV:-/opt/surroundcore/.env}
[ -f "$ENV_SRC" ] || { echo "SurroundCore environment not found: $ENV_SRC" >&2; exit 1; }
DEBIAN_FRONTEND=noninteractive apt-get install -y bluez bluez-alsa-utils alsa-utils ffmpeg ca-certificates >/dev/null
systemctl enable --now bluetooth.service bluealsa.service
install -d -m 0755 /usr/local/lib/surroundcore /etc/surroundcore
install -m 0755 "$SRC/endpoint/surround-bluetooth-agent.py" /usr/local/lib/surroundcore/
install -m 0644 "$SRC/endpoint/surround-bluetooth-agent.service" /etc/systemd/system/
TOKEN=$(bash -lc "set -a; source '$ENV_SRC'; printf '%s' \"\$SURROUNDCORE_TOKEN\"")
CORE_URL=$(bash -lc "set -a; source '$ENV_SRC'; printf '%s' \"\${SURROUNDCORE_CORE_URL:-http://127.0.0.1:8080}\"")
HOST_IP=$(hostname -I | awk '{print $1}')
cat > /etc/surroundcore/bluetooth-agent.env <<ENV
SURROUNDCORE_TOKEN=$TOKEN
SURROUNDCORE_CORE_URL=$CORE_URL
SURROUNDCORE_BLUETOOTH_ADVERTISE=http://$HOST_IP:8092
SURROUNDCORE_BLUETOOTH_PORT=8092
ENV
chmod 0600 /etc/surroundcore/bluetooth-agent.env
systemctl daemon-reload
systemctl enable --now surround-bluetooth-agent.service
echo "Bluetooth endpoint agent installed at http://$HOST_IP:8092"
