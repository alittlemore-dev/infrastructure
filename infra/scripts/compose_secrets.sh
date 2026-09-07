#!/usr/bin/env bash

readonly COMPOSE_SECRET_SPECS=(
    "MINIO_ROOT_ACCESS_KEY minio_root_access_key COMPOSE_MINIO_ROOT_ACCESS_KEY_FILE required literal"
    "MINIO_ROOT_SECRET_KEY minio_root_secret_key COMPOSE_MINIO_ROOT_SECRET_KEY_FILE required literal"
    "DATABASUS_MINIO_ACCESS_KEY databasus_minio_access_key COMPOSE_DATABASUS_MINIO_ACCESS_KEY_FILE required literal"
    "DATABASUS_MINIO_SECRET_KEY databasus_minio_secret_key COMPOSE_DATABASUS_MINIO_SECRET_KEY_FILE required literal"
    "PERSONAL_WORKSPACE_APP_SECRET_KEY personal_workspace_app_secret_key COMPOSE_PERSONAL_WORKSPACE_APP_SECRET_KEY_FILE required literal"
    "PERSONAL_WORKSPACE_DB_PASSWORD personal_workspace_db_password COMPOSE_PERSONAL_WORKSPACE_DB_PASSWORD_FILE required literal"
    "PERSONAL_WORKSPACE_MINIO_ACCESS_KEY personal_workspace_minio_access_key COMPOSE_PERSONAL_WORKSPACE_MINIO_ACCESS_KEY_FILE required literal"
    "PERSONAL_WORKSPACE_MINIO_SECRET_KEY personal_workspace_minio_secret_key COMPOSE_PERSONAL_WORKSPACE_MINIO_SECRET_KEY_FILE required literal"
    "PERSONAL_WORKSPACE_OWNER_PASSWORD_HASH personal_workspace_owner_password_hash COMPOSE_PERSONAL_WORKSPACE_OWNER_PASSWORD_HASH_FILE required literal"
    "PERSONAL_WORKSPACE_SENTRY_DSN personal_workspace_sentry_dsn COMPOSE_PERSONAL_WORKSPACE_SENTRY_DSN_FILE allow-empty literal"
    "COMPETENCY_APP_SECRET_KEY competency_app_secret_key COMPOSE_COMPETENCY_APP_SECRET_KEY_FILE required literal"
    "COMPETENCY_AUTH_PRIVATE_KEY competency_auth_private_key COMPOSE_COMPETENCY_AUTH_PRIVATE_KEY_FILE required pem"
    "COMPETENCY_DB_PASSWORD competency_db_password COMPOSE_COMPETENCY_DB_PASSWORD_FILE required literal"
    "COMPETENCY_MINIO_ACCESS_KEY competency_minio_access_key COMPOSE_COMPETENCY_MINIO_ACCESS_KEY_FILE required literal"
    "COMPETENCY_MINIO_SECRET_KEY competency_minio_secret_key COMPOSE_COMPETENCY_MINIO_SECRET_KEY_FILE required literal"
    "COMPETENCY_OWNER_INIT_PASSWORD competency_owner_init_password COMPOSE_COMPETENCY_OWNER_INIT_PASSWORD_FILE required literal"
    "COMPETENCY_SENTRY_DSN competency_sentry_dsn COMPOSE_COMPETENCY_SENTRY_DSN_FILE allow-empty literal"
    "COMPETENCY_AGENT_ACCESS_ISSUING_CERTIFICATE competency_agent_issuing_certificate COMPOSE_COMPETENCY_AGENT_ISSUING_CERTIFICATE_FILE required pem"
    "COMPETENCY_AGENT_ACCESS_ISSUING_PRIVATE_KEY competency_agent_issuing_private_key COMPOSE_COMPETENCY_AGENT_ISSUING_PRIVATE_KEY_FILE required pem"
    "COMPETENCY_AGENT_ACCESS_CERTIFICATE_CHAIN competency_agent_certificate_chain COMPOSE_COMPETENCY_AGENT_CERTIFICATE_CHAIN_FILE required pem"
)

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

validate_competency_pki() {
    local secrets_dir="$1"
    local auth_key="${secrets_dir}/competency_auth_private_key"
    local auth_public_key="${secrets_dir}/.competency-auth-public.pem"
    local issuing_certificate="${secrets_dir}/competency_agent_issuing_certificate"
    local issuing_key="${secrets_dir}/competency_agent_issuing_private_key"
    local chain="${secrets_dir}/competency_agent_certificate_chain"
    local chain_issuing="${secrets_dir}/.agent-chain-issuing.pem"
    local chain_root="${secrets_dir}/.agent-chain-root.pem"
    local certificate_public_key="${secrets_dir}/.agent-certificate-public.der"
    local private_public_key="${secrets_dir}/.agent-private-public.der"
    local auth_declared_public_key="${secrets_dir}/.auth-declared-public.der"
    local auth_private_public_key="${secrets_dir}/.auth-private-public.der"
    local certificate_count

    command -v openssl >/dev/null 2>&1 || {
        echo "openssl is required to validate deployment PKI material." >&2
        return 1
    }
    openssl pkey -in "$auth_key" -noout >/dev/null 2>&1 \
        || fail_invalid_secret "COMPETENCY_AUTH_PRIVATE_KEY"
    printf '%b' "$COMPETENCY_AUTH_PUBLIC_KEY" >"$auth_public_key"
    openssl pkey -pubin -in "$auth_public_key" -outform DER >"$auth_declared_public_key" 2>/dev/null \
        || fail_invalid_secret "COMPETENCY_AUTH_PUBLIC_KEY"
    openssl pkey -in "$auth_key" -pubout -outform DER >"$auth_private_public_key" 2>/dev/null \
        || fail_invalid_secret "COMPETENCY_AUTH_PRIVATE_KEY"
    if ! cmp -s "$auth_declared_public_key" "$auth_private_public_key"; then
        echo "COMPETENCY_AUTH_PUBLIC_KEY does not match COMPETENCY_AUTH_PRIVATE_KEY." >&2
        return 1
    fi
    openssl x509 -in "$issuing_certificate" -noout >/dev/null 2>&1 \
        || fail_invalid_secret "COMPETENCY_AGENT_ACCESS_ISSUING_CERTIFICATE"
    openssl pkey -in "$issuing_key" -noout >/dev/null 2>&1 \
        || fail_invalid_secret "COMPETENCY_AGENT_ACCESS_ISSUING_PRIVATE_KEY"

    certificate_count="$(grep -c '^-----BEGIN CERTIFICATE-----$' "$chain" || true)"
    if [ "$certificate_count" -ne 2 ]; then
        fail_invalid_secret "COMPETENCY_AGENT_ACCESS_CERTIFICATE_CHAIN"
        return 1
    fi
    extract_chain_certificate "$chain" 1 "$chain_issuing"
    extract_chain_certificate "$chain" 2 "$chain_root"
    openssl verify -CAfile "$chain_root" "$chain_root" >/dev/null 2>&1 \
        || fail_invalid_secret "COMPETENCY_AGENT_ACCESS_CERTIFICATE_CHAIN"
    openssl verify -CAfile "$chain_root" "$chain_issuing" >/dev/null 2>&1 \
        || fail_invalid_secret "COMPETENCY_AGENT_ACCESS_CERTIFICATE_CHAIN"
    if [ "$(openssl x509 -in "$issuing_certificate" -noout -fingerprint -sha256)" \
        != "$(openssl x509 -in "$chain_issuing" -noout -fingerprint -sha256)" ]; then
        fail_invalid_secret "COMPETENCY_AGENT_ACCESS_CERTIFICATE_CHAIN"
        return 1
    fi
    openssl x509 -in "$issuing_certificate" -pubkey -noout \
        | openssl pkey -pubin -outform DER >"$certificate_public_key" 2>/dev/null \
        || fail_invalid_secret "COMPETENCY_AGENT_ACCESS_ISSUING_CERTIFICATE"
    openssl pkey -in "$issuing_key" -pubout -outform DER >"$private_public_key" 2>/dev/null \
        || fail_invalid_secret "COMPETENCY_AGENT_ACCESS_ISSUING_PRIVATE_KEY"
    if ! cmp -s "$certificate_public_key" "$private_public_key"; then
        fail_invalid_secret "COMPETENCY_AGENT_ACCESS_ISSUING_PRIVATE_KEY"
        return 1
    fi
    rm -f \
        "$auth_public_key" \
        "$auth_declared_public_key" \
        "$auth_private_public_key" \
        "$chain_issuing" \
        "$chain_root" \
        "$certificate_public_key" \
        "$private_public_key"
}

prepare_compose_secret_files() {
    if [ -z "${repo_dir:-}" ]; then
        echo "repo_dir must be set before sourcing compose_secrets.sh." >&2
        exit 1
    fi

    local default_state_directory
    local compose_secrets_dir
    local previous_umask
    local spec

    default_state_directory="$(runtime_state_directory)"
    compose_secrets_dir="${COMPOSE_SECRETS_DIR:-${default_state_directory}/compose-secrets}"
    mkdir -p "$compose_secrets_dir"
    chmod 700 "$compose_secrets_dir"
    previous_umask="$(umask)"
    umask 077

    for spec in "${COMPOSE_SECRET_SPECS[@]}"; do
        local source_variable_name
        local secret_file_name
        local compose_file_variable_name
        local empty_policy
        local encoding
        local secret_file_path
        local secret_value

        read -r source_variable_name secret_file_name compose_file_variable_name empty_policy encoding <<<"$spec"
        if [ "${!source_variable_name+x}" != "x" ]; then
            echo "${source_variable_name} must be set before preparing Compose secret files." >&2
            exit 1
        fi
        secret_value="${!source_variable_name}"
        if [ "$empty_policy" = "required" ] && [ -z "$secret_value" ]; then
            echo "${source_variable_name} must not be empty." >&2
            exit 1
        fi

        secret_file_path="${compose_secrets_dir}/${secret_file_name}"
        rm -f "$secret_file_path"
        if [ "$encoding" = "pem" ]; then
            printf '%b' "$secret_value" >"$secret_file_path"
        else
            printf '%s' "$secret_value" >"$secret_file_path"
        fi
        chmod 444 "$secret_file_path"
        export "$compose_file_variable_name=$secret_file_path"
    done

    validate_competency_pki "$compose_secrets_dir"
    umask "$previous_umask"
}
