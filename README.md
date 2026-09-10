# SurroundCore

**SurroundCore — created by Kev n Chris.**

SurroundCore is a Debian 13 multichannel music core and endpoint system intended to sit alongside Meridian/Sooloos and provide modern local, network, disc-ingest and streaming-provider support.

Current status: **v0.5 development / developer preview**.

## Installation tracks

Use the branch that matches the machine you are building:

| Branch | Purpose |
| --- | --- |
| [`debian13`](https://github.com/lurcheous73/SurroundCore/tree/debian13) | Clean SurroundCore Core install on a normal Debian 13 host/VM/bare-metal machine. |
| [`proxmox-ct`](https://github.com/lurcheous73/SurroundCore/tree/proxmox-ct) | **Privileged** Debian 13 Proxmox LXC build for Core + optical ingest, MakeMKV integration and USB/media hand-off. |
| [`endpoint`](https://github.com/lurcheous73/SurroundCore/tree/endpoint) | Lightweight Debian 13 playback endpoint for Pi/NUC/PC/remote audio hardware. |
| [`main`](https://github.com/lurcheous73/SurroundCore/tree/main) | Integrated development baseline. |

Streaming/platform work may also be developed on feature branches before being merged into the appropriate install tracks.

## Goals

- Preserve and catalogue stereo, quad, 5.0, 5.1 and 7.1 music editions.
- Play multichannel PCM/FLAC over local or remote HDMI/ALSA endpoints.
- Keep original DSF/DFF/MKA/MKV assets available for later native/bitstream support.
- Discover and control network endpoints including Sonos, AirPlay and Chromecast-compatible devices as support matures.
- Treat grouped/bonded devices as logical zones with explicit channel roles and synchronization.
- Support local, USB, NFS, SMB/CIFS, Plex-library and cloud/rclone-backed sources with optional caching.
- Provide an authenticated HTTP API for ControlMac and other clients.
- Support direct optical ingest/ripping and disc-image workflows.

## Required platform

**Debian 13 (Trixie)** is the supported Linux baseline.

The Proxmox CT edition deliberately uses a **privileged LXC**. Optical drives and MakeMKV require low-level SCSI access (`/dev/sr*` plus the matching `/dev/sg*`), which an unprivileged LXC can block even when the block device itself is visible.

## Debian 13 Core

For a normal Debian 13 host, VM or bare-metal system use the `debian13` branch:

```bash
git clone --branch debian13 https://github.com/lurcheous73/SurroundCore.git
cd SurroundCore
sudo ./install.sh
```

## Proxmox VE privileged CT

For Proxmox VE use the `proxmox-ct` branch. Its CT/install/json layout follows the Community Scripts development conventions and installs the application natively in the privileged Debian 13 LXC rather than nesting Docker inside the CT.

```bash
git clone --branch proxmox-ct https://github.com/lurcheous73/SurroundCore.git
cd SurroundCore
bash ct/surroundcore.sh
```

The Proxmox media library bind is intended to be **read/write** when SurroundCore is expected to rip/import media into that library. The host-side media broker is being developed to detect newly attached USB/optical devices and ask through the SurroundCore web interface whether the device should be handed to the CT or left on the Proxmox host.

See [`docs/PROXMOX_CT.md`](docs/PROXMOX_CT.md) on the `proxmox-ct` branch for the Proxmox-specific architecture.

## Endpoint-only install

Use the `endpoint` branch for a playback-only machine. An endpoint does not need the Core database, storage stack, optical ingest service or streaming-provider configuration; it registers with an existing SurroundCore Core and exposes its local ALSA/HDMI hardware.

```bash
git clone --branch endpoint https://github.com/lurcheous73/SurroundCore.git
cd SurroundCore
sudo CORE_URL=http://CORE-IP:8080 TOKEN=YOUR_TOKEN ./install-endpoint.sh
```

## API

FastAPI documentation is available from `http://CORE:8080/docs` on a running Core. Major API families include endpoints/groups, library/media, storage sources/cache, streaming/providers, Sonos and ingest.

## Licence

SurroundCore is provided under the **SurroundCore No-Commercial-Exploitation Licence (SC-NCE) 1.2**.

Personal/community use is allowed. Businesses may use SurroundCore for staff enjoyment and may use it free for background/ambient music in UK pubs, restaurants and comparable venues. Commercial exploitation of SurroundCore itself, rebadging it as somebody else's product, or using it as a paid/material customer amenity requires prior permission. Hotel guest-room/in-room entertainment is specifically treated as commercial use.

See [`LICENSE`](LICENSE) for the exact terms. Music, streaming-service and public-performance rights remain separate from the SurroundCore software licence.

## Support

For installation problems, reproducible bugs and feature requests, use [GitHub Issues](https://github.com/lurcheous73/SurroundCore/issues).

For private support, email [surroundcore@brimstoncottage.uk](mailto:surroundcore@brimstoncottage.uk).

Please remove passwords, API keys, OAuth tokens and other credentials before posting logs publicly. See [`SUPPORT.md`](SUPPORT.md) for the current support scope.
