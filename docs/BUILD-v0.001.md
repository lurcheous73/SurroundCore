# SurroundCore v0.001 baseline

Date: 11 September 2026

This tag is the first locked Brimstone / SurroundCore appliance baseline.

## Canonical user interface

The canonical web shell is the goth-black / aubergine / purple ControlMac-style interface with these primary pages:

- Library
- Now Playing
- Import
- Export
- Discs
- Recording
- Devices
- Configuration
- Remote

Do not replace this shell with the older Play / Zones / Streaming / Rip / Burn / Admin layout. Backend features must be merged into this UI rather than reverting the presentation layer.

## Important behaviour

- Sooloos is not polled during routine UI/session refreshes.
- Sooloos catalogue replication is stored locally in SQLite and refreshed explicitly.
- Recording interfaces use the same hardware-first grouped presentation as output devices.
- MusicBrainz, Cover Art Archive, TheAudioDB, Bandcamp artwork and Discogs share Core-owned provider settings.
- Provider secrets remain on the Core and are not returned to clients.
