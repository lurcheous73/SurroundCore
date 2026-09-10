#!/usr/bin/env bash
set -Eeuo pipefail

CTID=${1:?Usage: $0 CTID HOST_MEDIA_PATH [CT_PATH] [rw|ro]}
HOST_PATH=${2:?Usage: $0 CTID HOST_MEDIA_PATH [CT_PATH] [rw|ro]}
CT_PATH=${3:-/srv/surroundcore/media}
MODE=${4:-rw}

[[ "$MODE" == "rw" || "$MODE" == "ro" ]] || { echo "Mode must be rw or ro" >&2; exit 1; }
[[ -d "$HOST_PATH" ]] || { echo "Host media path not found: $HOST_PATH" >&2; exit 1; }
command -v pct >/dev/null || { echo "Run on a Proxmox VE host" >&2; exit 1; }

for slot in $(seq 0 9); do
  if ! pct config "$CTID" | grep -q "^mp${slot}:"; then
    mount_spec="$HOST_PATH,mp=$CT_PATH"
    [[ "$MODE" == "ro" ]] && mount_spec+=",ro=1"
    pct set "$CTID" -mp${slot} "$mount_spec"
    echo "Mounted $HOST_PATH $MODE at $CT_PATH in CT $CTID (mp${slot})."
    exit 0
  fi
done

echo "No free mp0..mp9 mount slot on CT $CTID" >&2
exit 1
