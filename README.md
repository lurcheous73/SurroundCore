# SurroundCore — Endpoint

**SurroundCore — created by Kev n Chris.**

This branch is the lightweight **playback-only SurroundCore endpoint** for Debian 13 machines such as Raspberry Pi, NUC, small PCs and remote audio boxes.

It does **not** install the SurroundCore Core, database, storage layer, streaming-provider configuration or optical ingest service. It only exposes local ALSA/HDMI/USB audio hardware to an existing SurroundCore Core.

## Requirements

- Debian 13 (Trixie)
- amd64 or arm64
- working ALSA audio hardware
- network access to an existing SurroundCore Core
- the Core URL and shared SurroundCore token

## Install

```bash
git clone --branch endpoint https://github.com/lurcheous73/SurroundCore.git
cd SurroundCore
sudo CORE_URL=http://CORE-IP:8080 TOKEN=YOUR_TOKEN ./install-endpoint.sh
```

Optional values:

```bash
sudo \
  CORE_URL=http://CORE-IP:8080 \
  TOKEN=YOUR_TOKEN \
  NAME="Living Room" \
  DEVICE=default \
  ./install-endpoint.sh
```

The endpoint installs:

```text
/usr/local/lib/surroundcore/surround-agent.py
/etc/surroundcore/endpoint.env
/etc/systemd/system/surround-agent.service
```

The control API listens on TCP **8090**. Capabilities are available at:

```text
http://ENDPOINT-IP:8090/v1/capabilities
```

## Audio hardware

The installer reports `aplay -l` after installation. `DEVICE=default` is suitable for many systems; a specific ALSA device can be supplied when needed.

The endpoint is intended to support HDMI, USB audio, S/PDIF/AES and other ALSA-visible outputs according to the capabilities detected by `surround-agent.py`.

## Other installation tracks

- `debian13` — clean native Debian 13 SurroundCore Core + ingest
- `proxmox-ct` — privileged Debian 13 Proxmox LXC Core + ingest
- `main` — integrated development baseline

## Licence

SurroundCore uses the **SC-NCE 1.2** licence. Home use, staff-enjoyment use, and free background/ambient use in UK pubs and restaurants are permitted. Selling/rebadging SurroundCore, paid customer features and hotel guest-room/in-room use require prior permission. See `LICENSE` for the exact terms.

## Support

GitHub Issues are preferred for reproducible bugs and feature requests. Private support: `surroundcore@brimstoncottage.uk`.
