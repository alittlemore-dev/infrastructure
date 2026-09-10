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

sync_certificates() {
    prepare_certificate_mount_directory
    docker compose build cert-sync
    docker compose run --rm --pull never cert-sync
}

nginx_is_running() {
    docker compose ps --services --status running | grep -Fxq nginx
}

reload_nginx_if_running() {
    if nginx_is_running; then
        docker compose exec -T nginx nginx -t
        docker compose exec -T nginx nginx -s reload
        verify_served_edge_certificates "$repo_dir"
    fi
}

issue_certificates() {
    local -a compose_options=(run --rm)
    local -a challenge_options

    if nginx_is_running; then
        echo "Issuing certificates through the running nginx ACME webroot."
        challenge_options=(--webroot --webroot-path=/var/www/certbot)
    else
        echo "Issuing certificates with the standalone ACME server on port 80."
        compose_options+=(--service-ports)
        challenge_options=(--standalone --preferred-challenges http)
    fi

    docker compose "${compose_options[@]}" certbot \
        certonly \
        "${challenge_options[@]}" \
        --email "$LE_EMAIL" \
        --agree-tos \
        --non-interactive \
        --no-eff-email \
        --keep-until-expiring \
        --expand \
        --cert-name "$TLS_CERTIFICATE_NAME" \
        -d "$APP_DOMAIN" \
        -d "$MINIO_DOMAIN" \
        -d "agent.${APP_DOMAIN}"
    sync_certificates
    reload_nginx_if_running
}

renew_certificates() {
    docker compose run --rm certbot \
        renew \
        --webroot \
        --webroot-path=/var/www/certbot
    sync_certificates
    reload_nginx_if_running
}

action="${1:?action is required}"
require_docker_compose
require_command openssl
acquire_runtime_lock
load_environment
prepare_compose_secret_files maintenance

case "$action" in
    issue) issue_certificates ;;
    renew) renew_certificates ;;
    sync)
        sync_certificates
        reload_nginx_if_running
        ;;
    *)
        echo "Unknown TLS action: ${action}" >&2
        exit 2
        ;;
esac
