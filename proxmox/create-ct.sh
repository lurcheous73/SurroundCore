#!/usr/bin/env bash
set -Eeuo pipefail
CTID=${CTID:-240}
HOSTNAME=${HOSTNAME:-surroundcore}
STORAGE=${STORAGE:-local-lvm}
MEMORY=${MEMORY:-4096}
CORES=${CORES:-4}
DISK=${DISK:-16}
BRIDGE=${BRIDGE:-vmbr0}
IPCFG=${IPCFG:-ip=dhcp}

command -v pct >/dev/null || { echo "Run on a Proxmox VE host" >&2; exit 1; }
pveam update >/dev/null
TPL=$(pveam available --section system | awk '/debian-13-standard/ {print $2; exit}')
[ -n "$TPL" ] || { echo "Debian 13 standard template not found" >&2; exit 1; }
CACHE="local:vztmpl/${TPL##*/}"
pveam download local "${TPL##*/}" || true

pct create "$CTID" "$CACHE" \
  --hostname "$HOSTNAME" --cores "$CORES" --memory "$MEMORY" \
  --rootfs "$STORAGE:$DISK" \
  --net0 "name=eth0,bridge=$BRIDGE,$IPCFG" \
  --features nesting=1,keyctl=1 --unprivileged 1 --start 1

echo "Debian 13 CT $CTID created."
echo "Clone SurroundCore inside it and run ./install.sh."
echo "For host HDMI, see proxmox/enable-audio-passthrough.sh."
