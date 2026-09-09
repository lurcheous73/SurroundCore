#!/usr/bin/env bash
set -Eeuo pipefail
CTID=${1:?Usage: $0 CTID [HOST_UID_GID]}
ID=${2:-1000}
CONF="/etc/pve/lxc/$CTID.conf"
[ -f "$CONF" ] || { echo "CT config not found: $CONF" >&2; exit 1; }
grep -q '^unprivileged: 1' "$CONF" || { echo "CT $CTID must be unprivileged" >&2; exit 1; }
if grep -q '^lxc.idmap:' "$CONF"; then
  echo "CT $CTID already has custom idmaps; refusing to overwrite them." >&2
  exit 1
fi
grep -q "^root:${ID}:1$" /etc/subuid || { echo "Add root:${ID}:1 to /etc/subuid first" >&2; exit 1; }
grep -q "^root:${ID}:1$" /etc/subgid || { echo "Add root:${ID}:1 to /etc/subgid first" >&2; exit 1; }

WAS_RUNNING=0
pct status "$CTID" | grep -q running && WAS_RUNNING=1
[ "$WAS_RUNNING" -eq 0 ] || pct shutdown "$CTID" --timeout 20 || pct stop "$CTID"
cat >> "$CONF" <<EOF
lxc.idmap: u 0 100000 ${ID}
lxc.idmap: g 0 100000 ${ID}
lxc.idmap: u ${ID} ${ID} 1
lxc.idmap: g ${ID} ${ID} 1
lxc.idmap: u $((ID+1)) $((100000+ID+1)) $((65536-ID-1))
lxc.idmap: g $((ID+1)) $((100000+ID+1)) $((65536-ID-1))
EOF
[ "$WAS_RUNNING" -eq 0 ] || pct start "$CTID"
echo "CT $CTID now directly maps UID/GID $ID for shared media access."
