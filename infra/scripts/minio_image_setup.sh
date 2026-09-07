#!/bin/sh
set -eu

printf '%s\n' 'minio:x:10002:10002:MinIO:/data:/sbin/nologin' >>/etc/passwd
printf '%s\n' 'minio:x:10002:' >>/etc/group
mkdir -p /data
chown -R 10002:10002 /data
chmod 755 /usr/local/bin/alittlemore-minio-entrypoint
rm -f -- "$0"
