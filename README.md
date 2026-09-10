# SurroundCore

SurroundCore is a Debian 13-only multichannel music core and endpoint system intended to sit alongside Meridian/Sooloos and be controlled by modern clients such as ControlMac 2026.

Current status: **v0.5 development / developer preview**. Storage, synchronized playback, optical ingest and streaming providers are being developed as one system.

## Goals

- Preserve and catalogue stereo, quad, 5.0, 5.1 and 7.1 music editions.
- Play multichannel PCM/FLAC over local or remote HDMI/ALSA endpoints.
- Keep original DSF/DFF/MKA/MKV assets available for later native/bitstream support.
- Discover network endpoints, beginning with Sonos UPnP topology.
- Treat bonded Sonos speaker sets as one logical zone with explicit channel roles.
- Support multiple local, USB, NFS, SMB/CIFS and rclone-backed library sources with optional read-through/pinned cache.
- Provide a small authenticated HTTP API for ControlMac and other clients.
- Run the Core in Docker while keeping physical ALSA/HDMI playback in a native endpoint agent.

## Storage foundation (v0.4 development)

SurroundCore now separates **library sources** from **playback endpoints**. The Docker host owns mounts and credentials; the Core receives media read-only and can cache selected files locally for reliable playback.

- Local/USB/NFS/SMB-CIFS/rclone source registration
- Read-through and pinned cache policies with LRU/free-space limits
- Cached media remains playable when a read-through source goes offline
- Host-side `surround-storage` helper keeps SMB/cloud credentials out of the playback container
- Plex is a reserved source kind; its provider adapter is not implemented yet

See `docs/STORAGE.md` for the source, mount and cache model.

## Streaming providers (v0.5 development)

SurroundCore now owns streaming configuration and quality negotiation; ControlMac remains a control surface.

- Core-hosted setup page at `/setup/streaming`
- Provider-neutral streaming API and saved Internet Radio stations
- Bandcamp Subsonic account/purchased-library adapter with Core-side credential proxying
- Native RSS/Atom podcast feed and episode playback
- Official Spotify Soloist control surface (user-supplied binary/API key)
- Sonos OAuth + Sonos Radio/Favorites control on Sonos groups
- Licensed-provider bridge contract for TIDAL, Qobuz, HDtracks/AIRIA, Apple Music and Audible
- Highest-native/lossless-first source policy with downsample/downmix disabled by default
- MQA pass-through policy for legacy/provider-supplied MQA
- Optional licensed AIRIA helper detection (AIRIA is never claimed when no module is installed)
- Meridian output negotiation prefers MHR for stereo and MMHR for multichannel only when an endpoint actually advertises those transports
- Generic ALSA endpoints explicitly advertise PCM rather than pretending to support Meridian transports

See `docs/STREAMING.md` for provider status and the implemented/pending boundary.

## Required platform

**Debian 13 (Trixie) only.**

The installer intentionally refuses Debian 12, Ubuntu and other distributions. The Core container is also built from `debian:13-slim` so the host, CT/VM and container share one supported baseline.

Supported deployment targets:

- Proxmox VE LXC (recommended for the Core)
- Debian 13 VM
- Debian 13 bare metal/NAS
- Debian 13 Pi/NUC endpoint

## Architecture

```text
ControlMac / API client
        |
        v
SurroundCore (Docker, TCP 8080)
  - multi-source library scanner / FFprobe
  - SQLite catalogue + source registry
  - read-through/pinned media cache
  - endpoint registry + synchronized groups
  - Sonos / mDNS / UPnP discovery
  - streaming-provider and quality policy layer
  - authenticated media streaming
        |
        +-----------------------+
        |                       |
        v                       v
local endpoint agent       remote endpoint agent
(native systemd)           Pi / NUC / Debian
        |                       |
      ALSA                    ALSA
        |                       |
 HDMI / USB / sound card   HDMI / USB / sound card
```

The Core uses host networking so SSDP/multicast discovery works correctly. Physical audio remains outside Docker; this avoids unnecessary device/hotplug complexity and allows ALSA to claim the selected output directly.

## Quick install on Debian 13

```bash
git clone https://github.com/lurcheous73/SurroundCore.git
cd SurroundCore
sudo ./install.sh
```

## Proxmox CT

The helper creates an unprivileged Debian 13 CT with nesting enabled for Docker:

```bash
CTID=700 STORAGE=NVME-512-ZFS BRIDGE=vmbr0 IPCFG=ip=dhcp ./proxmox/create-ct.sh
```

For a tagged network, edit/pass the CT network with the required VLAN tag using `pct set`; the reference deployment uses VLAN 30.

Media should normally be mounted read-only into the CT from the host dataset:

```bash
./proxmox/add-media-bind.sh 700 /path/to/music-surround
```

See `docs/SHARES.md` for host mounts and `docs/STORAGE.md` for multi-source/cache configuration.

## API

FastAPI documentation is available from `http://CORE:8080/docs`. Main API families are:

- `/api/v1/endpoints` — discovered/registered playback endpoints
- `/api/v1/groups` — synchronized playback groups
- `/api/v1/library` and `/api/v1/media/...` — indexed media and authenticated streaming
- `/api/v1/sources` — multi-source storage, scans and cache management
- `/api/v1/streaming` and `/api/v1/providers/...` — streaming quality/providers/accounts
- `/api/v1/sonos/...` — Sonos playback compatibility

A random shared token is generated by `install.sh`. Protected calls use `Authorization: Bearer <token>`; media streams can also receive the token as a query parameter for endpoint playback.

## Support

For installation problems, reproducible bugs and feature requests, use [GitHub Issues](https://github.com/lurcheous73/SurroundCore/issues).

For direct support enquiries, email [surroundcore@brimstoncottage.uk](mailto:surroundcore@brimstoncottage.uk).

Please remove passwords, API keys, OAuth tokens and other credentials before posting logs publicly.

See [SUPPORT.md](SUPPORT.md) for what to include in a support request and the current support scope.
