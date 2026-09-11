#!/usr/bin/env bash
set -Eeuo pipefail
POOL="${SURROUNDCORE_STORAGE_POOL:-SurroundStore}"
CIDR="${SURROUNDCORE_STORAGE_CLIENTS:-10.26.30.0/27}"
ROOT="/pools/${POOL}/library"
[ -d "$ROOT" ] || { echo "library dataset missing: $ROOT" >&2; exit 1; }
mkdir -p /var/run/ganesha /var/lib/nfs/ganesha
cat >/etc/ganesha/ganesha.conf <<EOF
NFS_Core_Param { Protocols = 4; }
EXPORT {
 Export_Id = 705;
 Path = "$ROOT";
 Pseudo = /library;
 Access_Type = RW;
 Squash = No_Root_Squash;
 Protocols = 4;
 Transports = TCP;
 SecType = sys;
 FSAL { Name = VFS; }
 CLIENT { Clients = $CIDR; Access_Type = RW; Squash = No_Root_Squash; }
}
EOF
exec /usr/bin/ganesha.nfsd -F -L STDOUT -f /etc/ganesha/ganesha.conf
