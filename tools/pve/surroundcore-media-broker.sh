#!/usr/bin/env bash
set -Eeuo pipefail

# Copyright (c) 2026 SurroundCore contributors
# Author: lurcheous73
# Source: https://github.com/lurcheous73/SurroundCore
#
# Proxmox-host helper for safe removable-media discovery and live LXC device
# passthrough. The SurroundCore web decision API is intentionally a separate
# layer: this tool never exposes an unsafe host storage device merely because
# it is connected over USB.

usage() {
  cat <<'EOF'
Usage:
  surroundcore-media-broker.sh scan
  surroundcore-media-broker.sh attach CTID /dev/DEVICE
  surroundcore-media-broker.sh detach CTID /dev/DEVICE
  surroundcore-media-broker.sh inspect /dev/DEVICE

The target LXC must be privileged. `attach` is live-only: unplugged devices do
not become stale permanent `devN` entries in the Proxmox configuration.
EOF
}

require_pve() {
  command -v pct >/dev/null || { echo "Run this on a Proxmox VE host" >&2; exit 1; }
  command -v lxc-device >/dev/null || { echo "lxc-device is required" >&2; exit 1; }
}

require_privileged_ct() {
  local ctid=$1
  pct status "$ctid" >/dev/null 2>&1 || { echo "Unknown CT $ctid" >&2; exit 1; }
  if pct config "$ctid" | grep -q '^unprivileged: 1'; then
    echo "CT $ctid is unprivileged; SurroundCore media passthrough requires a privileged LXC" >&2
    exit 1
  fi
}

block_tree() {
  lsblk -nrpo NAME "$1" 2>/dev/null || true
}

is_active_zfs_device() {
  command -v zpool >/dev/null || return 1
  zpool status -P 2>/dev/null | grep -Fqw -- "$1"
}

is_active_lvm_device() {
  command -v pvs >/dev/null || return 1
  pvs --noheadings -o pv_name 2>/dev/null | awk '{$1=$1};1' | grep -Fxq -- "$1"
}

unsafe_reason() {
  local dev=$1 node fstype mounts
  [[ -b "$dev" ]] || { echo "not a block device"; return 0; }

  while read -r node; do
    [[ -n "$node" ]] || continue
    mounts=$(lsblk -ndo MOUNTPOINTS "$node" 2>/dev/null | tr -d '[:space:]')
    [[ -z "$mounts" ]] || { echo "$node is mounted on the Proxmox host"; return 0; }

    fstype=$(lsblk -ndo FSTYPE "$node" 2>/dev/null | xargs)
    case "$fstype" in
      zfs_member) echo "$node is a ZFS member"; return 0 ;;
      LVM2_member) echo "$node is an LVM physical volume"; return 0 ;;
    esac

    is_active_zfs_device "$node" && { echo "$node is in an active ZFS pool"; return 0; }
    is_active_lvm_device "$node" && { echo "$node is in active LVM storage"; return 0; }
  done < <(block_tree "$dev")

  return 1
}

eligible_device() {
  local dev=$1 type tran hotplug
  [[ -b "$dev" ]] || return 1
  type=$(lsblk -ndo TYPE "$dev" 2>/dev/null | xargs)
  tran=$(lsblk -ndo TRAN "$dev" 2>/dev/null | xargs)
  hotplug=$(lsblk -ndo HOTPLUG "$dev" 2>/dev/null | xargs)

  case "$type" in
    rom) [[ "$tran" == "usb" || "$hotplug" == "1" ]] || return 1 ;;
    disk) [[ "$tran" == "usb" && "$hotplug" == "1" ]] || return 1 ;;
    *) return 1 ;;
  esac

  [[ -z "$(unsafe_reason "$dev" || true)" ]]
}

matching_sg() {
  local dev=$1 sys sg
  sys=$(readlink -f "/sys/class/block/$(basename "$dev")/device" 2>/dev/null || true)
  [[ -n "$sys" && -d "$sys/scsi_generic" ]] || return 0
  sg=$(find "$sys/scsi_generic" -mindepth 1 -maxdepth 1 -printf '%f\n' 2>/dev/null | head -n1)
  [[ -n "$sg" && -e "/dev/$sg" ]] && echo "/dev/$sg"
}

usb_device_node() {
  local dev=$1 sys busnum devnum
  sys="/sys$(udevadm info --query=path --name "$dev" 2>/dev/null || true)"
  while [[ "$sys" == /sys/* ]]; do
    if [[ -r "$sys/busnum" && -r "$sys/devnum" ]]; then
      busnum=$(<"$sys/busnum")
      devnum=$(<"$sys/devnum")
      printf '/dev/bus/usb/%03d/%03d\n' "$busnum" "$devnum"
      return 0
    fi
    [[ "$sys" == /sys ]] && break
    sys=$(dirname "$sys")
  done
}

inspect_device() {
  local dev=$1 reason
  reason=$(unsafe_reason "$dev" || true)
  printf 'device=%s\n' "$dev"
  lsblk -ndo 'TYPE,TRAN,RM,HOTPLUG,SIZE,FSTYPE,LABEL,VENDOR,MODEL,SERIAL' "$dev" 2>/dev/null || true
  if [[ -n "$reason" ]]; then
    printf 'eligible=no\nreason=%s\n' "$reason"
  elif eligible_device "$dev"; then
    printf 'eligible=yes\n'
  else
    printf 'eligible=no\nreason=not removable media eligible for SurroundCore\n'
  fi
}

scan_devices() {
  local sys dev
  for sys in /sys/class/block/*; do
    dev="/dev/$(basename "$sys")"
    eligible_device "$dev" || continue
    inspect_device "$dev"
    echo '---'
  done
}

nodes_for_device() {
  local dev=$1 type sg usb
  type=$(lsblk -ndo TYPE "$dev" 2>/dev/null | xargs)
  if [[ "$type" == "rom" ]]; then
    echo "$dev"
    sg=$(matching_sg "$dev")
    [[ -n "$sg" ]] && echo "$sg"
    usb=$(usb_device_node "$dev")
    [[ -n "$usb" && -e "$usb" ]] && echo "$usb"
  else
    block_tree "$dev"
  fi
}

attach_device() {
  local ctid=$1 dev=$2 reason node
  require_privileged_ct "$ctid"
  [[ "$(pct status "$ctid")" == "status: running" ]] || { echo "CT $ctid must be running" >&2; exit 1; }
  reason=$(unsafe_reason "$dev" || true)
  [[ -z "$reason" ]] || { echo "Refusing $dev: $reason" >&2; exit 1; }
  eligible_device "$dev" || { echo "Refusing $dev: device is not eligible removable media" >&2; exit 1; }

  while read -r node; do
    [[ -e "$node" ]] || continue
    if pct exec "$ctid" -- test -e "$node" 2>/dev/null; then
      echo "$node already visible in CT $ctid"
      continue
    fi
    lxc-device -n "$ctid" add "$node"
    echo "Attached $node to CT $ctid"
  done < <(nodes_for_device "$dev")
}

detach_device() {
  local ctid=$1 dev=$2 node
  require_privileged_ct "$ctid"
  while read -r node; do
    pct exec "$ctid" -- test -e "$node" 2>/dev/null || continue
    lxc-device -n "$ctid" del "$node" 2>/dev/null || true
    echo "Detached $node from CT $ctid"
  done < <(nodes_for_device "$dev")
}

require_pve
case "${1:-}" in
  scan) scan_devices ;;
  inspect)
    [[ $# -eq 2 ]] || { usage; exit 64; }
    inspect_device "$2"
    ;;
  attach)
    [[ $# -eq 3 ]] || { usage; exit 64; }
    attach_device "$2" "$3"
    ;;
  detach)
    [[ $# -eq 3 ]] || { usage; exit 64; }
    detach_device "$2" "$3"
    ;;
  *) usage; exit 64 ;;
esac
