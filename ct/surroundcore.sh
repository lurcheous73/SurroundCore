#!/usr/bin/env bash
_CS_DEFAULT_URL="https://raw.githubusercontent.com/lurcheous73/SurroundCore/feature/proxmox-ct-install"
_cs_boot="${COMMUNITY_SCRIPTS_CORE_DIR:-$(dirname "${BASH_SOURCE[0]}")/../../core}/core/build.func"
source "$_cs_boot" 2>/dev/null || source <(curl -fsSL "${COMMUNITY_SCRIPTS_CORE_URL:-https://raw.githubusercontent.com/community-scripts/core/main}/core/build.func")

# Copyright (c) 2026 Christopher Swain
# Author: Kev n Chris
# License: SurroundCore No-Commercial-Exploitation Licence (SC-NCE) 1.2
# Source: https://github.com/lurcheous73/SurroundCore

APP="SurroundCore"
var_tags="${var_tags:-media;audio;music}"
var_cpu="${var_cpu:-4}"
var_ram="${var_ram:-4096}"
var_disk="${var_disk:-16}"
var_os="${var_os:-debian}"
var_version="${var_version:-13}"
var_arm64="${var_arm64:-no}" # Optical/MakeMKV path is currently validated on amd64 only.
var_unprivileged="${var_unprivileged:-0}" # Required for optical SCSI/USB media passthrough.

header_info "$APP"
variables
color
catch_errors

function update_script() {
  header_info
  check_container_storage
  check_container_resources

  if [[ ! -d /opt/surroundcore ]]; then
    msg_error "No ${APP} Installation Found!"
    exit
  fi

  if check_for_gh_branch "surroundcore" "lurcheous73/SurroundCore" "feature/proxmox-ct-install"; then
    msg_info "Stopping SurroundCore"
    systemctl stop surroundcore surroundcore-ingest 2>/dev/null || true
    msg_ok "Stopped SurroundCore"

    create_backup /opt/surroundcore/.env /var/lib/surroundcore
    CLEAN_INSTALL=1 fetch_and_deploy_gh_branch "surroundcore" "lurcheous73/SurroundCore" "feature/proxmox-ct-install" "/opt/surroundcore"
    restore_backup

    msg_info "Updating Python Dependencies"
    /opt/surroundcore/venv/bin/pip install --quiet --upgrade -r /opt/surroundcore/core/requirements.txt -r /opt/surroundcore/ingest/requirements.txt
    msg_ok "Updated Python Dependencies"

    if [[ -d /opt/surroundcore/ingest/netmd ]]; then
      msg_info "Updating NetMD Dependencies"
      cd /opt/surroundcore/ingest/netmd
      $STD npm ci --omit=dev
      msg_ok "Updated NetMD Dependencies"
    fi

    msg_info "Starting SurroundCore"
    systemctl start surroundcore surroundcore-ingest
    msg_ok "Started SurroundCore"
    msg_ok "Updated successfully!"
  fi
  exit
}

start
build_container
description

msg_ok "Completed Successfully!\n"
echo -e "${CREATING}${GN}${APP} setup has been successfully initialized!${CL}"
echo -e "${INFO}${YW}Access it using the following URL:${CL}"
echo -e "${GATEWAY}${BGN}http://${IP}:8080${CL}"
