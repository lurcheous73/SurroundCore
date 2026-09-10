# SurroundCore — Proxmox CT

**SurroundCore — created by Kev n Chris.**

This branch is the supported **Proxmox VE privileged LXC** build of SurroundCore.

It follows the current Community Scripts / ProxmoxVED structure: the Proxmox host creates the CT, while SurroundCore itself is installed **natively inside Debian 13** using systemd services rather than Docker-in-LXC.

## Why privileged?

Optical ingest and MakeMKV require low-level SCSI access to the optical block device and its matching generic SCSI device, for example `/dev/sr0` and `/dev/sg10`. Testing showed that an unprivileged LXC can expose `/dev/sr0` while still blocking the SCSI ioctls MakeMKV requires.

For that reason this build intentionally creates a **privileged** container.

## Install

Run on the Proxmox VE host:

```bash
git clone --branch proxmox-ct https://github.com/lurcheous73/SurroundCore.git
cd SurroundCore
bash ct/surroundcore.sh
```

The Community-Scripts-style installer creates a Debian 13 CT and installs:

- SurroundCore Core on TCP 8080
- SurroundCore optical/media ingest service
- FFmpeg and lossless audio tools
- CD/DVD/Blu-ray discovery/ripping utilities
- optical burning utilities
- NetMD dependencies
- systemd services and persistent configuration

The application itself is installed under `/opt/surroundcore`; persistent configuration is under `/etc/surroundcore` and data under `/var/lib/surroundcore`.

## Music library

If the CT is expected to rip/import media into the main library, the music dataset must be mounted **read/write**:

```bash
./proxmox/add-media-bind.sh CTID /path/to/music /srv/surroundcore/media
```

Pass `ro` as the fourth argument only when a deliberately read-only library is wanted.

## USB and optical media

`tools/pve/surroundcore-media-broker.sh` is the host-side foundation for controlled removable-media hand-off.

The intended finished flow is:

```text
USB/optical device connected to Proxmox host
        |
        v
safe-device check
        |
        v
SurroundCore web prompt: Add this device to SurroundCore?
        |                               |
       Yes                              No
        |                               |
live attach to CT                 remain on host
        |
      unplug
        |
live detach from CT
```

The broker must never offer mounted devices, active ZFS/LVM members, Proxmox storage devices, or other host-critical disks for hand-off.

## MakeMKV

MakeMKV belongs **inside this privileged CT**, not on the Proxmox host. SurroundCore does not redistribute MakeMKV binaries or licence keys; `/opt/surroundcore-makemkv` is reserved for the locally installed vendor CLI.

The ingest configuration expects:

```text
SURROUNDCORE_MAKEMKVCON=/opt/surroundcore-makemkv/makemkvcon
```

## Other installation tracks

- `debian13` — clean native Debian 13 Core install
- `endpoint` — playback-only endpoint
- `main` — integrated development baseline

## Licence

SurroundCore uses the **SC-NCE 1.2** licence. Home, staff-enjoyment and free ambient/background use in UK pubs and restaurants is permitted. Commercial exploitation, rebadging, paid customer features and hotel guest-room/in-room use require prior permission. See `LICENSE` for the exact terms.

## Support

GitHub Issues are preferred for reproducible bugs and feature requests. Private support: `surroundcore@brimstoncottage.uk`.
