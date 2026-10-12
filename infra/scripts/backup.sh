#!/usr/bin/env bash
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd -- "${script_dir}/../.." && pwd)"
action="${1:?Choose run, status or health explicitly.}"
project="${BACKUP_COMPOSE_PROJECT:-alittlemore-infra}"
arguments=(--state-dir /state)
case "$action" in
    run)
        arguments+=(--work-dir /work --projection-dir /projection --config /etc/backup/sources.json --destinations /etc/backup/destinations.json)
        ;;
    status) arguments+=(--json) ;;
    health) ;;
    *) echo "Usage: backup.sh {run|status|health}" >&2; exit 2 ;;
esac
docker compose --project-name "$project" --file "$repo_dir/infra/compose/stop.yml" \
    exec --no-TTY backup python -m infra_backup.cli "$action" "${arguments[@]}"
