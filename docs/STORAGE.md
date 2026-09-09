# Library sources and cache

SurroundCore separates **library sources** from **playback endpoints**.

Sources may be:
- local storage
- USB disks
- NFS exports
- CIFS/SMB shares
- Plex source slot (provider adapter pending)
- rclone/FUSE cloud remotes such as MEGA, Dropbox, OneDrive, Google Drive, S3 and WebDAV

The dedicated Docker host owns all filesystem mounts and credentials. Core receives mounted media read-only below `/sources` and writes cache data below `/cache`.

Host layout:
- `/srv/surroundcore/sources/<source-id>` — mounted source
- `/var/cache/surroundcore` — fast source cache
- `/var/cache/surroundcore/rclone/<source-id>` — rclone VFS cache

Core layout:
- `/sources/<source-id>` — read-only library source
- `/cache/<source-id>` — SurroundCore read-through/pinned cache

This keeps SMB passwords, NFS configuration, cloud OAuth tokens and FUSE privileges outside the playback container.

Docker binds the host source root into Core with `rslave` propagation, so a USB/NFS/CIFS/FUSE submount added after the container starts becomes visible without restarting Core. A previously cached read-through/pinned file remains playable if its original source later goes offline.

## Cache policies

`off` reads directly from the source. `metadata` indexes the source but does not cache media files. `read-through` copies a file to fast local cache on first playback and reuses it. `pin` is intended for media that should remain local and is exempt from automatic LRU eviction.

## Cache limits

`SURROUNDCORE_CACHE_MIN_FREE_GB` defaults to 5 GB. When free space falls below the reserve, the oldest non-pinned cache files are removed first.

`SURROUNDCORE_CACHE_MAX_GB=0` means no fixed global cap; set a positive value to impose one. A source may also set `config.cache_limit_gb` for its own read-through cache.

## Host helper

`surround-storage` is installed to `/usr/local/sbin` and supports `local`, `usb`, `nfs`, `cifs`, `rclone`, `umount` and `rclone-gui` operations.

For cloud configuration, `surround-storage rclone-gui` starts rclone's web GUI on `127.0.0.1:5572`. Keep that interface local or place it behind authenticated administration access; cloud credentials should not be exposed directly to the LAN.

An rclone-mounted source already uses rclone's VFS cache for remote filesystem semantics. SurroundCore's own source cache can therefore be `off`/`metadata` for ordinary use, or `read-through`/`pin` when you want whole media files local for reliable playback.

## API

`GET /api/v1/sources` lists registered sources, supported kinds, cache policies and mount status. `POST /api/v1/sources` registers or updates a source. Mounted filesystem sources can be scanned with `POST /api/v1/sources/{id}/scan`.

`POST /api/v1/sources/{id}/cache/warm` preloads indexed media for `read-through` or `pin` sources. `DELETE /api/v1/sources/{id}/cache` purges that source's cache, and `GET /api/v1/sources/cache/status` reports cache usage/free space.

Plex is reserved as an input-only source kind, but the Plex provider/indexer is not implemented yet. The API therefore refuses a Plex source scan rather than pretending the adapter exists.
