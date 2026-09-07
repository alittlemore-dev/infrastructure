#!/usr/bin/env bash

readonly REQUIRED_ENVIRONMENT_VARIABLES=(
    IMAGE_REGISTRY
    PERSONAL_WORKSPACE_DOMAIN
    COMPETENCY_DOMAIN
    MINIO_DOMAIN
    APP_URL_SCHEMA
    LE_EMAIL
    TLS_CERTIFICATE_NAME
    SSL_CERT
    SSL_KEY
    VPN_BIND_ADDRESS
    MINIO_ROOT_ACCESS_KEY
    MINIO_ROOT_SECRET_KEY
    MINIO_REGION
    MINIO_CORS_MAX_AGE_SECONDS
    DATABASUS_MINIO_ACCESS_KEY
    DATABASUS_MINIO_SECRET_KEY
    PERSONAL_WORKSPACE_APP_DEBUG
    PERSONAL_WORKSPACE_APP_SECRET_KEY
    PERSONAL_WORKSPACE_APP_USE_CACHE
    PERSONAL_WORKSPACE_AUTH_SESSION_TTL_SECONDS
    PERSONAL_WORKSPACE_FILES_ORPHAN_RETENTION_SECONDS
    PERSONAL_WORKSPACE_I18N_DEFAULT_LANGUAGE
    PERSONAL_WORKSPACE_OWNER_USERNAME
    PERSONAL_WORKSPACE_OWNER_PASSWORD_HASH
    PERSONAL_WORKSPACE_SENTRY_USE
    PERSONAL_WORKSPACE_TASKIQ_CACHE_WARM_INTERVAL_SECONDS
    PERSONAL_WORKSPACE_TASKIQ_FILE_ORPHAN_PRUNE_INTERVAL_SECONDS
    PERSONAL_WORKSPACE_TASKIQ_RESULT_EXPIRE_SECONDS
    PERSONAL_WORKSPACE_DB_USER
    PERSONAL_WORKSPACE_DB_PASSWORD
    PERSONAL_WORKSPACE_DB_DRIVER
    PERSONAL_WORKSPACE_DB_NAME
    PERSONAL_WORKSPACE_DB_POOL_PRE_PING
    PERSONAL_WORKSPACE_DB_POOL_SIZE
    PERSONAL_WORKSPACE_DB_MAX_OVERFLOW
    PERSONAL_WORKSPACE_DB_EXPIRE_ON_COMMIT
    PERSONAL_WORKSPACE_DB_LOG_QUERY_METRICS
    PERSONAL_WORKSPACE_DB_SLOW_QUERY_LOG_THRESHOLD_MS
    PERSONAL_WORKSPACE_DB_SLOW_QUERY_LOG_STATEMENT_MAX_LENGTH
    PERSONAL_WORKSPACE_MINIO_SECRET_KEY
    PERSONAL_WORKSPACE_MINIO_ACCESS_KEY
    COMPETENCY_APP_CONTACT_REQUESTS_ENABLED
    COMPETENCY_APP_DEBUG
    COMPETENCY_APP_SECRET_KEY
    COMPETENCY_APP_USE_CACHE
    COMPETENCY_AUTH_PUBLIC_KEY
    COMPETENCY_AUTH_PRIVATE_KEY
    COMPETENCY_AUTH_TOKEN_EXPIRE_SECONDS
    COMPETENCY_AUTH_SESSION_EXPIRE_SECONDS
    COMPETENCY_AUTH_SESSION_ABSOLUTE_EXPIRE_SECONDS
    COMPETENCY_AUTH_TOKEN_HEADER_NAME
    COMPETENCY_AUTH_TOKEN_PREFIX
    COMPETENCY_CACHE_WARM_ARTICLES_PAGE_SIZE
    COMPETENCY_MATRIX_QUESTION_SUGGESTION_ANONYMOUS_DAILY_LIMIT
    COMPETENCY_FILES_ORPHAN_RETENTION_SECONDS
    COMPETENCY_I18N_DEFAULT_LANGUAGE
    COMPETENCY_OWNER_INIT_LOGIN
    COMPETENCY_OWNER_INIT_PASSWORD
    COMPETENCY_SENTRY_USE
    COMPETENCY_TASKIQ_AUTH_SESSION_PRUNE_INTERVAL_SECONDS
    COMPETENCY_TASKIQ_AGENT_AUDIT_PRUNE_INTERVAL_SECONDS
    COMPETENCY_TASKIQ_CACHE_WARM_INTERVAL_SECONDS
    COMPETENCY_TASKIQ_FILE_ORPHAN_PRUNE_INTERVAL_SECONDS
    COMPETENCY_TASKIQ_RESULT_EXPIRE_SECONDS
    COMPETENCY_DB_USER
    COMPETENCY_DB_PASSWORD
    COMPETENCY_DB_DRIVER
    COMPETENCY_DB_NAME
    COMPETENCY_DB_POOL_PRE_PING
    COMPETENCY_DB_POOL_SIZE
    COMPETENCY_DB_MAX_OVERFLOW
    COMPETENCY_DB_EXPIRE_ON_COMMIT
    COMPETENCY_DB_LOG_QUERY_METRICS
    COMPETENCY_DB_SLOW_QUERY_LOG_THRESHOLD_MS
    COMPETENCY_DB_SLOW_QUERY_LOG_STATEMENT_MAX_LENGTH
    COMPETENCY_MINIO_SECRET_KEY
    COMPETENCY_MINIO_ACCESS_KEY
    COMPETENCY_AGENT_ACCESS_ISSUING_CERTIFICATE
    COMPETENCY_AGENT_ACCESS_ISSUING_PRIVATE_KEY
    COMPETENCY_AGENT_ACCESS_CERTIFICATE_CHAIN
)

readonly ALLOW_EMPTY_ENVIRONMENT_VARIABLES=(
    PERSONAL_WORKSPACE_SENTRY_DSN
    COMPETENCY_SENTRY_DSN
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
        echo "${variable_name} must be set and non-empty in .env." >&2
        exit 1
    fi
}

require_env_set() {
    local variable_name="$1"

    if [ "${!variable_name+x}" != "x" ]; then
        echo "${variable_name} must be present in .env; an empty value is allowed." >&2
        exit 1
    fi
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
    if [[ ! "$runtime_root" =~ ^/[A-Za-z0-9._/-]+/alittlemore-infra$ ]] \
        || [[ "$runtime_root" == *"//"* ]]; then
        echo "Deployment runtime-root marker contains an invalid path." >&2
        exit 1
    fi
    case "$runtime_root" in
        /*/alittlemore-infra) ;;
        *)
            echo "Deployment runtime-root marker contains an invalid path." >&2
            exit 1
            ;;
    esac
    case "/${runtime_root#/}/" in
        */./* | */../*)
            echo "Deployment runtime-root marker contains an invalid path." >&2
            exit 1
            ;;
    esac
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

    state_directory="$(runtime_state_directory)"
    lock_file="${state_directory}/runtime.lock"

    require_command flock
    if [ -L "$state_directory" ] || { [ -e "$state_directory" ] && [ ! -d "$state_directory" ]; }; then
        echo "Runtime state path must be a real directory: ${state_directory}." >&2
        exit 1
    fi
    mkdir -p "$state_directory"
    chmod 700 "$state_directory"
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
    unset COMPOSE_FILE COMPOSE_PROFILES
}

load_environment() {
    if [ -z "${repo_dir:-}" ]; then
        echo "repo_dir must be set before sourcing common.sh." >&2
        exit 1
    fi
    require_command python3
    if [ ! -e "${repo_dir}/.env" ]; then
        echo ".env file could not be found. Install .env.example as an owner-only .env and set every value." >&2
        exit 1
    fi
    if ! python3 "${repo_dir}/infra/scripts/validate_private_file.py" "${repo_dir}/.env"; then
        echo ".env must be an owner-only regular file owned by the deploy user." >&2
        exit 1
    fi

    set -a
    # shellcheck disable=SC1091
    . "${repo_dir}/.env"
    set +a

    local variable_name
    for variable_name in "${REQUIRED_ENVIRONMENT_VARIABLES[@]}"; do
        require_env "$variable_name"
    done
    for variable_name in "${ALLOW_EMPTY_ENVIRONMENT_VARIABLES[@]}"; do
        require_env_set "$variable_name"
    done

    if [[ ! "$IMAGE_REGISTRY" =~ ^[A-Za-z0-9][A-Za-z0-9._:-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)*$ ]]; then
        echo "IMAGE_REGISTRY must be a registry/repository prefix without a scheme, tag, or trailing slash." >&2
        exit 1
    fi
    if [ "$PERSONAL_WORKSPACE_DOMAIN" != "personal-workspace.alittlemore.dev" ]; then
        echo "PERSONAL_WORKSPACE_DOMAIN must be personal-workspace.alittlemore.dev." >&2
        exit 1
    fi
    if [ "$COMPETENCY_DOMAIN" != "competency.alittlemore.dev" ]; then
        echo "COMPETENCY_DOMAIN must be competency.alittlemore.dev." >&2
        exit 1
    fi
    if [ "$MINIO_DOMAIN" != "s3.alittlemore.dev" ]; then
        echo "MINIO_DOMAIN must be s3.alittlemore.dev." >&2
        exit 1
    fi
    if [ "$APP_URL_SCHEMA" != "https" ]; then
        echo "APP_URL_SCHEMA must be https for this production-oriented stack." >&2
        exit 1
    fi
    if [[ ! "$TLS_CERTIFICATE_NAME" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
        echo "TLS_CERTIFICATE_NAME contains unsupported characters." >&2
        exit 1
    fi
    if [ "$SSL_CERT" != "/certs/current/fullchain.pem" ] \
        || [ "$SSL_KEY" != "/certs/current/privkey.pem" ]; then
        echo "SSL_CERT and SSL_KEY must use the managed /certs/current certificate paths." >&2
        exit 1
    fi
    if [ "$MINIO_ROOT_ACCESS_KEY" != "alittlemore-infra-admin" ] \
        || [ "$PERSONAL_WORKSPACE_MINIO_ACCESS_KEY" != "personal-workspace" ] \
        || [ "$COMPETENCY_MINIO_ACCESS_KEY" != "competency-trainer" ] \
        || [ "$DATABASUS_MINIO_ACCESS_KEY" != "databasus" ]; then
        echo "MinIO access-key identities must keep the fixed values declared in .env.example." >&2
        exit 1
    fi
    local minio_secret_variable_name
    local minio_secret_value
    for minio_secret_variable_name in \
        MINIO_ROOT_SECRET_KEY \
        PERSONAL_WORKSPACE_MINIO_SECRET_KEY \
        COMPETENCY_MINIO_SECRET_KEY \
        DATABASUS_MINIO_SECRET_KEY; do
        minio_secret_value="${!minio_secret_variable_name}"
        if [ "${#minio_secret_value}" -lt 8 ]; then
            echo "${minio_secret_variable_name} must contain at least eight characters." >&2
            exit 1
        fi
    done
    if [ "$MINIO_ROOT_SECRET_KEY" = "$PERSONAL_WORKSPACE_MINIO_SECRET_KEY" ] \
        || [ "$MINIO_ROOT_SECRET_KEY" = "$COMPETENCY_MINIO_SECRET_KEY" ] \
        || [ "$MINIO_ROOT_SECRET_KEY" = "$DATABASUS_MINIO_SECRET_KEY" ] \
        || [ "$PERSONAL_WORKSPACE_MINIO_SECRET_KEY" = "$COMPETENCY_MINIO_SECRET_KEY" ] \
        || [ "$PERSONAL_WORKSPACE_MINIO_SECRET_KEY" = "$DATABASUS_MINIO_SECRET_KEY" ] \
        || [ "$COMPETENCY_MINIO_SECRET_KEY" = "$DATABASUS_MINIO_SECRET_KEY" ]; then
        echo "MinIO secret keys must all be different." >&2
        exit 1
    fi
    python3 "${repo_dir}/infra/scripts/validate_network.py" "$VPN_BIND_ADDRESS"
    pin_compose_identity
}
