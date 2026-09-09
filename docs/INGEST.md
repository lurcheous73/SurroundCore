# SurroundCore Ingest

`surround-ingest` is a separate privileged Docker service for **audio-only** media ingestion. The main `surroundcore` container remains unprivileged and focused on catalogue, streaming and playback.

Web UI: `http://CORE:8082/ingest`

## Sources

Implemented source paths:

- SATA or USB CD/DVD/Blu-ray optical drives exposed as Linux `/dev/sr*` devices
- Audio CD (CDDA)
- DVD-Video used as a music/audio source (`VIDEO_TS`)
- DVD-Audio PCM/MLP programmes exposed through `AUDIO_TS/*.AOB`
- Blu-ray / Pure Audio Blu-ray (`BDMV`)
- uploaded `.iso` images
- uploaded `.bin` + `.cue` CD images
- NetMD / MiniDisc over USB

The service polls physical drives when `SURROUNDCORE_AUTO_RIP=true`. A new disc fingerprint creates one queued job; already-completed media is not automatically ripped again. Manual **Rip now** can force another copy.

## Audio policy

The ingest service uses the same quality policy as ControlMac's optical work:

- choose the highest-quality stereo stream;
- preserve the best stream for every distinct multichannel layout (quad/4.0, 5.1, 7.1, etc.);
- prefer lossless/high-resolution PCM where available;
- no automatic stereo downmix;
- no automatic sample-rate conversion;
- no normalisation or gain processing;
- verify output sample rate and channel count after encoding.

Finished library files are FLAC. 24-bit PCM is encoded as 24-bit FLAC; multichannel layouts remain multichannel.

Video may exist temporarily while a DVD/Blu-ray title is being demultiplexed. **Video is never copied into the finished ingest library.** Temporary MakeMKV title files are deleted after audio extraction.

## CD / BIN-CUE

Physical Audio CD uses Debian's `cd-paranoia` batch reader and converts each verified track to FLAC. Debian 13 provides the libcdio-based `cd-paranoia` implementation with CDDA error correction.

BIN/CUE uses `bchunk`. Audio tracks become individual FLAC tracks; a data track is converted to ISO and then passed back through the same ISO/DVD/Blu-ray detector.

## DVD

Unencrypted DVD-Video is read from the largest title VOB programme and all candidate audio streams are inspected before the stereo/multichannel policy is applied.

`dvdbackup` is installed in the ingest image for DVD access. CSS decryption is **not silently bundled**. Debian's `dvdbackup` can use `libdvdcss2` when an administrator has legitimately installed it; without that optional facility an encrypted commercial DVD may fail rather than producing corrupt audio.

DVD-A uses the largest `AUDIO_TS` AOB programme and extracts its audio streams without retaining video.

## Blu-ray and MakeMKV

Unencrypted BDMV can be processed directly from its transport stream. When an authorised MakeMKV Linux installation is made available, ingest prefers it for Blu-ray title selection because it understands playlists and protected discs.

SurroundCore does not redistribute MakeMKV. Put the user's installation under the host directory configured by `SURROUNDCORE_MAKEMKV_DIR` and set `SURROUNDCORE_MAKEMKVCON` if the executable is in a different location.

AACS-protected Blu-ray fails clearly when MakeMKV is absent; the ingest service does not implement its own AACS decryptor.

## MiniDisc / NetMD

The ingest image contains the same open NetMD JavaScript stack used by ControlMac (`netmd-js` / `netmd-exploits`). A detected MiniDisc is digitally recovered track by track, decoded to stereo 16-bit/44.1 kHz FLAC, verified, and released from USB cleanly.

Hi-MD remains experimental and is not claimed as part of the automatic ingest path until physically verified on Linux.

## Storage handoff

Persistent host paths:

- `/var/lib/surroundcore/ingest` — uploads, job state and temporary work
- `/srv/surroundcore/sources/ingest` — completed verified audio

Inside the ingest container these are `/ingest` and `/library`.

The main Core already mounts `/srv/surroundcore/sources` read-only at `/sources`. After a successful job, ingest registers the `ingest` source (if needed) and calls `/api/v1/sources/ingest/scan` on the main Core. The Mac never carries the disc image or extracted programme.

Successful uploaded ISO/BIN/CUE work files are deleted by default. Set `SURROUNDCORE_INGEST_KEEP_UPLOADS=true` to retain them.

## Device access and Proxmox

The ingest container is intentionally the only privileged service because raw optical and NetMD access requires host devices. The main Core stays unprivileged.

On Debian bare metal or a VM, Docker privileged mode exposes the attached SATA/USB optical and USB devices.

When SurroundCore itself runs inside a Proxmox LXC, the host must first pass the optical/USB device into that CT. Current Proxmox `pct` supports `dev[n]` device passthrough; use that facility for the relevant `/dev/sr*` and SCSI-generic device. USB NetMD hotplug may be easier in a VM or on bare-metal Debian if persistent USB-bus passthrough is required.

## API

- `GET /api/v1/ingest/health`
- `GET /api/v1/ingest/capabilities`
- `GET /api/v1/ingest/devices`
- `GET /api/v1/ingest/jobs`
- `GET /api/v1/ingest/jobs/{id}`
- `POST /api/v1/ingest/rip`
- `POST /api/v1/ingest/netmd`
- `POST /api/v1/ingest/upload`

All APIs except health require the same bearer token as the main Core.

## Current proof boundary

Proven on the development Mac using the real Raven authored Blu-ray programme: the new ingest extractor selected LPCM stereo 24/96, wrote FLAC at 24/96, and decoded source/output PCM SHA-256 values matched exactly (`4e69e4100c2529e1ce3a58794dfc4741953095472275f94c365d176cdb10ddf4`).

Still requiring physical Linux deployment tests before being labelled proven: SATA/USB optical passthrough, physical CDDA in the container, DVD-A AOB on Linux, NetMD USB in the container, and Linux MakeMKV protected-Blu-ray operation.
