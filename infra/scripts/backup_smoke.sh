#!/usr/bin/env bash
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd -- "${script_dir}/../.." && pwd)"
work_dir="$(mktemp -d "${TMPDIR:-/tmp}/alm-backup-smoke.XXXXXX")"
run_name="$(basename "$work_dir" | tr '[:upper:]' '[:lower:]')"
worker_image="${run_name}-worker:local"
server_image="${run_name}-minio:local"
reader_image="${run_name}-reader:local"
images_owned=false
cleanup() {
    local status=$?
    trap - EXIT HUP INT TERM
    if "$images_owned"; then
        docker image rm "$worker_image" "$server_image" "$reader_image" >/dev/null 2>&1 || true
    fi
    rm -rf -- "$work_dir"
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' HUP INT TERM
for image in "$worker_image" "$server_image" "$reader_image"; do
    if docker image inspect "$image" >/dev/null 2>&1; then
        echo "Refusing to replace an existing backup smoke image." >&2
        exit 1
    fi
done
images_owned=true
docker build --tag "$worker_image" --file "$repo_dir/infra/backup/Dockerfile" "$repo_dir"
docker build --tag "$server_image" --file "$repo_dir/infra/minio/Dockerfile" "$repo_dir"
docker build --tag "$reader_image" --file "$repo_dir/infra/backup-status/Dockerfile" "$repo_dir"
python3 "$script_dir/backup_smoke_client.py" host --name "$run_name" \
    --worker-image "$worker_image" --server-image "$server_image" --reader-image "$reader_image" --directory "$work_dir"
