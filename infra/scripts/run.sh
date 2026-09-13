#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd -- "${script_dir}/../.." && pwd)"
cd "$repo_dir"

# shellcheck source=infra/scripts/common.sh
. "$script_dir/common.sh"
# shellcheck source=infra/scripts/compose_secrets.sh
. "$script_dir/compose_secrets.sh"
# shellcheck source=infra/scripts/edge_checks.sh
. "$script_dir/edge_checks.sh"

readonly COMPOSE_WAIT_TIMEOUT_SECONDS=180
readonly DEPLOY_DRAIN_SECONDS=10
DEPLOY_STATE_DIR="$(runtime_state_directory)"
readonly DEPLOY_STATE_DIR
readonly ACTIVE_SLOT_FILE="${DEPLOY_STATE_DIR}/active-slot"
readonly MINIO_CREDENTIAL_FINGERPRINT_FILE="${DEPLOY_STATE_DIR}/minio-credentials.sha256"
readonly NGINX_IMAGE_REPOSITORY=alittlemore-infra/nginx
readonly APPLICATION_IMAGE_SERVICES=(
    personal-workspace-backend-init
    competency-backend-init
    auth-api-backend-init
    frontend-blue
)
readonly INFRASTRUCTURE_SERVICES=(
    personal-workspace-postgres
    personal-workspace-valkey
    competency-postgres
    auth-api-postgres
    competency-valkey
    auth-api-valkey
    minio
    databasus
)

other_slot() {
    local slot="$1"

    case "$slot" in
        blue) printf '%s\n' green ;;
        green) printf '%s\n' blue ;;
        *)
            echo "Unknown deploy slot: ${slot}" >&2
            exit 1
            ;;
    esac
}

read_active_slot() {
    local active_slot=""
    local committed_release=""
    local extra_field=""

    if [ ! -e "$ACTIVE_SLOT_FILE" ] && [ ! -L "$ACTIVE_SLOT_FILE" ]; then
        printf '%s\n' "${ACTIVE_DEPLOY_SLOT:-}"
        return
    fi
    if [ ! -f "$ACTIVE_SLOT_FILE" ] || [ -L "$ACTIVE_SLOT_FILE" ]; then
        echo "Active-slot marker must be a regular file." >&2
        exit 1
    fi
    if ! python3 "$script_dir/validate_private_file.py" "$ACTIVE_SLOT_FILE"; then
        echo "Active-slot marker must be owner-only and owned by the current user." >&2
        exit 1
    fi
    read -r active_slot committed_release extra_field <"$ACTIVE_SLOT_FILE"
    case "$active_slot" in
        blue | green) ;;
        *)
            echo "Active-slot marker contains an invalid slot." >&2
            exit 1
            ;;
    esac
    if [ -n "$extra_field" ] \
        || { [ -n "$committed_release" ] \
            && [[ ! "$committed_release" =~ ^release-[0-9]+-[0-9]+$ ]]; }; then
        echo "Active-slot marker contains invalid deployment metadata." >&2
        exit 1
    fi
    printf '%s\n' "$active_slot"
}

compose_up_wait() {
    docker compose up \
        --wait \
        --detach \
        --remove-orphans \
        --wait-timeout "$COMPOSE_WAIT_TIMEOUT_SECONDS" \
        "$@"
}

prepare_minio_volume_permissions() {
    docker compose build minio
    docker compose run \
        --rm \
        --no-deps \
        --user 0:0 \
        --entrypoint sh \
        minio \
        -c 'mkdir -p /data && chown -R 10002:10002 /data'
}

record_minio_credential_fingerprints() {
    local temporary_fingerprint_file

    if [ -e "$MINIO_CREDENTIAL_FINGERPRINT_FILE" ] \
        || [ -L "$MINIO_CREDENTIAL_FINGERPRINT_FILE" ]; then
        return
    fi
    temporary_fingerprint_file="$(mktemp "${MINIO_CREDENTIAL_FINGERPRINT_FILE}.tmp.XXXXXX")"
    if ! python3 "$script_dir/minio_credential_fingerprints.py" >"$temporary_fingerprint_file" \
        || ! chmod 600 "$temporary_fingerprint_file" \
        || ! mv -f "$temporary_fingerprint_file" "$MINIO_CREDENTIAL_FINGERPRINT_FILE"; then
        rm -f "$temporary_fingerprint_file"
        echo "Could not persist the MinIO credential fingerprint marker." >&2
        exit 1
    fi
}

pull_application_images() {
    docker compose pull --policy always "${APPLICATION_IMAGE_SERVICES[@]}"
}

run_backend_initializers() {
    docker compose run --pull never --rm personal-workspace-backend-init
    docker compose run --pull never --rm competency-backend-init
    docker compose run --pull never --rm auth-api-backend-init
}

sync_certificates() {
    prepare_certificate_mount_directory
    docker compose build cert-sync
    docker compose run --rm --pull never cert-sync
}

verify_restart_policy() {
    local service_name="$1"
    local expected_policy="$2"
    local container_id
    local actual_policy

    container_id="$(docker compose ps -q "$service_name")"
    if [ -z "$container_id" ]; then
        echo "Running container for ${service_name} could not be found." >&2
        return 1
    fi
    actual_policy="$(docker inspect --format '{{.HostConfig.RestartPolicy.Name}}' "$container_id")"
    if [ "$actual_policy" != "$expected_policy" ]; then
        echo "Unexpected restart policy for ${service_name}: expected ${expected_policy}, got ${actual_policy}." >&2
        return 1
    fi
}

verify_runtime_restart_policies() {
    local service_name
    local services=(
        "personal-workspace-backend-${target_slot}"
        "personal-workspace-taskiq-worker-${target_slot}"
        "personal-workspace-taskiq-scheduler-${target_slot}"
        "competency-backend-${target_slot}"
        "auth-api-backend-${target_slot}"
        "competency-taskiq-worker-${target_slot}"
        "auth-api-taskiq-worker-${target_slot}"
        "competency-taskiq-scheduler-${target_slot}"
        "auth-api-taskiq-scheduler-${target_slot}"
        "frontend-${target_slot}"
        "${INFRASTRUCTURE_SERVICES[@]}"
    )

    for service_name in "${services[@]}"; do
        verify_restart_policy "$service_name" unless-stopped || return 1
    done
    verify_restart_policy nginx always || return 1
}

build_and_validate_candidate_edge() {
    export NGINX_IMAGE="${NGINX_IMAGE_REPOSITORY}:${target_slot}"
    docker compose build nginx
    docker compose run \
        --rm \
        --no-deps \
        --pull never \
        nginx \
        /usr/local/bin/alittlemore-nginx-entrypoint \
        test
}

start_target_background_processes() {
    if [ -n "$previous_slot" ]; then
        docker compose stop \
            "personal-workspace-taskiq-scheduler-${previous_slot}" \
            "competency-taskiq-scheduler-${previous_slot}" \
            "auth-api-taskiq-scheduler-${previous_slot}" || return 1
    fi
    compose_up_wait --no-build --pull never --force-recreate \
        "personal-workspace-taskiq-worker-${target_slot}" \
        "personal-workspace-taskiq-scheduler-${target_slot}" \
        "competency-taskiq-worker-${target_slot}" \
        "auth-api-taskiq-worker-${target_slot}" \
        "competency-taskiq-scheduler-${target_slot}" \
        "auth-api-taskiq-scheduler-${target_slot}" || return 1
}

restore_previous_background_processes() {
    docker compose stop \
        "personal-workspace-taskiq-scheduler-${target_slot}" \
        "competency-taskiq-scheduler-${target_slot}" \
        "auth-api-taskiq-scheduler-${target_slot}" || {
        echo "Could not stop target schedulers; refusing to start the previous schedulers." >&2
        return 1
    }
    docker compose stop \
        "personal-workspace-taskiq-worker-${target_slot}" \
        "competency-taskiq-worker-${target_slot}" \
        "auth-api-taskiq-worker-${target_slot}" || \
        echo "Target workers could not be fully stopped during rollback." >&2

    if [ -z "$previous_slot" ]; then
        return
    fi
    docker compose start \
        "personal-workspace-taskiq-worker-${previous_slot}" \
        "personal-workspace-taskiq-scheduler-${previous_slot}" \
        "competency-taskiq-worker-${previous_slot}" \
        "auth-api-taskiq-worker-${previous_slot}" \
        "competency-taskiq-scheduler-${previous_slot}" \
        "auth-api-taskiq-scheduler-${previous_slot}" || return 1
}

save_active_slot() {
    local temporary_slot_file="${DEPLOY_STATE_DIR}/.active-slot.$$"
    local release_id="${ALITTLEMORE_RELEASE_ID:-}"

    mkdir -p "$DEPLOY_STATE_DIR" || return 1
    if [ -e "$temporary_slot_file" ] || [ -L "$temporary_slot_file" ]; then
        echo "Temporary active-slot path already exists." >&2
        return 1
    fi
    if [ -n "$release_id" ] && [[ ! "$release_id" =~ ^release-[0-9]+-[0-9]+$ ]]; then
        echo "ALITTLEMORE_RELEASE_ID contains an invalid release identifier." >&2
        return 1
    fi
    if [ -n "$release_id" ]; then
        printf '%s %s\n' "$1" "$release_id" >"$temporary_slot_file" || return 1
    else
        printf '%s\n' "$1" >"$temporary_slot_file" || return 1
    fi
    chmod 600 "$temporary_slot_file" || return 1
    if { [ -e "$ACTIVE_SLOT_FILE" ] || [ -L "$ACTIVE_SLOT_FILE" ]; } \
        && { [ ! -f "$ACTIVE_SLOT_FILE" ] || [ -L "$ACTIVE_SLOT_FILE" ]; }; then
        echo "Active-slot destination is not a regular file." >&2
        return 1
    fi
    mv -f "$temporary_slot_file" "$ACTIVE_SLOT_FILE" || return 1
}

runtime_commit_is_recorded() {
    local active_slot=""
    local active_release=""
    local extra_field=""
    local expected_release="${ALITTLEMORE_RELEASE_ID:-}"

    [ -f "$ACTIVE_SLOT_FILE" ] || return 1
    [ ! -L "$ACTIVE_SLOT_FILE" ] || return 1
    python3 "$script_dir/validate_private_file.py" "$ACTIVE_SLOT_FILE" >/dev/null || return 1
    read -r active_slot active_release extra_field <"$ACTIVE_SLOT_FILE"
    [ "$active_slot" = "$target_slot" ] || return 1
    [ -z "$extra_field" ] || return 1
    [ "$active_release" = "$expected_release" ]
}

stop_previous_slot() {
    local previous="$1"

    if [ -z "$previous" ]; then
        return
    fi
    sleep "$DEPLOY_DRAIN_SECONDS"
    if ! docker compose stop \
        "personal-workspace-backend-${previous}" \
        "personal-workspace-taskiq-worker-${previous}" \
        "personal-workspace-taskiq-scheduler-${previous}" \
        "competency-backend-${previous}" \
        "auth-api-backend-${previous}" \
        "competency-taskiq-worker-${previous}" \
        "auth-api-taskiq-worker-${previous}" \
        "competency-taskiq-scheduler-${previous}" \
        "auth-api-taskiq-scheduler-${previous}" \
        "frontend-${previous}"; then
        echo "The new slot is active, but the previous slot could not be fully stopped." >&2
        return
    fi
}

activate_and_verify_edge() {
    edge_replaced=true
    compose_up_wait --no-build --pull never --force-recreate nginx || return 1
    verify_runtime_restart_policies || return 1
    verify_served_edge_certificates "$repo_dir" || return 1
    smoke_edge_applications || return 1
}

restore_previous_edge() {
    if [ -z "$previous_slot" ]; then
        echo "The first deployment failed after edge activation; stopping the unverified public edge." >&2
        docker compose stop nginx || return 1
        docker compose stop \
            "personal-workspace-backend-${target_slot}" \
            "competency-backend-${target_slot}" \
            "auth-api-backend-${target_slot}" \
            "frontend-${target_slot}" || \
            echo "Some first-deployment target application containers could not be stopped." >&2
        return
    fi

    export PERSONAL_WORKSPACE_ACTIVE_BACKEND="personal-workspace-backend-${previous_slot}"
    export COMPETENCY_ACTIVE_BACKEND="competency-backend-${previous_slot}"
    export AUTH_API_ACTIVE_BACKEND="auth-api-backend-${previous_slot}"
    export FRONTEND_ACTIVE="frontend-${previous_slot}"
    export NGINX_IMAGE="${NGINX_IMAGE_REPOSITORY}:${previous_slot}"

    if ! compose_up_wait --no-build --pull never --force-recreate nginx; then
        echo "Deployment failed and nginx rollback to slot ${previous_slot} also failed." >&2
        return 1
    fi
    echo "Deployment failed; nginx was restored to slot ${previous_slot}." >&2
}

handle_edge_interruption() {
    trap - HUP INT TERM
    if runtime_commit_is_recorded; then
        echo "Deployment was interrupted after its runtime commit; leaving the verified target slot active." >&2
        exit 130
    fi
    echo "Deployment interrupted during activation; attempting to restore the previous slot." >&2
    restore_previous_background_processes || true
    if [ "$edge_replaced" = true ]; then
        restore_previous_edge || true
    fi
    exit 130
}

require_docker_compose
require_command curl
require_command openssl

acquire_runtime_lock
load_environment
previous_slot="$(read_active_slot)"
if [ -z "$previous_slot" ]; then
    target_slot=blue
else
    target_slot="$(other_slot "$previous_slot")"
fi
readonly target_slot
prepare_compose_secret_files "$target_slot"

export PERSONAL_WORKSPACE_ACTIVE_BACKEND="personal-workspace-backend-${target_slot}"
export COMPETENCY_ACTIVE_BACKEND="competency-backend-${target_slot}"
export AUTH_API_ACTIVE_BACKEND="auth-api-backend-${target_slot}"
export FRONTEND_ACTIVE="frontend-${target_slot}"

pull_application_images
prepare_minio_volume_permissions
compose_up_wait --build "${INFRASTRUCTURE_SERVICES[@]}"
record_minio_credential_fingerprints
run_backend_initializers
compose_up_wait --no-build --pull never --force-recreate \
    "$PERSONAL_WORKSPACE_ACTIVE_BACKEND" \
    "$COMPETENCY_ACTIVE_BACKEND" \
    "$AUTH_API_ACTIVE_BACKEND" \
    "$FRONTEND_ACTIVE"
sync_certificates
build_and_validate_candidate_edge
edge_replaced=false
trap handle_edge_interruption HUP INT TERM
if ! start_target_background_processes; then
    trap - HUP INT TERM
    restore_previous_background_processes || true
    exit 1
fi
if ! activate_and_verify_edge; then
    trap - HUP INT TERM
    restore_previous_background_processes || true
    restore_previous_edge || true
    exit 1
fi
if ! save_active_slot "$target_slot"; then
    trap - HUP INT TERM
    echo "Could not atomically record the active slot; restoring the previous deployment." >&2
    restore_previous_background_processes || true
    restore_previous_edge || true
    exit 1
fi
trap - HUP INT TERM
stop_previous_slot "$previous_slot"

echo "Unified deployment slot ${target_slot} is active for all applications."
