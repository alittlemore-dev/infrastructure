#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd -- "${script_dir}/../.." && pwd)"
work_dir="$(mktemp -d "${TMPDIR:-/tmp}/alm-minio-smoke.XXXXXX")"
run_name="$(basename "$work_dir" | tr '[:upper:]' '[:lower:]')"
server_image="${run_name}-server:local"
client_image="${run_name}-client:local"
server_name="${run_name}-server"
client_name="${run_name}-client"
network_owned=false
server_owned=false
client_owned=false
images_owned=false
platform_arguments=()
if [ -n "${MINIO_SMOKE_PLATFORM:-}" ]; then
    platform_arguments=(--platform "$MINIO_SMOKE_PLATFORM")
fi

cleanup() {
    local cleanup_status=$?
    trap - EXIT HUP INT TERM
    if "$client_owned"; then
        docker rm --force "$client_name" >/dev/null 2>&1 || true
    fi
    if "$server_owned"; then
        docker rm --force "$server_name" >/dev/null 2>&1 || true
    fi
    if "$network_owned"; then
        docker network rm "$run_name" >/dev/null 2>&1 || true
    fi
    if "$images_owned"; then
        docker image rm "$server_image" "$client_image" >/dev/null 2>&1 || true
    fi
    rm -rf -- "$work_dir"
    exit "$cleanup_status"
}
trap cleanup EXIT
trap 'exit 130' HUP INT TERM

for image in "$server_image" "$client_image"; do
    if docker image inspect "$image" >/dev/null 2>&1; then
        echo "Refusing to replace an existing smoke-test image: ${image}" >&2
        exit 1
    fi
done
if docker container inspect "$server_name" >/dev/null 2>&1 \
    || docker container inspect "$client_name" >/dev/null 2>&1 \
    || docker network inspect "$run_name" >/dev/null 2>&1; then
    echo "Refusing to reuse existing smoke-test resources." >&2
    exit 1
fi
images_owned=true
docker build --pull "${platform_arguments[@]}" --tag "$server_image" \
    --file "$repo_dir/infra/minio/Dockerfile" "$repo_dir"
docker build --pull "${platform_arguments[@]}" --tag "$client_image" \
    --file "$repo_dir/infra/minio-mc/Dockerfile" "$repo_dir"

mkdir "$work_dir/secrets"
chmod 755 "$work_dir/secrets"
for identity in minio_root personal_workspace_minio competency_minio databasus_minio auth_api_minio; do
    printf '%s-smoke-access\n' "$identity" >"$work_dir/secrets/${identity}_access_key"
    printf '%s-smoke-secret\n' "$identity" >"$work_dir/secrets/${identity}_secret_key"
done
chmod 644 "$work_dir/secrets/"*

network_owned=true
docker network create --label "alittlemore.minio-smoke=$run_name" "$run_name" >/dev/null
server_owned=true
docker run --detach "${platform_arguments[@]}" --name "$server_name" \
    --label "alittlemore.minio-smoke=$run_name" \
    --network "$run_name" --network-alias minio \
    --read-only --cap-drop ALL --security-opt no-new-privileges:true \
    --tmpfs /data:uid=10002,gid=10002,mode=0700 \
    --tmpfs /tmp:mode=1777 \
    --mount "type=bind,source=$work_dir/secrets,target=/run/secrets,readonly" \
    "$server_image" >/dev/null

ready=false
for attempt in {1..60}; do
    if docker exec "$server_name" curl --fail --silent --output /dev/null \
        http://localhost:9000/minio/health/live; then
        ready=true
        break
    fi
    sleep 1
done
if ! "$ready"; then
    docker logs "$server_name" >&2
    echo "The isolated MinIO smoke-test server did not become ready after ${attempt} attempts." >&2
    exit 1
fi
docker exec "$server_name" curl --fail --silent --output /dev/null http://localhost:9001/

run_client_script() {
    client_owned=true
    docker run --rm "${platform_arguments[@]}" --name "$client_name" --network "$run_name" \
        --read-only --cap-drop ALL --security-opt no-new-privileges:true \
        --tmpfs /tmp:mode=1777,uid=10003,gid=10003 \
        --mount "type=bind,source=$work_dir/secrets,target=/run/secrets,readonly" \
        --mount "type=bind,source=$repo_dir/infra/minio/policies,target=/policies,readonly" \
        --mount "type=bind,source=$1,target=/smoke.sh,readonly" \
        --entrypoint /bin/sh "$client_image" /smoke.sh
    client_owned=false
}
run_client_script "$script_dir/minio_bootstrap.sh"
run_client_script "$script_dir/minio_bootstrap.sh"
run_client_script "$script_dir/minio_smoke_client.sh"
echo "MinIO build, health, repeated bootstrap and isolated S3 policies passed."
