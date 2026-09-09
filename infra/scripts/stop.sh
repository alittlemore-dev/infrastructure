#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd -- "${script_dir}/../.." && pwd)"
cd "$repo_dir"

# shellcheck source=infra/scripts/common.sh
. "$script_dir/common.sh"

readonly STOP_COMPOSE_FILE="${repo_dir}/infra/compose/stop.yml"

require_command docker
acquire_runtime_lock
pin_compose_identity

docker compose \
    --project-name alittlemore-infra \
    --file "$STOP_COMPOSE_FILE" \
    down --remove-orphans
rm -f "$(runtime_state_directory)/active-slot"
