#!/usr/bin/env sh
set -eu
apk add --no-cache python3 py3-pip libstdc++
python3 -m venv /app/.venv
/app/.venv/bin/pip install --no-cache-dir --require-hashes -r /app/requirements.txt
addgroup -g 10004 infra-backup
adduser -D -H -u 10004 -G infra-backup infra-backup
mkdir -p /state /work /projection
chown 10004:10004 /state /work /projection
chmod 700 /state /work /projection
pg_dump --version
valkey-cli --version
