# Media storage

SurroundCore reads media from `/srv/surroundcore/media` on the Debian host. Docker receives that path read-only as `/media`.

## Preferred: local NAS / Proxmox bind mount

If SurroundCore runs in an LXC on the same host that owns the media dataset, bind-mount the dataset into the CT. This avoids SMB/NFS overhead and keeps storage credentials out of Docker.

Example on the Proxmox host:

```bash
./proxmox/add-media-bind.sh 240 /tank/media/music-surround
```

The CT then sees the library at `/srv/surroundcore/media`.

## NFS

Mount NFS on Debian first, then let Docker consume the local mount:

```bash
mount -t nfs4 10.0.10.100:/music /srv/surroundcore/media
```

Use `/etc/fstab` for a permanent mount.

## SMB/CIFS

Mount SMB on Debian first. Store credentials in a root-readable credentials file rather than in `docker-compose.yml`.

```bash
mount -t cifs //10.0.10.100/music /srv/surroundcore/media -o credentials=/root/.smb-surroundcore,vers=3.1.1,ro
```
