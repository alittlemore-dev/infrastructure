#!/usr/bin/env bash
set -euo pipefail

umask 077
rm -rf -- .deploy-payload
mkdir -p .deploy-payload
cp -a .dockerignore .sops.yaml Makefile docker-compose.yml config/ secrets/ infra/ .deploy-payload/
