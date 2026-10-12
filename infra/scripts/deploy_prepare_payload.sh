#!/usr/bin/env bash
set -euo pipefail

umask 077
rm -rf -- .deploy-payload
mkdir -p .deploy-payload
cp -a .dockerignore .sops.yaml Makefile docker-compose.yml config/ secrets/ infra/ .deploy-payload/
python3 infra/scripts/backup_deployment_metadata.py --release-only --output-dir .deploy-payload/infra/deploy
