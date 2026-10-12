#!/usr/bin/env bash
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd -- "${script_dir}/../.." && pwd)"
work_dir="$(mktemp -d "${TMPDIR:-/tmp}/alm-backup-types.XXXXXX")"
image="$(basename "$work_dir" | tr '[:upper:]' '[:lower:]'):local"
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
    echo "Refusing to reuse a backup type-check image." >&2
    exit 1
fi
image_owned=true
docker build --tag "$image" --file "$repo_dir/infra/backup/check/Dockerfile" "$repo_dir"
docker run --rm --cpus 1 --memory 384m --network none --read-only --cap-drop ALL \
    --security-opt no-new-privileges:true --tmpfs /tmp \
    --mount "type=bind,source=$repo_dir/infra/backup/src,target=/source,readonly" \
    --mount "type=bind,source=$script_dir,target=/checks,readonly" "$image"
