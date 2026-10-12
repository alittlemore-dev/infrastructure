#!/usr/bin/env bash
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd -- "${script_dir}/../.." && pwd)"
work_dir="$(mktemp -d "${TMPDIR:-/tmp}/alm-backup-http.XXXXXX")"
run_name="$(basename "$work_dir" | tr '[:upper:]' '[:lower:]')"
image="${run_name}:local"
image_owned=false
cleanup() {
    local status=$?
    if "$image_owned"; then
        docker image rm "$image" >/dev/null 2>&1 || true
    fi
    rm -rf -- "$work_dir"
    exit "$status"
}
trap cleanup EXIT
if docker image inspect "$image" >/dev/null 2>&1; then
    echo "Refusing to reuse a backup HTTP test image." >&2
    exit 1
fi
image_owned=true
docker build --tag "$image" --file "$repo_dir/infra/backup-status/Dockerfile" "$repo_dir"
docker run --rm --cpus 1 --memory 256m --network none --read-only --cap-drop ALL --security-opt no-new-privileges:true \
    --tmpfs /tmp --mount "type=bind,source=$script_dir/backup_status_http_checks.py,target=/app/checks.py,readonly" \
    --entrypoint python "$image" /app/checks.py
python3 "$script_dir/backup_status_edge_test.py" --image "$image"
