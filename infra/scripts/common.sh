#!/usr/bin/env bash

readonly REQUIRED_RUNTIME_ENVIRONMENT_VARIABLES=(
    IMAGE_REGISTRY
    APP_DOMAIN
    MINIO_DOMAIN
    APP_URL_SCHEMA
    LE_EMAIL
    TLS_CERTIFICATE_NAME
    SSL_CERT
    SSL_KEY
    VPN_BIND_ADDRESS
    MINIO_REGION
    MINIO_CORS_MAX_AGE_SECONDS
    PERSONAL_WORKSPACE_DB_USER
    PERSONAL_WORKSPACE_DB_NAME
    COMPETENCY_DB_USER
    COMPETENCY_DB_NAME
    SOPS_AGE_KEY_FILE
)

require_command() {
    local command_name="$1"

    if ! command -v "$command_name" >/dev/null 2>&1; then
        echo "${command_name} could not be found. Install it." >&2
        exit 1
    fi
}

require_env() {
    local variable_name="$1"

    if [ -z "${!variable_name:-}" ]; then
        echo "${variable_name} must be set and non-empty in generated runtime config." >&2
        exit 1
    fi
}

is_safe_absolute_path() {
    local path="$1"

    [[ "$path" =~ ^/[A-Za-z0-9._/-]+$ ]] \
        && [ "$path" != "/" ] \
        && [[ "$path" != *"//"* ]] \
        && case "/${path#/}/" in
            */./* | */../*) false ;;
            *) true ;;
        esac
}

is_dns_hostname() {
    local hostname="$1"

    [ "${#hostname}" -le 253 ] \
        && [[ "$hostname" =~ ^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$ ]]
}

require_distinct_values() {
    local description="$1"
    shift
    local -a values=("$@")
    local first_index
    local second_index

    for ((first_index = 0; first_index < ${#values[@]}; first_index++)); do
        for ((second_index = first_index + 1; second_index < ${#values[@]}; second_index++)); do
            if [ "${values[$first_index]}" = "${values[$second_index]}" ]; then
                echo "${description} must contain distinct values." >&2
                exit 1
            fi
        done
    done
}

verify_i18n_ssr_transfer_state() {
    local rendered_page="$1"
    local language="$2"
    local bundle
    local state_key

    for bundle in shared how-this-site-is-built; do
        state_key="i18n.bundle.${bundle}.${language}"
        if ! grep -Fq "$state_key" "$rendered_page"; then
            echo "SSR response is missing i18n TransferState for ${bundle}/${language}." >&2
            return 1
        fi
    done
}

runtime_state_directory() {
    local marker_file="${repo_dir}/.alittlemore-runtime-root"
    local runtime_root
    local sentinel

    if [ ! -e "$marker_file" ] && [ ! -L "$marker_file" ]; then
        printf '%s\n' "${repo_dir}/.deploy-state"
        return
    fi
    if [ -L "$marker_file" ] || [ ! -f "$marker_file" ]; then
        echo "Deployment runtime-root marker must be a regular file." >&2
        exit 1
    fi
    if [ ! -O "$marker_file" ] || [ "$(stat -c '%a' "$marker_file")" != "600" ]; then
        echo "Deployment runtime-root marker must be owner-only and owned by the current user." >&2
        exit 1
    fi
    IFS= read -r runtime_root <"$marker_file"
    if ! is_safe_absolute_path "$runtime_root"; then
        echo "Deployment runtime-root marker contains an invalid path." >&2
        exit 1
    fi
    if [ ! -d "$runtime_root" ] || [ -L "$runtime_root" ] \
        || [ "$(readlink -f "$runtime_root")" != "$runtime_root" ]; then
        echo "Deployment runtime root must be a real canonical directory." >&2
        exit 1
    fi
    sentinel="${runtime_root}/.alittlemore-infra-deploy-root"
    if [ ! -f "$sentinel" ] || [ -L "$sentinel" ] \
        || [ "$(cat "$sentinel")" != "alittlemore-infra" ]; then
        echo "Deployment runtime root is missing its sentinel." >&2
        exit 1
    fi
    if [ "$(stat -c '%U' "$runtime_root")" != "$(id -un)" ] \
        || [ "$(stat -c '%a' "$runtime_root")" != "700" ] \
        || [ "$(stat -c '%U' "$sentinel")" != "$(id -un)" ] \
        || [ "$(stat -c '%a' "$sentinel")" != "600" ]; then
        echo "Deployment runtime root or sentinel has unsafe ownership or permissions." >&2
        exit 1
    fi
    printf '%s\n' "${runtime_root}/.deploy-state"
}

prepare_runtime_state_directory() {
    local state_directory

    state_directory="$(runtime_state_directory)"
    if [ -L "$state_directory" ] || { [ -e "$state_directory" ] && [ ! -d "$state_directory" ]; }; then
        echo "Runtime state path must be a real directory: ${state_directory}." >&2
        exit 1
    fi
    mkdir -p "$state_directory"
    if [ ! -O "$state_directory" ]; then
        echo "Runtime state directory must be owned by the current user." >&2
        exit 1
    fi
    chmod 700 "$state_directory"
    printf '%s\n' "$state_directory"
}

prepare_certificate_mount_directory() {
    local certificate_directory="${repo_dir}/infra/nginx/certs"
    local expected_directory
    local state_directory

    if [ -e "${repo_dir}/.alittlemore-runtime-root" ] \
        || [ -L "${repo_dir}/.alittlemore-runtime-root" ]; then
        state_directory="$(runtime_state_directory)"
        expected_directory="${state_directory%/.deploy-state}/certificates"
        if [ ! -L "$certificate_directory" ] \
            || [ "$(readlink -f "$certificate_directory")" != "$expected_directory" ]; then
            echo "Deployed certificate mount must point to ${expected_directory}." >&2
            return 1
        fi
        certificate_directory="$expected_directory"
    else
        if [ -L "$certificate_directory" ]; then
            echo "Local certificate mount must not be a symlink." >&2
            return 1
        fi
        mkdir -p "$certificate_directory"
    fi

    if [ ! -d "$certificate_directory" ] || [ -L "$certificate_directory" ] \
        || [ ! -O "$certificate_directory" ]; then
        echo "Certificate mount must be a real directory owned by the current user." >&2
        return 1
    fi
    chmod 751 "$certificate_directory"
}

require_docker_compose() {
    local compose_version
    local normalized_version
    local major
    local minor
    local _patch

    require_command docker
    compose_version="$(docker compose version --short 2>/dev/null)" || {
        echo "Docker Compose v2.24.0 or newer could not be found. Install or upgrade the plugin." >&2
        exit 1
    }
    normalized_version="${compose_version#v}"
    normalized_version="${normalized_version%%-*}"
    if [[ ! "$normalized_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        echo "Could not parse Docker Compose version: ${compose_version}." >&2
        exit 1
    fi
    IFS=. read -r major minor _patch <<<"$normalized_version"
    if ((major < 2 || (major == 2 && minor < 24))); then
        echo "Docker Compose v2.24.0 or newer is required; found ${compose_version}." >&2
        exit 1
    fi
}

acquire_runtime_lock() {
    local state_directory
    local lock_file

    state_directory="$(prepare_runtime_state_directory)"
    lock_file="${state_directory}/runtime.lock"

    require_command flock
    if [ -L "$lock_file" ] || { [ -e "$lock_file" ] && [ ! -f "$lock_file" ]; }; then
        echo "Runtime lock path must be a regular file: ${lock_file}." >&2
        exit 1
    fi
    if [ "${ALITTLEMORE_RUNTIME_LOCK_HELD:-}" = "1" ]; then
        if flock -n "$lock_file" true; then
            echo "ALITTLEMORE_RUNTIME_LOCK_HELD is set, but no parent transaction owns the lock." >&2
            exit 1
        fi
        return
    fi
    exec 9>"$lock_file"
    chmod 600 "$lock_file"
    if ! flock -n 9; then
        echo "Another infrastructure mutation is already running for ${repo_dir}." >&2
        exit 1
    fi
    export ALITTLEMORE_RUNTIME_LOCK_HELD=1
}

pin_compose_identity() {
    export COMPOSE_PROJECT_NAME=alittlemore-infra
    export COMPOSE_DISABLE_ENV_FILE=1
    unset COMPOSE_FILE COMPOSE_PROFILES COMPOSE_ENV_FILES
}

load_environment() {
    if [ -z "${repo_dir:-}" ]; then
        echo "repo_dir must be set before sourcing common.sh." >&2
        exit 1
    fi
    local runtime_environment_file
    local state_directory
    local variable_name

    require_command python3
    state_directory="$(prepare_runtime_state_directory)"
    runtime_environment_file="${state_directory}/runtime.env"
    python3 "${repo_dir}/infra/scripts/render_runtime_config.py" \
        --manifest "${repo_dir}/infra/deploy/runtime-config.manifest.json" \
        --repo-dir "$repo_dir" \
        --output "$runtime_environment_file"
    if ! python3 "${repo_dir}/infra/scripts/validate_private_file.py" "$runtime_environment_file"; then
        echo "Generated runtime environment must be owner-only and owned by the deploy user." >&2
        exit 1
    fi

    set -a
    # shellcheck disable=SC1090
    . "$runtime_environment_file"
    set +a

    for variable_name in "${REQUIRED_RUNTIME_ENVIRONMENT_VARIABLES[@]}"; do
        require_env "$variable_name"
    done

    if [[ ! "$IMAGE_REGISTRY" =~ ^[A-Za-z0-9][A-Za-z0-9._:-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)*$ ]]; then
        echo "IMAGE_REGISTRY must be a registry/repository prefix without a scheme, tag, or trailing slash." >&2
        exit 1
    fi
    local domain_name
    for domain_name in \
        "$APP_DOMAIN" \
        "$MINIO_DOMAIN"; do
        if ! is_dns_hostname "$domain_name"; then
            echo "Public application domains must be valid DNS hostnames." >&2
            exit 1
        fi
    done
    require_distinct_values \
        "Public application domains" \
        "$APP_DOMAIN" \
        "$MINIO_DOMAIN"
    if [ "$APP_URL_SCHEMA" != "https" ]; then
        echo "APP_URL_SCHEMA must be https for this production-oriented stack." >&2
        exit 1
    fi
    if [[ ! "$TLS_CERTIFICATE_NAME" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
        echo "TLS_CERTIFICATE_NAME contains unsupported characters." >&2
        exit 1
    fi
    if ! is_safe_absolute_path "$SSL_CERT" \
        || ! is_safe_absolute_path "$SSL_KEY" \
        || [ "$SSL_CERT" = "$SSL_KEY" ]; then
        echo "SSL_CERT and SSL_KEY must be distinct normalized absolute paths." >&2
        exit 1
    fi
    if ! is_safe_absolute_path "$SOPS_AGE_KEY_FILE"; then
        echo "SOPS_AGE_KEY_FILE must be a normalized absolute path outside deployment releases." >&2
        exit 1
    fi
    python3 "${repo_dir}/infra/scripts/validate_network.py" "$VPN_BIND_ADDRESS"
    pin_compose_identity
}
