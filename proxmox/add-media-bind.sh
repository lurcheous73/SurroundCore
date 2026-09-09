#!/usr/bin/env bash
set -Eeuo pipefail
CTID=${1:?Usage: $0 CTID HOST_MEDIA_PATH [CT_PATH]}
HOST_PATH=${2:?Usage: $0 CTID HOST_MEDIA_PATH [CT_PATH]}
CT_PATH=${3:-/srv/surroundcore/media}
[ -d "$HOST_PATH" ] || { echo "Host media path not found: $HOST_PATH" >&2; exit 1; }
command -v pct >/dev/null || { echo "Run on a Proxmox VE host" >&2; exit 1; }

for slot in $(seq 0 9); do
  if ! pct config "$CTID" | grep -q "^mp${slot}:"; then
    pct set "$CTID" -mp${slot} "$HOST_PATH,mp=$CT_PATH,ro=1"
    echo "Mounted $HOST_PATH read-only at $CT_PATH in CT $CTID (mp${slot})."
    exit 0
  fi
done

echo "No free mp0..mp9 mount slot on CT $CTID" >&2
exit 1
