#!/usr/bin/env bash

verify_served_edge_certificates() {
    local repository_directory="$1"
    local hostname
    local expected_fingerprint
    local actual_fingerprint
    local peer_certificate
    local attempt
    local served_current_certificate
    local -a hostnames=(
        "$PERSONAL_WORKSPACE_DOMAIN"
        "$COMPETENCY_DOMAIN"
        "$MINIO_DOMAIN"
        "agent.${COMPETENCY_DOMAIN}"
    )

    peer_certificate="$(mktemp)"
    if ! expected_fingerprint="$(
        openssl x509 \
            -in "${repository_directory}/infra/nginx/certs/current/fullchain.pem" \
            -noout \
            -fingerprint \
            -sha256
    )"; then
        rm -f "$peer_certificate"
        echo "Could not read the current synchronized edge certificate." >&2
        return 1
    fi

    for hostname in "${hostnames[@]}"; do
        served_current_certificate=false
        for attempt in {1..15}; do
            if openssl s_client \
                -connect 127.0.0.1:443 \
                -servername "$hostname" \
                </dev/null 2>/dev/null \
                | openssl x509 -outform PEM >"$peer_certificate" 2>/dev/null \
                && openssl x509 -in "$peer_certificate" -noout -checkend 0 >/dev/null \
                && openssl x509 -in "$peer_certificate" -noout -checkhost "$hostname" >/dev/null \
                && actual_fingerprint="$(
                    openssl x509 -in "$peer_certificate" -noout -fingerprint -sha256
                )" \
                && [ "$actual_fingerprint" = "$expected_fingerprint" ]; then
                served_current_certificate=true
                break
            fi
            sleep 1
        done
        if [ "$served_current_certificate" != true ]; then
            rm -f "$peer_certificate"
            echo "nginx did not serve the current valid certificate for ${hostname}." >&2
            return 1
        fi
    done
    rm -f "$peer_certificate"
}

smoke_edge_applications() {
    local check
    local hostname
    local path
    local attempt
    local -a checks=(
        "${PERSONAL_WORKSPACE_DOMAIN}|/api/healthcheck"
        "${PERSONAL_WORKSPACE_DOMAIN}|/healthz"
        "${COMPETENCY_DOMAIN}|/api/healthcheck"
        "${COMPETENCY_DOMAIN}|/healthz"
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
