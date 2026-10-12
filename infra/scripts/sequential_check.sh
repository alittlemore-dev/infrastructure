#!/usr/bin/env bash
set -euo pipefail

# Bound BuildKit's internal parallelism as well as the outer Make process.
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd -- "${script_dir}/../.." && pwd)"
work_dir="$(mktemp -d "${TMPDIR:-/tmp}/alm-sequential.XXXXXX")"
builder="$(basename "$work_dir" | tr '[:upper:]' '[:lower:]')"
builder_owned=false
cleanup() {
    local status=$?
    trap - EXIT HUP INT TERM
    if "$builder_owned"; then
        docker buildx rm --force "$builder" >/dev/null 2>&1 || true
    fi
    rm -rf -- "$work_dir"
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' HUP INT TERM
printf '[worker.oci]\n  max-parallelism = 1\n' >"$work_dir/buildkit.toml"
if docker buildx inspect "$builder" >/dev/null 2>&1; then
    echo "Refusing to reuse an existing sequential-check builder." >&2
    exit 1
fi
docker buildx create --name "$builder" --driver docker-container \
    --driver-opt memory=768m,cpu-period=100000,cpu-quota=100000,default-load=true \
    --buildkitd-config "$work_dir/buildkit.toml" >/dev/null
builder_owned=true
export BUILDX_BUILDER="$builder"
export COMPOSE_PARALLEL_LIMIT=1
export NG_BUILD_MAX_WORKERS=1
export GOMAXPROCS=1
export NODE_OPTIONS=--max-old-space-size=1536
export MAKEFLAGS=-j1
cd "$repo_dir"
make -j1 "${@:-quality}"
