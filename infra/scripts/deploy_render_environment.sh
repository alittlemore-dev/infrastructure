#!/usr/bin/env bash
set -euo pipefail

umask 077
python3 infra/scripts/render_deploy_env.py \
    --manifest infra/deploy/runtime-env.manifest.json \
    --output .env
chmod 600 .env
