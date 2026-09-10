# SurroundCore — Debian 13 Core

**SurroundCore — created by Kev n Chris.**

This branch is the clean **native Debian 13 (Trixie)** installation of the SurroundCore Core and ingest services.

No Proxmox-specific configuration is required and this install does not use Docker. SurroundCore runs directly under systemd with a Python virtual environment under `/opt/surroundcore/.venv`.

## Install

Start with a clean Debian 13 system:

```bash
git clone --branch debian13 https://github.com/lurcheous73/SurroundCore.git
cd SurroundCore
sudo ./install.sh
```

The installer checks for Debian 13, installs the required media/network dependencies, creates persistent configuration and starts:

- `surroundcore.service` — Core/API on TCP 8080
- `surroundcore-ingest.service` — optical/media ingest behind the Core

Persistent locations:

```text
/opt/surroundcore                 application
/etc/surroundcore                 configuration
/var/lib/surroundcore             database and ingest state
/var/cache/surroundcore           source cache
/srv/surroundcore/media           read/write music library
/srv/surroundcore/sources         additional source mounts
/opt/surroundcore-makemkv         optional local MakeMKV CLI
/opt/surroundcore-providers       optional provider binaries
```

The music library is deliberately read/write so ripped/imported material can be written directly into the library.

## Optical media

On bare-metal Debian, optical hardware is used directly. CD/DVD/Blu-ray tools, SCSI utilities and disc-burning tools are installed by the Core installer. MakeMKV is optional third-party software and is not redistributed by SurroundCore.

## Other installation tracks

- `proxmox-ct` — privileged Debian 13 Proxmox LXC with removable-media hand-off support
- `endpoint` — lightweight playback-only endpoint for Pi/NUC/PC/remote hardware
- `main` — integrated development baseline

## Licence

SurroundCore uses the **SC-NCE 1.2** licence. Home use, staff-enjoyment use, and free background/ambient use in UK pubs and restaurants are permitted. Selling/rebadging SurroundCore, paid customer features and hotel guest-room/in-room use require prior permission. See `LICENSE` for the exact terms.

## Support

GitHub Issues are preferred for reproducible bugs and feature requests. Private support: `surroundcore@brimstoncottage.uk`.
