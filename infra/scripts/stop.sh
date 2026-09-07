#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd -- "${script_dir}/../.." && pwd)"
cd "$repo_dir"

# shellcheck source=infra/scripts/common.sh
. "$script_dir/common.sh"
# shellcheck source=infra/scripts/compose_secrets.sh
. "$script_dir/compose_secrets.sh"

require_command docker
acquire_runtime_lock
pin_compose_identity

for variable_name in \
    "${REQUIRED_ENVIRONMENT_VARIABLES[@]}" \
    "${ALLOW_EMPTY_ENVIRONMENT_VARIABLES[@]}"; do
    export "$variable_name=stop-placeholder"
done
export IMAGE_REGISTRY=invalid.local/alittlemore-dev
export VPN_BIND_ADDRESS=127.0.0.1

for spec in "${COMPOSE_SECRET_SPECS[@]}"; do
    read -r _ _ compose_file_variable_name _ _ <<<"$spec"
    export "$compose_file_variable_name=/dev/null"
done

docker compose --env-file /dev/null down
rm -f "$(runtime_state_directory)/active-slot"
