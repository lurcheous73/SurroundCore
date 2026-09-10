#!/usr/bin/env bash

# Copyright (c) 2026 Christopher Swain
# Author: Kev n Chris
# License: SurroundCore No-Commercial-Exploitation Licence (SC-NCE) 1.2
# Source: https://github.com/lurcheous73/SurroundCore

source /dev/stdin <<<"$FUNCTIONS_FILE_PATH"
color
verb_ip6
catch_errors
setting_up_container
network_check
update_os

msg_info "Installing Media Dependencies"
$STD apt install -y \
  flac \
  cd-paranoia \
  cd-discid \
  libcdio-utils \
  lsdvd \
  dvdbackup \
  bchunk \
  util-linux \
  eject \
  sg3-utils \
  usbutils \
  udev \
  xorriso \
  wodim \
  dvd+rw-tools \
  cdrdao \
  7zip \
  build-essential \
  libusb-1.0-0-dev \
  openssl
msg_ok "Installed Media Dependencies"

setup_ffmpeg
NODE_VERSION="22" setup_nodejs
UV_PYTHON="3.13" setup_uv

fetch_and_deploy_gh_branch \
  "surroundcore" \
  "lurcheous73/SurroundCore" \
  "proxmox-ct" \
  "/opt/surroundcore"

msg_info "Installing Python Dependencies"
cd /opt/surroundcore
$STD uv venv --python 3.13 .venv
$STD uv pip install --python .venv/bin/python \
  -r core/requirements.txt \
  -r ingest/requirements.txt
msg_ok "Installed Python Dependencies"

if [[ -f /opt/surroundcore/ingest/netmd/package-lock.json ]]; then
  msg_info "Installing NetMD Dependencies"
  cd /opt/surroundcore/ingest/netmd
  $STD npm ci --omit=dev
  msg_ok "Installed NetMD Dependencies"
fi

msg_info "Creating SurroundCore Directories"
mkdir -p \
  /etc/surroundcore \
  /var/lib/surroundcore/ingest \
  /var/cache/surroundcore \
  /srv/surroundcore/media \
  /srv/surroundcore/sources \
  /opt/surroundcore-makemkv \
  /opt/surroundcore-providers/spotify
msg_ok "Created SurroundCore Directories"

msg_info "Creating SurroundCore Configuration"
umask 077
cat <<EOF >/etc/surroundcore/surroundcore.env
SURROUNDCORE_MEDIA=/srv/surroundcore/media
SURROUNDCORE_DATA=/var/lib/surroundcore
SURROUNDCORE_SOURCES=/srv/surroundcore/sources
SURROUNDCORE_SOURCE_CACHE=/var/cache/surroundcore
SURROUNDCORE_CACHE_MAX_GB=0
SURROUNDCORE_CACHE_MIN_FREE_GB=5
SURROUNDCORE_TOKEN=$(openssl rand -hex 32)
SURROUNDCORE_PUBLIC_URL=http://${LOCAL_IP}:8080
SURROUNDCORE_CORE_URL=http://127.0.0.1:8080
SURROUNDCORE_INGEST_URL=http://127.0.0.1:8082
SURROUNDCORE_INGEST_ROOT=/var/lib/surroundcore/ingest
SURROUNDCORE_INGEST_LIBRARY=/srv/surroundcore/media
SURROUNDCORE_AUTO_RIP=true
SURROUNDCORE_AUTO_NETMD=true
SURROUNDCORE_INGEST_EJECT=true
SURROUNDCORE_INGEST_KEEP_UPLOADS=false
SURROUNDCORE_INGEST_POLL_SECONDS=8
SURROUNDCORE_INGEST_MAX_UPLOAD_GB=0
SURROUNDCORE_MAKEMKVCON=/opt/surroundcore-makemkv/makemkvcon
SURROUNDCORE_SPOTIFY_SOLOIST=/opt/surroundcore-providers/spotify/soloist
EOF
chmod 600 /etc/surroundcore/surroundcore.env
msg_ok "Created SurroundCore Configuration"

msg_info "Creating SurroundCore Service"
cat <<EOF >/etc/systemd/system/surroundcore.service
[Unit]
Description=SurroundCore Music Core
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/surroundcore/core
EnvironmentFile=/etc/surroundcore/surroundcore.env
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/surroundcore/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8080
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
systemctl enable -q --now surroundcore
msg_ok "Created SurroundCore Service"

msg_info "Creating SurroundCore Ingest Service"
cat <<EOF >/etc/systemd/system/surroundcore-ingest.service
[Unit]
Description=SurroundCore Optical and Media Ingest
After=network-online.target systemd-udevd.service surroundcore.service
Wants=network-online.target surroundcore.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/surroundcore/ingest
EnvironmentFile=/etc/surroundcore/surroundcore.env
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/surroundcore/.venv/bin/uvicorn ingest_app.main:app --host 127.0.0.1 --port 8082
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
systemctl enable -q --now surroundcore-ingest
msg_ok "Created SurroundCore Ingest Service"

msg_info "Checking SurroundCore Services"
sleep 2
systemctl is-active --quiet surroundcore || { msg_error "SurroundCore Core failed to start"; exit 1; }
systemctl is-active --quiet surroundcore-ingest || { msg_error "SurroundCore Ingest failed to start"; exit 1; }
msg_ok "SurroundCore Services Are Running"

motd_ssh
customize
cleanup_lxc
