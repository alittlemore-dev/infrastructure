#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd -- "${script_dir}/../.." && pwd)"
state_dir="${ALITTLEMORE_DEV_STATE_DIR:-${repo_dir}/.dev-state}"
if [ ! -f "${state_dir}/compose.env" ]; then
    echo 'Start the isolated local stack with make dev before seeding analytics.' >&2
    exit 1
fi

docker compose \
    --project-name alittlemore-dev \
    --env-file "${repo_dir}/config/platform/development.env" \
    --env-file "${state_dir}/compose.env" \
    --file "${repo_dir}/docker-compose.yml" \
    --file "${repo_dir}/docker-compose.dev.yml" \
    exec -T competency-backend-blue .venv/bin/python - < "${script_dir}/seed_dev_analytics.py"
