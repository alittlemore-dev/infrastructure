#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd -- "${script_dir}/../.." && pwd)"
state_dir="${ALITTLEMORE_DEV_STATE_DIR:-${repo_dir}/.dev-state}"
personal_workspace_dir="${PERSONAL_WORKSPACE_DIR:-${repo_dir}/../personal-workspace}"
competency_trainer_dir="${COMPETENCY_TRAINER_DIR:-${repo_dir}/../competency-trainer}"
ca_certificate="${state_dir}/tls/local-development-ca.cert.pem"
server_certificate="${state_dir}/tls/fullchain.pem"

require_command() {
    local command_name="$1"

    if ! command -v "$command_name" >/dev/null 2>&1; then
        echo "${command_name} could not be found. Install it." >&2
        exit 1
    fi
}

prepare_state() {
    require_command python3
    require_command openssl
    python3 "${script_dir}/prepare_dev_state.py" \
        --repo-dir "$repo_dir" \
        --state-dir "$state_dir" \
        --personal-workspace-dir "$personal_workspace_dir" \
        --competency-trainer-dir "$competency_trainer_dir" >/dev/null
}

verify_trust() {
    local platform

    if [ ! -s "$ca_certificate" ] || [ ! -s "$server_certificate" ]; then
        echo "Local HTTPS certificates have not been generated. Run make dev-trust." >&2
        return 1
    fi
    platform="${ALITTLEMORE_DEV_PLATFORM:-$(uname -s)}"
    case "$platform" in
        Darwin)
            require_command security
            security verify-cert \
                -q \
                -L \
                -p ssl \
                -n alittlemore.localhost \
                -c "$server_certificate" >/dev/null 2>&1
            ;;
        Linux)
            require_command openssl
            openssl verify \
                -purpose sslserver \
                -verify_hostname alittlemore.localhost \
                "$server_certificate" >/dev/null 2>&1
            ;;
        *)
            echo "Automatic local CA verification is not supported on ${platform}." >&2
            return 1
            ;;
    esac
}

trust_ca() {
    local platform
    local user_keychain

    prepare_state
    if verify_trust; then
        echo "The alittlemore.dev local development CA is already trusted."
        return
    fi

    platform="${ALITTLEMORE_DEV_PLATFORM:-$(uname -s)}"
    case "$platform" in
        Darwin)
            require_command security
            if [ -z "${HOME:-}" ]; then
                echo "HOME is required to locate the login keychain." >&2
                exit 1
            fi
            user_keychain="${HOME}/Library/Keychains/login.keychain-db"
            security add-trusted-cert \
                -r trustRoot \
                -k "$user_keychain" \
                "$ca_certificate"
            ;;
        Linux)
            require_command sudo
            if command -v update-ca-certificates >/dev/null 2>&1; then
                sudo install \
                    -m 0644 \
                    "$ca_certificate" \
                    /usr/local/share/ca-certificates/alittlemore-dev-local.crt
                sudo update-ca-certificates
            elif command -v trust >/dev/null 2>&1; then
                sudo trust anchor "$ca_certificate"
            else
                echo "Install the CA manually from ${ca_certificate}; no supported Linux CA tool was found." >&2
                exit 1
            fi
            ;;
        *)
            echo "Install the CA manually from ${ca_certificate}; ${platform} is not supported automatically." >&2
            exit 1
            ;;
    esac

    if ! verify_trust; then
        echo "The local CA was installed, but the operating system still does not trust it." >&2
        exit 1
    fi
    echo "Trusted the alittlemore.dev local development CA."
}

action="${1:-}"
case "$action" in
    trust) trust_ca ;;
    verify)
        if ! verify_trust; then
            echo "Run make dev-trust once, then repeat make dev." >&2
            exit 1
        fi
        ;;
    *)
        echo "Usage: $0 trust|verify" >&2
        exit 2
        ;;
esac
