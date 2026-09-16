#!/usr/bin/env bash

COMPOSE_SECRET_FILE_VARIABLES=()

load_compose_secret_file_variables() {
    if [ -z "${repo_dir:-}" ]; then
        echo "repo_dir must be set before loading Compose secret variables." >&2
        return 1
    fi
    local variables_output
    local variable_name
    local existing_name

    if ! variables_output="$(
        python3 "${repo_dir}/infra/scripts/list_compose_secret_variables.py" \
            "${repo_dir}/infra/deploy/runtime-secrets.manifest.json"
    )"; then
        return 1
    fi
    COMPOSE_SECRET_FILE_VARIABLES=()
    while IFS= read -r variable_name; do
        if [[ ! "$variable_name" =~ ^[A-Z][A-Z0-9_]*$ ]]; then
            echo "Manifest contains an invalid Compose secret variable: ${variable_name}." >&2
            return 1
        fi
        for existing_name in "${COMPOSE_SECRET_FILE_VARIABLES[@]}"; do
            if [ "$existing_name" = "$variable_name" ]; then
                echo "Manifest repeats Compose secret variable: ${variable_name}." >&2
                return 1
            fi
        done
        COMPOSE_SECRET_FILE_VARIABLES+=("$variable_name")
    done <<<"$variables_output"
    if [ "${#COMPOSE_SECRET_FILE_VARIABLES[@]}" -eq 0 ]; then
        echo "Secret manifest does not declare Compose secret variables." >&2
        return 1
    fi
}

fail_invalid_secret() {
    local secret_name="$1"
    echo "${secret_name} is not valid deployment PKI material." >&2
    return 1
}

extract_chain_certificate() {
    local chain_file="$1"
    local certificate_number="$2"
    local output_file="$3"

    awk -v wanted="$certificate_number" '
        /-----BEGIN CERTIFICATE-----/ { current += 1 }
        current == wanted { print }
        current == wanted && /-----END CERTIFICATE-----/ { exit }
    ' "$chain_file" >"$output_file"
}

validate_minio_credentials() {
    local root_access_key
    local root_secret_key
    local personal_access_key
    local personal_secret_key
    local competency_access_key
    local competency_secret_key
    local databasus_access_key
    local databasus_secret_key
    local secret_key

    root_access_key="$(cat "$COMPOSE_MINIO_ROOT_ACCESS_KEY_FILE")"
    root_secret_key="$(cat "$COMPOSE_MINIO_ROOT_SECRET_KEY_FILE")"
    personal_access_key="$(cat "$COMPOSE_PERSONAL_WORKSPACE_MINIO_ACCESS_KEY_FILE")"
    personal_secret_key="$(cat "$COMPOSE_PERSONAL_WORKSPACE_MINIO_SECRET_KEY_FILE")"
    competency_access_key="$(cat "$COMPOSE_COMPETENCY_MINIO_ACCESS_KEY_FILE")"
    competency_secret_key="$(cat "$COMPOSE_COMPETENCY_MINIO_SECRET_KEY_FILE")"
    databasus_access_key="$(cat "$COMPOSE_DATABASUS_MINIO_ACCESS_KEY_FILE")"
    databasus_secret_key="$(cat "$COMPOSE_DATABASUS_MINIO_SECRET_KEY_FILE")"

    require_distinct_values \
        "MinIO access-key identities" \
        "$root_access_key" \
        "$personal_access_key" \
        "$competency_access_key" \
        "$databasus_access_key"
    for secret_key in \
        "$root_secret_key" \
        "$personal_secret_key" \
        "$competency_secret_key" \
        "$databasus_secret_key"; do
        if [ "${#secret_key}" -lt 8 ]; then
            echo "Every MinIO secret key must contain at least eight characters." >&2
            return 1
        fi
    done
    if [ "$root_secret_key" = "$personal_secret_key" ] \
        || [ "$root_secret_key" = "$competency_secret_key" ] \
        || [ "$root_secret_key" = "$databasus_secret_key" ] \
        || [ "$personal_secret_key" = "$competency_secret_key" ] \
        || [ "$personal_secret_key" = "$databasus_secret_key" ] \
        || [ "$competency_secret_key" = "$databasus_secret_key" ]; then
        echo "MinIO secret keys must all be different." >&2
        return 1
    fi
}

validate_auth_api_pki() {
    local auth_dir="$1/auth-api"
    local declared_public
    local derived_public
    local public_description
    local openssl_binary

    openssl_binary="$(python3 "${BASH_SOURCE[0]%/*}/openssl_tools.py")" || return 1

    if ! declared_public="$("$openssl_binary" pkey -pubin -in "$auth_dir/auth_public_key" -pubout 2>/dev/null)" \
        || ! derived_public="$("$openssl_binary" pkey -in "$auth_dir/auth_private_key" -pubout 2>/dev/null)" \
        || ! public_description="$("$openssl_binary" pkey -pubin -in "$auth_dir/auth_public_key" -text -noout 2>/dev/null)"; then
        echo "Invalid auth-api key pair; Ed25519 keys and a compatible OpenSSL are required." >&2
        return 1
    fi
    if [[ "$public_description" != *"ED25519 Public-Key:"* ]]; then
        echo "auth-api requires Ed25519 keys for PASETO v4.public." >&2
        return 1
    fi
    if [ "$declared_public" != "$derived_public" ]; then
        echo "AUTH_PUBLIC_KEY does not match AUTH_PRIVATE_KEY for auth-api." >&2
        return 1
    fi
}

validate_competency_pki() {
    local secrets_dir="$1"
    local competency_dir="${secrets_dir}/competency-trainer"
    local issuing_certificate="${competency_dir}/agent_issuing_certificate"
    local issuing_key="${competency_dir}/agent_issuing_private_key"
    local chain="${competency_dir}/agent_certificate_chain"
    local chain_issuing="${secrets_dir}/.agent-chain-issuing.pem"
    local chain_root="${secrets_dir}/.agent-chain-root.pem"
    local certificate_public_key="${secrets_dir}/.agent-certificate-public.der"
    local private_public_key="${secrets_dir}/.agent-private-public.der"
    local certificate_count

    command -v openssl >/dev/null 2>&1 || {
        echo "openssl is required to validate deployment PKI material." >&2
        return 1
    }
    openssl x509 -in "$issuing_certificate" -noout >/dev/null 2>&1 \
        || fail_invalid_secret "AGENT_ACCESS_ISSUING_CERTIFICATE"
    openssl pkey -in "$issuing_key" -noout >/dev/null 2>&1 \
        || fail_invalid_secret "AGENT_ACCESS_ISSUING_PRIVATE_KEY"

    certificate_count="$(grep -c '^-----BEGIN CERTIFICATE-----$' "$chain" || true)"
    if [ "$certificate_count" -ne 2 ]; then
        fail_invalid_secret "AGENT_ACCESS_CERTIFICATE_CHAIN"
        return 1
    fi
    extract_chain_certificate "$chain" 1 "$chain_issuing"
    extract_chain_certificate "$chain" 2 "$chain_root"
    openssl verify -CAfile "$chain_root" "$chain_root" >/dev/null 2>&1 \
        || fail_invalid_secret "AGENT_ACCESS_CERTIFICATE_CHAIN"
    openssl verify -CAfile "$chain_root" "$chain_issuing" >/dev/null 2>&1 \
        || fail_invalid_secret "AGENT_ACCESS_CERTIFICATE_CHAIN"
    if [ "$(openssl x509 -in "$issuing_certificate" -noout -fingerprint -sha256)" \
        != "$(openssl x509 -in "$chain_issuing" -noout -fingerprint -sha256)" ]; then
        fail_invalid_secret "AGENT_ACCESS_CERTIFICATE_CHAIN"
        return 1
    fi
    openssl x509 -in "$issuing_certificate" -pubkey -noout \
        | openssl pkey -pubin -outform DER >"$certificate_public_key" 2>/dev/null \
        || fail_invalid_secret "AGENT_ACCESS_ISSUING_CERTIFICATE"
    openssl pkey -in "$issuing_key" -pubout -outform DER >"$private_public_key" 2>/dev/null \
        || fail_invalid_secret "AGENT_ACCESS_ISSUING_PRIVATE_KEY"
    if ! cmp -s "$certificate_public_key" "$private_public_key"; then
        fail_invalid_secret "AGENT_ACCESS_ISSUING_PRIVATE_KEY"
        return 1
    fi
    rm -f \
        "$chain_issuing" \
        "$chain_root" \
        "$certificate_public_key" \
        "$private_public_key"
}

verify_minio_credential_fingerprints() {
    local candidate_fingerprints="$1"
    local legacy_candidate_fingerprints="$2"
    local fingerprint_marker="$3"
    local active_slot_marker="$4"

    if ! python3 "${repo_dir}/infra/scripts/minio_credential_fingerprints.py" \
        >"$candidate_fingerprints"; then
        echo "Could not calculate candidate MinIO credential fingerprints." >&2
        return 1
    fi
    chmod 600 "$candidate_fingerprints"

    if [ ! -e "$fingerprint_marker" ] && [ ! -L "$fingerprint_marker" ]; then
        if [ -e "$active_slot_marker" ] || [ -L "$active_slot_marker" ]; then
            echo "An active deployment exists without a MinIO credential fingerprint marker." >&2
            echo "Refusing to mutate live MinIO credentials automatically." >&2
            return 1
        fi
        printf '%s\n' first
        return
    fi
    if ! python3 "${repo_dir}/infra/scripts/validate_private_file.py" \
        "$fingerprint_marker"; then
        echo "MinIO credential fingerprint marker must be owner-only and owned by the current user." >&2
        return 1
    fi
    if cmp -s "$candidate_fingerprints" "$fingerprint_marker"; then
        printf '%s\n' current
        return
    fi
    if python3 "${repo_dir}/infra/scripts/minio_credential_fingerprints.py" \
        --legacy-secret-keys-only >"$legacy_candidate_fingerprints" \
        && chmod 600 "$legacy_candidate_fingerprints" \
        && cmp -s "$legacy_candidate_fingerprints" "$fingerprint_marker"; then
        printf '%s\n' legacy
        return
    fi
    echo "MinIO credential rotation is not supported during make run." >&2
    echo "Keep the existing MinIO access and secret keys or perform a coordinated maintenance rotation." >&2
    return 1
}

PREVIOUS_COMPOSE_SECRET_GENERATION=""

switch_compose_secret_slot() {
    local candidate_dir="$1"
    local slot_pointer="$2"
    local temporary_pointer="$3"
    local slot_name="$4"
    local state_directory="$5"
    local candidate_name
    local old_target=""

    PREVIOUS_COMPOSE_SECRET_GENERATION=""
    candidate_name="$(basename "$candidate_dir")"
    [[ "$candidate_name" =~ ^${slot_name}-[A-Za-z0-9]+$ ]] || {
        echo "Candidate Compose secret generation has an invalid name." >&2
        return 1
    }
    if [ -e "$slot_pointer" ] || [ -L "$slot_pointer" ]; then
        if [ ! -L "$slot_pointer" ]; then
            echo "Compose secret slot pointer must be a symlink." >&2
            return 1
        fi
        old_target="$(readlink "$slot_pointer")"
        if [[ ! "$old_target" =~ ^\.compose-secret-generations/${slot_name}-[A-Za-z0-9]+$ ]]; then
            echo "Compose secret slot pointer has an invalid target." >&2
            return 1
        fi
        PREVIOUS_COMPOSE_SECRET_GENERATION="${state_directory}/${old_target}"
        if [ ! -d "$PREVIOUS_COMPOSE_SECRET_GENERATION" ] \
            || [ -L "$PREVIOUS_COMPOSE_SECRET_GENERATION" ] \
            || [ ! -O "$PREVIOUS_COMPOSE_SECRET_GENERATION" ]; then
            echo "Previous Compose secret generation is not a safe directory." >&2
            return 1
        fi
    fi

    if [ -e "$temporary_pointer" ] || [ -L "$temporary_pointer" ]; then
        echo "Temporary Compose secret slot pointer already exists." >&2
        return 1
    fi
    ln -s ".compose-secret-generations/${candidate_name}" "$temporary_pointer"
    mv -Tf "$temporary_pointer" "$slot_pointer"
}

cleanup_compose_secret_transaction() {
    local candidate_dir="$1"
    local slot_pointer="$2"
    local temporary_pointer="$3"
    local expected_target="$4"
    shift 4

    if [ -L "$temporary_pointer" ] \
        && [ "$(readlink "$temporary_pointer")" = "$expected_target" ]; then
        rm -f -- "$temporary_pointer"
    fi
    if [ ! -L "$slot_pointer" ] \
        || [ "$(readlink "$slot_pointer")" != "$expected_target" ]; then
        rm -rf -- "$candidate_dir"
    fi
    rm -f -- "$@"
}

prepare_compose_secret_files() {
    if [ -z "${repo_dir:-}" ]; then
        echo "repo_dir must be set before sourcing compose_secrets.sh." >&2
        exit 1
    fi

    local slot_name="${1:?Compose secret slot name is required}"
    local default_state_directory
    local generation_root
    local slot_pointer
    local temporary_pointer
    local expected_pointer_target
    local candidate_dir
    local candidate_environment_file
    local candidate_fingerprints
    local legacy_candidate_fingerprints
    local fingerprint_marker
    local active_slot_marker
    local fingerprint_mode
    local candidate_path
    local variable_name
    local cleanup_status

    load_compose_secret_file_variables || exit 1

    if [[ ! "$slot_name" =~ ^(blue|green|maintenance|scan)$ ]]; then
        echo "Unsupported Compose secret slot: ${slot_name}." >&2
        exit 1
    fi

    require_command sops
    default_state_directory="$(runtime_state_directory)"
    generation_root="${default_state_directory}/.compose-secret-generations"
    if [ -L "$generation_root" ] \
        || { [ -e "$generation_root" ] && [ ! -d "$generation_root" ]; }; then
        echo "Compose secret generation root must be a real directory." >&2
        exit 1
    fi
    mkdir -p "$generation_root"
    if [ ! -O "$generation_root" ]; then
        echo "Compose secret generation root must be owned by the deploy user." >&2
        exit 1
    fi
    chmod 700 "$generation_root"
    slot_pointer="${default_state_directory}/compose-secrets-${slot_name}"
    fingerprint_marker="${default_state_directory}/minio-credentials.sha256"
    active_slot_marker="${default_state_directory}/active-slot"
    candidate_dir="$(mktemp -d "${generation_root}/${slot_name}-XXXXXX")"
    expected_pointer_target=".compose-secret-generations/$(basename "$candidate_dir")"
    temporary_pointer="${default_state_directory}/.compose-secrets-${slot_name}.$$"
    candidate_environment_file="$(mktemp "${default_state_directory}/.compose-secrets.env.XXXXXX")"
    candidate_fingerprints="$(mktemp "${default_state_directory}/.minio-fingerprints.XXXXXX")"
    legacy_candidate_fingerprints="$(mktemp "${default_state_directory}/.minio-legacy-fingerprints.XXXXXX")"

    trap 'cleanup_status=$?; trap - EXIT HUP INT TERM; cleanup_compose_secret_transaction \
        "$candidate_dir" "$slot_pointer" "$temporary_pointer" "$expected_pointer_target" \
        "$candidate_environment_file" "$candidate_fingerprints" \
        "$legacy_candidate_fingerprints"; exit "$cleanup_status"' EXIT
    trap 'exit 130' HUP INT TERM

    if ! python3 "${repo_dir}/infra/scripts/materialize_sops_secrets.py" \
        --manifest "${repo_dir}/infra/deploy/runtime-secrets.manifest.json" \
        --repo-dir "$repo_dir" \
        --output-dir "$candidate_dir" \
        --compose-env-output "$candidate_environment_file" \
        --age-key-file "$SOPS_AGE_KEY_FILE"; then
        exit 1
    fi
    if ! python3 "${repo_dir}/infra/scripts/validate_private_file.py" \
        "$candidate_environment_file"; then
        echo "Generated Compose secret-path environment must be owner-only." >&2
        exit 1
    fi

    set -a
    # shellcheck disable=SC1090
    . "$candidate_environment_file"
    set +a
    for variable_name in "${COMPOSE_SECRET_FILE_VARIABLES[@]}"; do
        require_env "$variable_name"
    done

    if ! validate_minio_credentials \
        || ! validate_competency_pki "$candidate_dir" \
        || ! validate_auth_api_pki "$candidate_dir"; then
        exit 1
    fi
    if ! fingerprint_mode="$(verify_minio_credential_fingerprints \
        "$candidate_fingerprints" \
        "$legacy_candidate_fingerprints" \
        "$fingerprint_marker" \
        "$active_slot_marker")"; then
        exit 1
    fi
    if ! switch_compose_secret_slot \
        "$candidate_dir" \
        "$slot_pointer" \
        "$temporary_pointer" \
        "$slot_name" \
        "$default_state_directory"; then
        exit 1
    fi

    for variable_name in "${COMPOSE_SECRET_FILE_VARIABLES[@]}"; do
        candidate_path="${!variable_name}"
        if [[ "$candidate_path" != "${candidate_dir}/"* ]]; then
            echo "Generated Compose secret path escapes its candidate directory." >&2
            exit 1
        fi
        printf -v "$variable_name" '%s' \
            "${slot_pointer}/${candidate_path#"${candidate_dir}/"}"
    done

    if [ "$fingerprint_mode" = legacy ]; then
        if ! mv -f "$candidate_fingerprints" "$fingerprint_marker"; then
            echo "Could not upgrade the legacy MinIO credential fingerprint marker." >&2
            exit 1
        fi
    fi

    if [ -n "$PREVIOUS_COMPOSE_SECRET_GENERATION" ] \
        && [ "$PREVIOUS_COMPOSE_SECRET_GENERATION" != "$candidate_dir" ]; then
        rm -rf -- "$PREVIOUS_COMPOSE_SECRET_GENERATION"
    fi
    cleanup_compose_secret_transaction \
        "$candidate_dir" "$slot_pointer" "$temporary_pointer" "$expected_pointer_target" \
        "$candidate_environment_file" "$candidate_fingerprints" \
        "$legacy_candidate_fingerprints"
    trap - EXIT HUP INT TERM
}
