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
        "$APP_DOMAIN"
        "$MINIO_DOMAIN"
        "agent.${APP_DOMAIN}"
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
    local status
    local language
    local rendered_page
    local -a checks=(
        "${APP_DOMAIN}|/healthz"
        "${APP_DOMAIN}|/ru/how-this-site-is-built"
        "${APP_DOMAIN}|/api/personal-workspace/healthcheck"
        "${APP_DOMAIN}|/api/competency/healthcheck"
        "${APP_DOMAIN}|/api/auth/healthcheck"
        "${APP_DOMAIN}|/api/i18n/healthcheck/ready"
        "${APP_DOMAIN}|/api/i18n/languages"
        "${APP_DOMAIN}|/api/i18n/bundles/shared/ru"
        "${APP_DOMAIN}|/api/i18n/bundles/shared/en"
        "${APP_DOMAIN}|/api/i18n/bundles/how-this-site-is-built/ru"
        "${APP_DOMAIN}|/api/i18n/bundles/how-this-site-is-built/en"
        "${APP_DOMAIN}|/api/i18n/bundles/articles/ru"
        "${APP_DOMAIN}|/api/i18n/bundles/competency-matrix/ru"
        "${APP_DOMAIN}|/api/i18n/bundles/updates/ru"
        "${APP_DOMAIN}|/api/i18n/bundles/sitemap/ru"
        "${APP_DOMAIN}|/api/i18n/bundles/account/ru"
        "${APP_DOMAIN}|/api/i18n/bundles/admin-panel/ru"
        "${APP_DOMAIN}|/api/i18n/bundles/personal-workspace/ru"
        "${APP_DOMAIN}|/api/i18n/bundles/personal-workspace/en"
    )
    local -a retired_i18n_paths=(
        "/api/i18n/bundles/ru"
        "/api/i18n/personal-workspace/bundles/ru"
        "/api/personal-workspace/i18n/languages"
        "/api/personal-workspace/i18n/bundles/ru"
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

    for path in "${retired_i18n_paths[@]}"; do
        status="$(curl \
            --silent \
            --show-error \
            --output /dev/null \
            --write-out '%{http_code}' \
            --max-time 5 \
            --noproxy '*' \
            --resolve "${APP_DOMAIN}:443:127.0.0.1" \
            "https://${APP_DOMAIN}${path}")"
        if [ "$status" != 404 ]; then
            echo "Retired i18n path returned ${status}, expected 404: ${path}" >&2
            return 1
        fi
    done

    rendered_page="$(mktemp)"
    for language in ru en; do
        if ! curl \
            --fail \
            --silent \
            --show-error \
            --output "$rendered_page" \
            --max-time 10 \
            --noproxy '*' \
            --resolve "${APP_DOMAIN}:443:127.0.0.1" \
            "https://${APP_DOMAIN}/${language}/how-this-site-is-built"; then
            rm -f "$rendered_page"
            echo "Could not fetch the ${language} SSR page for i18n verification." >&2
            return 1
        fi
        if ! verify_i18n_ssr_transfer_state "$rendered_page" "$language"; then
            rm -f "$rendered_page"
            return 1
        fi
    done
    rm -f "$rendered_page"
}
