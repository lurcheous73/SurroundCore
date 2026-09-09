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
TAG=${TAG:-}
MTU=${MTU:-1500}
NAMESERVER=${NAMESERVER:-}
TIMEZONE=${TIMEZONE:-host}

command -v pct >/dev/null || { echo "Run on a Proxmox VE host" >&2; exit 1; }
pveam update >/dev/null
TPL=$(pveam available --section system | awk '/debian-13-standard/ {print $2; exit}')
[ -n "$TPL" ] || { echo "Debian 13 standard template not found" >&2; exit 1; }
CACHE="local:vztmpl/${TPL##*/}"
pveam download local "${TPL##*/}" || true

NET="name=eth0,bridge=$BRIDGE,$IPCFG,mtu=$MTU"
[ -n "$TAG" ] && NET="$NET,tag=$TAG"
ARGS=(--hostname "$HOSTNAME" --cores "$CORES" --memory "$MEMORY" --rootfs "$STORAGE:$DISK" --net0 "$NET" --features nesting=1,keyctl=1 --unprivileged 1 --onboot 1 --timezone "$TIMEZONE" --start 1)
[ -n "$NAMESERVER" ] && ARGS+=(--nameserver "$NAMESERVER")

pct create "$CTID" "$CACHE" "${ARGS[@]}"
if [ -n "$NAMESERVER" ] && [[ "$IPCFG" == *"ip=dhcp"* ]]; then
  pct exec "$CTID" -- bash -lc "grep -q 'supersede domain-name-servers $NAMESERVER' /etc/dhcp/dhclient.conf || printf '\n# SurroundCore resolver\nsupersede domain-name-servers $NAMESERVER;\n' >> /etc/dhcp/dhclient.conf"
  pct reboot "$CTID"
fi
echo "Debian 13 CT $CTID created."
echo "Network: $NET"
[ -n "$NAMESERVER" ] && echo "DNS: $NAMESERVER"
echo "Clone SurroundCore inside it and run ./install.sh."
echo "For shared datasets, see enable-media-idmap.sh and add-media-bind.sh."
echo "For host HDMI, see enable-audio-passthrough.sh."
