#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd -- "${script_dir}/../.." && pwd)"
action="${1:?action is required}"
trivy_image="${2:?Trivy image is required}"

scan_config() {
    docker run --rm \
        -v "${repo_dir}:/workspace:ro" \
        "$trivy_image" \
        --cache-dir /tmp/trivy-cache \
        --quiet \
        config \
        --ignorefile /workspace/.trivyignore.yaml \
        --exit-code 1 \
        --format table \
        --severity HIGH,CRITICAL \
        /workspace
}

scan_image() {
    docker run --rm \
        -v /var/run/docker.sock:/var/run/docker.sock \
        "$trivy_image" \
        --cache-dir /tmp/trivy-cache \
        --quiet \
        image \
        --exit-code 1 \
        --format table \
        --ignore-unfixed \
        --image-src docker \
        --pkg-types os,library \
        --scanners vuln \
        --severity HIGH,CRITICAL \
        "$1"
}

case "$action" in
    config)
        scan_config
        ;;
    images)
        # shellcheck source=infra/scripts/common.sh
        . "$script_dir/common.sh"
        # shellcheck source=infra/scripts/compose_secrets.sh
        . "$script_dir/compose_secrets.sh"
        load_environment
        prepare_compose_secret_files scan
        docker compose build minio nginx cert-sync
        docker pull "${IMAGE_REGISTRY}/personal-workspace-backend:latest"
        docker pull "${IMAGE_REGISTRY}/personal-workspace-frontend:latest"
        docker pull "${IMAGE_REGISTRY}/competency-trainer-backend:latest"
        docker pull "${IMAGE_REGISTRY}/competency-trainer-frontend:latest"
        docker pull postgres:18.4-alpine
        docker pull valkey/valkey:9.0.1
        docker pull minio/mc:RELEASE.2025-08-13T08-35-41Z
        docker pull databasus/databasus:v3.47.1
        docker pull certbot/certbot:v5.2.2
        scan_image alittlemore-infra/minio:RELEASE.2025-09-07T16-13-09Z
        scan_image alittlemore-infra/nginx:1.31.3
        scan_image alittlemore-infra/cert-sync:alpine3.22.2-openssl3.5.7
        scan_image postgres:18.4-alpine
        scan_image valkey/valkey:9.0.1
        scan_image minio/mc:RELEASE.2025-08-13T08-35-41Z
        scan_image databasus/databasus:v3.47.1
        scan_image certbot/certbot:v5.2.2
        scan_image "${IMAGE_REGISTRY}/personal-workspace-backend:latest"
        scan_image "${IMAGE_REGISTRY}/personal-workspace-frontend:latest"
        scan_image "${IMAGE_REGISTRY}/competency-trainer-backend:latest"
        scan_image "${IMAGE_REGISTRY}/competency-trainer-frontend:latest"
        ;;
    *)
        echo "Usage: $0 {config|images} TRIVY_IMAGE" >&2
        exit 2
        ;;
esac
