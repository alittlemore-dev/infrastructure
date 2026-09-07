#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd -- "${script_dir}/../.." && pwd)"
cd "$repo_dir"

while IFS= read -r shell_file; do
    bash -n "$shell_file"
done < <(find infra/scripts -type f -name '*.sh' -print | sort)
python3 -m json.tool infra/minio/policies/personal-workspace.json >/dev/null
python3 -m json.tool infra/minio/policies/competency-trainer.json >/dev/null
python3 -m json.tool infra/minio/policies/databasus.json >/dev/null

python3 infra/scripts/render_deploy_env.py \
    --manifest infra/deploy/runtime-env.manifest.json \
    --validate-only
python3 -m unittest discover -s tests -p 'test_*.py' -v

set -a
# shellcheck disable=SC1091
. .env.example
set +a

export PERSONAL_WORKSPACE_ACTIVE_BACKEND=personal-workspace-backend-blue
export PERSONAL_WORKSPACE_ACTIVE_FRONTEND=personal-workspace-frontend-blue
export COMPETENCY_ACTIVE_BACKEND=competency-backend-blue
export COMPETENCY_ACTIVE_FRONTEND=competency-frontend-blue
export COMPOSE_MINIO_ROOT_ACCESS_KEY_FILE=/dev/null
export COMPOSE_MINIO_ROOT_SECRET_KEY_FILE=/dev/null
export COMPOSE_DATABASUS_MINIO_ACCESS_KEY_FILE=/dev/null
export COMPOSE_DATABASUS_MINIO_SECRET_KEY_FILE=/dev/null
export COMPOSE_PERSONAL_WORKSPACE_APP_SECRET_KEY_FILE=/dev/null
export COMPOSE_PERSONAL_WORKSPACE_DB_PASSWORD_FILE=/dev/null
export COMPOSE_PERSONAL_WORKSPACE_MINIO_ACCESS_KEY_FILE=/dev/null
export COMPOSE_PERSONAL_WORKSPACE_MINIO_SECRET_KEY_FILE=/dev/null
export COMPOSE_PERSONAL_WORKSPACE_OWNER_PASSWORD_HASH_FILE=/dev/null
export COMPOSE_PERSONAL_WORKSPACE_SENTRY_DSN_FILE=/dev/null
export COMPOSE_COMPETENCY_APP_SECRET_KEY_FILE=/dev/null
export COMPOSE_COMPETENCY_AUTH_PRIVATE_KEY_FILE=/dev/null
export COMPOSE_COMPETENCY_DB_PASSWORD_FILE=/dev/null
export COMPOSE_COMPETENCY_MINIO_ACCESS_KEY_FILE=/dev/null
export COMPOSE_COMPETENCY_MINIO_SECRET_KEY_FILE=/dev/null
export COMPOSE_COMPETENCY_OWNER_INIT_PASSWORD_FILE=/dev/null
export COMPOSE_COMPETENCY_SENTRY_DSN_FILE=/dev/null
export COMPOSE_COMPETENCY_AGENT_ISSUING_CERTIFICATE_FILE=/dev/null
export COMPOSE_COMPETENCY_AGENT_ISSUING_PRIVATE_KEY_FILE=/dev/null
export COMPOSE_COMPETENCY_AGENT_CERTIFICATE_CHAIN_FILE=/dev/null
export COMPOSE_PROJECT_NAME=alittlemore-infra
unset COMPOSE_FILE COMPOSE_PROFILES

docker compose --env-file .env.example config --quiet
