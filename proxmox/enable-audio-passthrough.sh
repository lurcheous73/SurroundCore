#!/usr/bin/env bash
set -Eeuo pipefail
CTID=${1:?Usage: $0 CTID}
CONF="/etc/pve/lxc/$CTID.conf"
[ -f "$CONF" ] || { echo "CT config not found: $CONF" >&2; exit 1; }
[ -e /dev/snd ] || { echo "/dev/snd not present on host" >&2; exit 1; }

grep -q '^lxc.cgroup2.devices.allow: c 116:' "$CONF" || \
  echo 'lxc.cgroup2.devices.allow: c 116:* rwm' >> "$CONF"
grep -q '/dev/snd dev/snd' "$CONF" || \
  echo 'lxc.mount.entry: /dev/snd dev/snd none bind,optional,create=dir 0 0' >> "$CONF"

if [ -e /dev/dri ]; then
  grep -q '^lxc.cgroup2.devices.allow: c 226:' "$CONF" || \
    echo 'lxc.cgroup2.devices.allow: c 226:* rwm' >> "$CONF"
  grep -q '/dev/dri dev/dri' "$CONF" || \
    echo 'lxc.mount.entry: /dev/dri dev/dri none bind,optional,create=dir 0 0' >> "$CONF"
fi

echo "Passthrough configured. Restart CT $CTID."
echo "Test with: pct exec $CTID -- aplay -l"
echo "If an unprivileged CT hits device permissions, use the host endpoint agent instead."
