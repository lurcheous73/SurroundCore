#!/usr/bin/env bash
_CS_DEFAULT_URL="https://raw.githubusercontent.com/lurcheous73/SurroundCore/feature/proxmox-ct-install"
_cs_boot="${COMMUNITY_SCRIPTS_CORE_DIR:-$(dirname "${BASH_SOURCE[0]}")/../../core}/core/build.func"
source "$_cs_boot" 2>/dev/null || source <(curl -fsSL "${COMMUNITY_SCRIPTS_CORE_URL:-https://raw.githubusercontent.com/community-scripts/core/main}/core/build.func")

# Copyright (c) 2026 SurroundCore contributors
# Author: lurcheous73
# License: SurroundCore application licence to be finalised; Community Scripts contribution files may be licensed separately.
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

# Permit native optical filesystem access when the host exposes a drive.
ALLOW_MOUNT_FS="${ALLOW_MOUNT_FS:-udf;iso9660}"

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

  UV_PYTHON="3.13" setup_uv
  NODE_VERSION="22" setup_nodejs
  setup_ffmpeg

  if check_for_gh_branch "surroundcore" "lurcheous73/SurroundCore" "feature/proxmox-ct-install"; then
    msg_info "Stopping SurroundCore"
    systemctl stop surroundcore-ingest surroundcore 2>/dev/null || true
    msg_ok "Stopped SurroundCore"

    create_backup /etc/surroundcore/surroundcore.env /var/lib/surroundcore

    CLEAN_INSTALL=1 fetch_and_deploy_gh_branch \
      "surroundcore" \
      "lurcheous73/SurroundCore" \
      "feature/proxmox-ct-install" \
      "/opt/surroundcore"

    restore_backup

    msg_info "Updating Python Dependencies"
    cd /opt/surroundcore
    $STD uv venv --python 3.13 .venv
    $STD uv pip install --python .venv/bin/python \
      -r core/requirements.txt \
      -r ingest/requirements.txt
    msg_ok "Updated Python Dependencies"

    if [[ -f /opt/surroundcore/ingest/netmd/package-lock.json ]]; then
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

msg_ok "Completed successfully!\n"
echo -e "${CREATING}${GN}${APP} setup has been successfully initialized!${CL}"
echo -e "${INFO}${YW}Access it using the following URL:${CL}"
echo -e "${GATEWAY}${BGN}http://${IP}:8080${CL}"
echo -e "${INFO}${YW}Optical and removable media are attached by the SurroundCore Proxmox media broker.${CL}"
