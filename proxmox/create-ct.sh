#!/usr/bin/env bash
set -Eeuo pipefail

# Compatibility entry point. The supported Proxmox CT installer now follows
# the community-scripts/ProxmoxVED layout in ct/surroundcore.sh.

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
LOCAL_SCRIPT="$SCRIPT_DIR/../ct/surroundcore.sh"
REMOTE_SCRIPT="https://raw.githubusercontent.com/lurcheous73/SurroundCore/feature/proxmox-ct-install/ct/surroundcore.sh"

if [[ -f "$LOCAL_SCRIPT" ]]; then
  exec bash "$LOCAL_SCRIPT"
fi

command -v curl >/dev/null || { echo "curl is required" >&2; exit 1; }
echo "Using the Community Scripts style SurroundCore Proxmox installer."
exec bash <(curl -fsSL "$REMOTE_SCRIPT")
