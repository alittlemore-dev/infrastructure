#!/usr/bin/env bash
set -euo pipefail

umask 077
mkdir -p .deploy-payload
cp -a .dockerignore Makefile docker-compose.yml infra/ .env .deploy-payload/
chmod 600 .deploy-payload/.env
