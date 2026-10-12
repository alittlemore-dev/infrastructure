#!/usr/bin/env sh
set -eu
pip install --no-cache-dir --require-hashes -r /app/requirements.txt
groupadd --gid 10004 infra-backup
useradd --uid 10004 --gid 10004 --no-create-home infra-backup
mkdir -p /projection
chown 10004:10004 /projection
