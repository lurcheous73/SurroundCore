#!/usr/bin/env bash
set -Eeuo pipefail
CTID=${1:?Usage: $0 CTID [CORE_URL]}
ROOTFS=$(pct config "$CTID" | awk -F'[:,]' '/^rootfs:/ {print $2; exit}')
CORE_URL=${2:-}
[ -n "$CORE_URL" ] || CORE_URL="http://$(pct exec "$CTID" -- hostname -I | awk '{print $1}'):8080"
SRC=${SURROUNDCORE_SOURCE:-"/DATA/NVME-512GB/pve-ct/subvol-${CTID}-disk-0/root/SurroundCore"}
[ -f "$SRC/endpoint/surround-bluetooth-agent.py" ] || { echo "SurroundCore source not found: $SRC" >&2; exit 1; }

DEBIAN_FRONTEND=noninteractive apt-get install -y bluez bluez-alsa-utils alsa-utils ffmpeg ca-certificates >/dev/null
systemctl enable --now bluetooth.service bluealsa.service
install -d -m 0755 /usr/local/lib/surroundcore /etc/surroundcore
install -m 0755 "$SRC/endpoint/surround-bluetooth-agent.py" /usr/local/lib/surroundcore/
install -m 0644 "$SRC/endpoint/surround-bluetooth-agent.service" /etc/systemd/system/
TOKEN=$(pct exec "$CTID" -- bash -lc "set -a; source /opt/surroundcore/.env; printf '%s' \"\$SURROUNDCORE_TOKEN\"")
[ -n "$TOKEN" ] || { echo "Core token not found in CT $CTID" >&2; exit 1; }
HOST_IP=""
for candidate in $(hostname -I); do
  if pct exec "$CTID" -- bash -lc "timeout 1 bash -c '</dev/tcp/$candidate/8092' >/dev/null 2>&1"; then
    HOST_IP="$candidate"; break
  fi
done
[ -n "$HOST_IP" ] || HOST_IP=$(hostname -I | awk '{print $1}')
cat > /etc/surroundcore/bluetooth-agent.env <<ENV
SURROUNDCORE_TOKEN=$TOKEN
SURROUNDCORE_CORE_URL=$CORE_URL
SURROUNDCORE_BLUETOOTH_ADVERTISE=http://$HOST_IP:8092
SURROUNDCORE_BLUETOOTH_PORT=8092
SURROUNDCORE_CTID=$CTID
ENV
chmod 0600 /etc/surroundcore/bluetooth-agent.env
# Tell Core where the sanitized host hardware helper lives. Keep the value in the CT env only.
pct exec "$CTID" -- bash -lc "python3 - <<'PY2'
from pathlib import Path
p=Path('/opt/surroundcore/.env')
s=p.read_text() if p.exists() else ''
key='SURROUNDCORE_HARDWARE_URL'
value='http://$HOST_IP:8092'
lines=[x for x in s.splitlines() if not x.startswith(key+'=')]
lines.append(key+'='+value)
p.write_text('\n'.join(lines)+'\n')
PY2"
systemctl daemon-reload
systemctl enable surround-bluetooth-agent.service >/dev/null
systemctl restart surround-bluetooth-agent.service
# Raw HCI passthrough into the CT is intentionally unnecessary: host BlueZ owns the controller.
CONF="/etc/pve/lxc/$CTID.conf"
sed -i '/path=\/dev\/surroundcore-bt/d' "$CONF"
echo "Bluetooth host agent installed for CT $CTID at http://$HOST_IP:8092"
