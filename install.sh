#!/usr/bin/env bash
set -Eeuo pipefail

# Copyright (c) 2026 Christopher Swain
# Author: Kev n Chris
# License: SurroundCore No-Commercial-Exploitation Licence (SC-NCE) 1.2

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root (or with sudo)." >&2
  exit 1
fi

source /etc/os-release
if [[ "${ID:-}" != "debian" || "${VERSION_ID:-}" != "13" ]]; then
  echo "SurroundCore requires Debian 13 (Trixie)." >&2
  echo "Detected: ${PRETTY_NAME:-unknown}" >&2
  exit 1
fi

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

apt update
DEBIAN_FRONTEND=noninteractive apt install -y \
  python3 python3-venv python3-pip \
  ffmpeg flac \
  cd-paranoia cd-discid libcdio-utils lsdvd dvdbackup bchunk \
  util-linux eject sg3-utils usbutils udev \
  xorriso wodim dvd+rw-tools cdrdao 7zip \
  nodejs npm \
  cifs-utils nfs-common rclone fuse3 \
  ca-certificates openssl

if [[ "$SRC_DIR" != "/opt/surroundcore" ]]; then
  rm -rf /opt/surroundcore
  install -d -m 0755 /opt/surroundcore
  cp -a "$SRC_DIR"/. /opt/surroundcore/
fi

install -d -m 0755 \
  /etc/surroundcore \
  /var/lib/surroundcore/ingest \
  /var/cache/surroundcore \
  /srv/surroundcore/media \
  /srv/surroundcore/sources \
  /opt/surroundcore-makemkv \
  /opt/surroundcore-providers/spotify

cd /opt/surroundcore
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install \
  -r core/requirements.txt \
  -r ingest/requirements.txt

if [[ -f ingest/netmd/package-lock.json ]]; then
  (cd ingest/netmd && npm ci --omit=dev)
fi

install -m 0755 storage/surround-storage /usr/local/sbin/surround-storage

grep -q '^user_allow_other$' /etc/fuse.conf 2>/dev/null || echo user_allow_other >>/etc/fuse.conf

ENV=/etc/surroundcore/surroundcore.env
if [[ ! -f "$ENV" ]]; then
  TOKEN="$(openssl rand -hex 32)"
  IP="$(hostname -I | awk '{print $1}')"
  umask 077
  cat <<EOF >"$ENV"
SURROUNDCORE_MEDIA=/srv/surroundcore/media
SURROUNDCORE_DATA=/var/lib/surroundcore
SURROUNDCORE_SOURCES=/srv/surroundcore/sources
SURROUNDCORE_SOURCE_CACHE=/var/cache/surroundcore
SURROUNDCORE_CACHE_MAX_GB=0
SURROUNDCORE_CACHE_MIN_FREE_GB=5
SURROUNDCORE_TOKEN=${TOKEN}
SURROUNDCORE_PUBLIC_URL=http://${IP}:8080
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
  chmod 600 "$ENV"
fi

cat <<'EOF' >/etc/systemd/system/surroundcore.service
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

cat <<'EOF' >/etc/systemd/system/surroundcore-ingest.service
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

systemctl daemon-reload
systemctl enable --now surroundcore.service surroundcore-ingest.service

sleep 2
systemctl is-active --quiet surroundcore.service || {
  echo "SurroundCore Core failed to start." >&2
  systemctl --no-pager --full status surroundcore.service || true
  exit 1
}
systemctl is-active --quiet surroundcore-ingest.service || {
  echo "SurroundCore ingest failed to start." >&2
  systemctl --no-pager --full status surroundcore-ingest.service || true
  exit 1
}

IP="$(hostname -I | awk '{print $1}')"
echo
echo "SurroundCore installed successfully."
echo "Core/API:  http://${IP}:8080"
echo "API docs:  http://${IP}:8080/docs"
echo "Config:    /etc/surroundcore/surroundcore.env"
echo "Music:     /srv/surroundcore/media"
echo
echo "For a playback-only machine use the endpoint branch."
echo "For Proxmox VE use the privileged proxmox-ct branch."
