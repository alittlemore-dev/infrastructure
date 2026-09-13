#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd -- "${script_dir}/../.." && pwd)"
state_dir="${ALITTLEMORE_DEV_STATE_DIR:-${repo_dir}/.dev-state}"
personal_workspace_dir="${PERSONAL_WORKSPACE_DIR:-${repo_dir}/../personal-workspace}"
competency_trainer_dir="${COMPETENCY_TRAINER_DIR:-${repo_dir}/../competency-trainer}"
auth_api_dir="${AUTH_API_DIR:-${repo_dir}/../auth-api}"
frontend_dir="${FRONTEND_DIR:-${repo_dir}/../frontend}"
platform_environment="${repo_dir}/config/platform/development.env"
state_environment="${state_dir}/compose.env"
owner_password_file="${state_dir}/owner-password"
owner_hash_file="${state_dir}/secrets/personal-workspace/owner_password_hash"
ca_certificate="${state_dir}/tls/local-development-ca.cert.pem"
readonly wait_timeout_seconds=180

# shellcheck source=infra/scripts/common.sh
. "${script_dir}/common.sh"

compose() {
    docker compose \
        --project-name alittlemore-dev \
        --env-file "$platform_environment" \
        --env-file "$state_environment" \
        --file "${repo_dir}/docker-compose.yml" \
        --file "${repo_dir}/docker-compose.dev.yml" \
        "$@"
}

prepare_owner_password_hash() {
    local owner_password
    local password_hash
    local temporary_hash

    if [ -s "$owner_hash_file" ]; then
        return
    fi
    owner_password="$(cat "$owner_password_file")"
    export DEV_OWNER_PASSWORD="$owner_password"
    password_hash="$(
        docker run \
            --rm \
            --pull never \
            --env DEV_OWNER_PASSWORD \
            --entrypoint python \
            alittlemore-dev/personal-workspace-backend:local \
            -c 'import os; from argon2 import PasswordHasher; print(PasswordHasher().hash(os.environ["DEV_OWNER_PASSWORD"]))'
    )"
    unset DEV_OWNER_PASSWORD
    if [[ ! "$password_hash" =~ ^\$argon2id\$ ]]; then
        echo "The Personal Workspace image did not generate a valid Argon2id owner password hash." >&2
        exit 1
    fi
    temporary_hash="$(mktemp "${owner_hash_file}.tmp.XXXXXX")"
    printf '%s\n' "$password_hash" >"$temporary_hash"
    chmod 600 "$temporary_hash"
    mv -f "$temporary_hash" "$owner_hash_file"
}

prepare_minio_volume_permissions() {
    compose run \
        --rm \
        --no-deps \
        --pull never \
        --user 0:0 \
        --entrypoint sh \
        minio \
        -c 'mkdir -p /data && chown -R 10002:10002 /data'
}

compose_up_wait() {
    local pull_policy="${1:?pull policy is required}"
    shift

    compose up \
        --wait \
        --detach \
        --wait-timeout "$wait_timeout_seconds" \
        --no-build \
        --pull "$pull_policy" \
        "$@"
}

run_initializers() {
    compose run --rm --no-deps --pull never personal-workspace-backend-init
    compose run --rm --no-deps --pull never competency-backend-init
    compose run --rm --no-deps --pull never auth-api-backend-init
}

smoke_local_edge() {
    local check
    local hostname
    local path
    local attempt
    local -a checks=(
        "alittlemore.localhost|/healthz"
        "alittlemore.localhost|/ru/how-this-site-is-built"
        "alittlemore.localhost|/api/personal-workspace/healthcheck"
        "alittlemore.localhost|/api/competency/healthcheck"
        "alittlemore.localhost|/api/auth/healthcheck"
        "s3.localhost|/minio/health/live"
    )

    for check in "${checks[@]}"; do
        hostname="${check%%|*}"
        path="${check#*|}"
        for attempt in {1..30}; do
            if curl \
                --fail \
                --silent \
                --show-error \
                --output /dev/null \
                --max-time 5 \
                --noproxy '*' \
                --cacert "$ca_certificate" \
                --resolve "${hostname}:443:127.0.0.1" \
                "https://${hostname}${path}"; then
                break
            fi
            if [ "$attempt" -eq 30 ]; then
                echo "Local edge healthcheck failed: https://${hostname}${path}" >&2
                return 1
            fi
            sleep 1
        done
    done
}

require_docker_compose
require_command curl
require_command openssl
require_command python3
if ! docker info >/dev/null 2>&1; then
    echo "The Docker daemon is not available. Start Docker and repeat make dev." >&2
    exit 1
fi

python3 "${script_dir}/prepare_dev_state.py" \
    --repo-dir "$repo_dir" \
    --state-dir "$state_dir" \
    --personal-workspace-dir "$personal_workspace_dir" \
    --competency-trainer-dir "$competency_trainer_dir" \
    --auth-api-dir "$auth_api_dir" \
    --frontend-dir "$frontend_dir"
bash "${script_dir}/dev_tls.sh" verify

compose config --quiet
compose build \
    personal-workspace-backend-blue \
    competency-backend-blue \
    auth-api-backend-blue \
    frontend-blue \
    minio \
    nginx
prepare_owner_password_hash
prepare_minio_volume_permissions
compose_up_wait missing \
    --remove-orphans \
    personal-workspace-postgres \
    personal-workspace-valkey \
    competency-postgres \
    competency-valkey \
    auth-api-postgres \
    auth-api-valkey \
    minio \
    databasus
run_initializers
compose_up_wait never \
    --no-deps \
    --force-recreate \
    personal-workspace-backend-blue \
    personal-workspace-taskiq-worker-blue \
    personal-workspace-taskiq-scheduler-blue \
    competency-backend-blue \
    competency-taskiq-worker-blue \
    competency-taskiq-scheduler-blue \
    auth-api-backend-blue \
    auth-api-taskiq-worker-blue \
    auth-api-taskiq-scheduler-blue \
    frontend-blue \
    nginx
smoke_local_edge

printf '\nLocal integration stack is ready:\n'
printf '  Application edge: https://alittlemore.localhost\n'
printf '  Personal Workspace API: https://alittlemore.localhost/api/personal-workspace/\n'
printf '  Competency Trainer API: https://alittlemore.localhost/api/competency/\n'
printf '  Auth API: https://alittlemore.localhost/api/auth/\n'
printf '  MinIO API: https://s3.localhost\n\n'
cat "${state_dir}/credentials"
