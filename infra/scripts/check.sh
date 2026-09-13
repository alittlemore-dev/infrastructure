#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd -- "${script_dir}/../.." && pwd)"
cd "$repo_dir"

while IFS= read -r shell_file; do
    bash -n "$shell_file"
done < <(find infra/scripts -type f -name '*.sh' -print | sort)
while IFS= read -r json_file; do
    python3 -m json.tool "$json_file" >/dev/null
done < <(
    find . -type f -name '*.json' \
        ! -path './.git/*' \
        ! -path './.cache/*' \
        -print \
        | sort
)

python3 infra/scripts/render_runtime_config.py \
    --manifest infra/deploy/runtime-config.manifest.json \
    --repo-dir . \
    --validate-only

runtime_environment_file="$(mktemp)"
trap 'rm -f "$runtime_environment_file"' EXIT
python3 infra/scripts/render_runtime_config.py \
    --manifest infra/deploy/runtime-config.manifest.json \
    --repo-dir . \
    --output "$runtime_environment_file"
set -a
# shellcheck disable=SC1090
. "$runtime_environment_file"
set +a

export PERSONAL_WORKSPACE_ACTIVE_BACKEND=personal-workspace-backend-blue
export COMPETENCY_ACTIVE_BACKEND=competency-backend-blue
export AUTH_API_ACTIVE_BACKEND=auth-api-backend-blue
while IFS= read -r compose_secret_variable; do
    export "$compose_secret_variable=/dev/null"
done < <(
    python3 infra/scripts/list_compose_secret_variables.py \
        infra/deploy/runtime-secrets.manifest.json
)
export COMPOSE_PROJECT_NAME=alittlemore-infra
export COMPOSE_DISABLE_ENV_FILE=1
unset COMPOSE_FILE COMPOSE_PROFILES COMPOSE_ENV_FILES

docker compose --file docker-compose.quality.yml config --quiet
docker compose --env-file /dev/null config --quiet
docker compose --env-file /dev/null config --format json \
    | python3 infra/scripts/list_compose_build_images.py >/dev/null
