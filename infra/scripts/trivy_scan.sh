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
        docker compose build
        images=()
        built_images=()
        while IFS= read -r image; do
            if [ -n "$image" ]; then
                images+=("$image")
            fi
        done < <(docker compose config --images | sort -u)
        if ! built_images_output="$(
            docker compose config --format json \
                | python3 "$script_dir/list_compose_build_images.py"
        )"; then
            exit 1
        fi
        while IFS= read -r image; do
            if [ -n "$image" ]; then
                built_images+=("$image")
            fi
        done <<<"$built_images_output"
        if [ "${#images[@]}" -eq 0 ]; then
            echo "Docker Compose did not report any runtime images." >&2
            exit 1
        fi
        for image in "${images[@]}"; do
            is_built_image=0
            for built_image in "${built_images[@]}"; do
                if [ "$image" = "$built_image" ]; then
                    is_built_image=1
                    break
                fi
            done
            if [ "$is_built_image" -eq 0 ]; then
                docker pull "$image"
            fi
        done
        for image in "${images[@]}"; do
            scan_image "$image"
        done
        ;;
    *)
        echo "Usage: $0 {config|images} TRIVY_IMAGE" >&2
        exit 2
        ;;
esac
