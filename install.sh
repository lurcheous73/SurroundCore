#!/usr/bin/env bash
set -Eeuo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root" >&2
  exit 1
fi

. /etc/os-release
if [ "${ID:-}" != "debian" ] || [ "${VERSION_ID:-}" != "13" ]; then
  echo "SurroundCore requires Debian 13 (Trixie)." >&2
  echo "Detected: ${PRETTY_NAME:-unknown}" >&2
  exit 1
fi

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  docker.io docker-compose ffmpeg alsa-utils curl ca-certificates \
  cifs-utils nfs-common python3
systemctl enable --now docker

install -d -m 0755 /opt/surroundcore /var/lib/surroundcore \
  /srv/surroundcore/media /usr/local/lib/surroundcore
cp -a core docker-compose.yml .env.example /opt/surroundcore/
cp endpoint/surround-agent.py /usr/local/lib/surroundcore/
cp endpoint/surround-agent.service /etc/systemd/system/
chmod 0755 /usr/local/lib/surroundcore/surround-agent.py

ENV=/opt/surroundcore/.env
if [ ! -f "$ENV" ]; then
  cp /opt/surroundcore/.env.example "$ENV"
fi

if grep -q '^SURROUNDCORE_TOKEN=change-me$' "$ENV"; then
  TOKEN=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
  sed -i "s/^SURROUNDCORE_TOKEN=.*/SURROUNDCORE_TOKEN=$TOKEN/" "$ENV"
fi

cd /opt/surroundcore
docker compose up -d --build

systemctl daemon-reload
if [ -e /dev/snd ]; then
  systemctl enable --now surround-agent.service
else
  echo "No /dev/snd: Core installed; endpoint agent left disabled."
fi

IP=$(hostname -I | awk '{print $1}')
echo "SurroundCore API: http://${IP}:8080/docs"
echo "Endpoint API:     http://${IP}:8090/v1/capabilities (when audio exists)"
