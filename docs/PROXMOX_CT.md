# SurroundCore Proxmox CT Architecture

This document defines the Proxmox VE installation and removable-media model for SurroundCore.

The implementation on the `proxmox-ct` branch follows the structure and conventions used by the Community Scripts development repository (`community-scripts/ProxmoxVED`) and its shared `community-scripts/core` engine.

## Installation model

SurroundCore is installed natively in a Debian 13 LXC. The Proxmox edition does not use Docker inside the container.

The Community Scripts compatible files are:

- `ct/surroundcore.sh` — creates and updates the LXC through the shared Community Scripts core engine.
- `install/surroundcore-install.sh` — installs SurroundCore and its dependencies inside the LXC.
- `json/surroundcore.json` — metadata used by the Community Scripts frontend and validation tooling.
- `tools/pve/` — Proxmox-host-only helpers, including the removable-media broker.

The LXC is privileged. This is intentional: optical ingest and MakeMKV require SCSI-generic ioctl access that is not reliable through an unprivileged LXC.

## Native services

The Proxmox installation runs two systemd services:

- `surroundcore.service` — web UI, library, playback and provider control on TCP 8080.
- `surroundcore-ingest.service` — optical, disc-image and MiniDisc ingest on loopback TCP 8082.

Both services use `/etc/surroundcore/surroundcore.env`. The master token is generated locally during installation and must never be committed to Git.

The default rip destination is `/srv/surroundcore/media`. A Proxmox bind mount placed there must be read/write when SurroundCore is expected to rip directly into the main music library.

## MakeMKV

MakeMKV belongs inside the SurroundCore LXC, not on the Proxmox host.

SurroundCore does not redistribute MakeMKV, its proprietary binaries, registration keys, beta keys, or licence files. A MakeMKV installation is stored outside the SurroundCore source tree so application updates cannot delete it.

Default native path:

`/opt/surroundcore-makemkv/makemkvcon`

The Proxmox host should not require MakeMKV.

## Removable-media ownership model

The Proxmox host is the gatekeeper for newly attached physical media devices. SurroundCore does not receive every USB device automatically.

For each eligible newly-arrived media device:

1. The host broker discovers and classifies the device.
2. The broker registers a pending device with the SurroundCore web UI.
3. The UI asks the administrator whether the device should be added to SurroundCore.
4. **Yes**: the host broker exposes the required device nodes to the running privileged LXC using live LXC device passthrough.
5. **No**: nothing is exposed to the LXC; the device remains available to the Proxmox host.
6. On physical removal, the broker removes any live LXC device exposure and clears the pending/accepted state.

The decision is per insertion. A future option may allow remembering a device by stable USB identity, but the safe default is to ask.

## Optical drives

An accepted USB optical drive is passed as a device set rather than as `/dev/sr0` alone. The broker resolves the current nodes at insertion time and exposes:

- the optical block node, for example `/dev/sr0`;
- the matching SCSI-generic node, for example `/dev/sg10`;
- the current USB device node when a supported workflow needs raw USB access.

The `/dev/sr*` and `/dev/sg*` numbers are not treated as stable identities.

Rip and Burn use the same accepted-device registry. A drive accepted for SurroundCore must therefore appear consistently on both pages.

## USB storage

USB storage is eligible only when it is safe to offer. The broker must refuse or silently ignore devices that are part of the Proxmox host's own storage stack.

A device must not be offered when any of the following apply:

- it or a child device is mounted by the host;
- it contains an active ZFS member or belongs to an imported ZFS pool;
- it contains an LVM/LVM2 physical volume in use by the host;
- it backs configured Proxmox storage;
- it is the Proxmox boot/root device or an ancestor of it;
- it is already assigned to another guest in a way that would conflict;
- its identity cannot be established safely.

This specifically prevents a removable-looking USB disk that contains a live Proxmox ZFS pool from appearing as an innocent "add this drive" prompt.

## Host broker security boundary

Only the Proxmox host performs `lxc-device`, `pct`, udev and host-storage inspection operations. The SurroundCore LXC is never given the Proxmox API token, host SSH keys, or permission to execute arbitrary commands on the host.

The broker communicates device candidates and decisions through a narrow local integration. No new public host-management TCP port is required.

## Git and secrets

The following must never be committed:

- `SURROUNDCORE_TOKEN` values;
- streaming-provider secrets, API keys or OAuth refresh tokens;
- MakeMKV registration or beta keys;
- MakeMKV proprietary binaries;
- machine-specific `/dev/bus/usb/...`, `/dev/sr*` or `/dev/sg*` mappings;
- Proxmox API tokens or SSH private keys.

Installers may create local configuration containing secrets at runtime, with restrictive permissions.

## Development status

The stable Proxmox installation track is the `proxmox-ct` branch. Community-Scripts-shaped development can still be done on temporary feature branches before being folded into this branch.

The media broker API/UI and host daemon remain active development. Until that is complete, development machines may still have manual/static device mappings; those mappings are not the intended final installation model.
