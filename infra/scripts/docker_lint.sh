#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd -- "${script_dir}/../.." && pwd)"
readonly hadolint_image="hadolint/hadolint:v2.14.0"
readonly shellcheck_image="koalaman/shellcheck:v0.11.0"
shell_files=()

while IFS= read -r shell_file; do
    shell_files+=("$shell_file")
done < <(find infra/scripts -maxdepth 1 -type f -name '*.sh' -print | sort)

docker run --rm \
    -v "${repo_dir}:/workspace:ro" \
    -w /workspace \
    "$hadolint_image" \
    hadolint \
    --failure-threshold error \
    infra/cert-sync/Dockerfile \
    infra/minio/Dockerfile \
    infra/nginx/Dockerfile

docker run --rm \
    -v "${repo_dir}:/workspace:ro" \
    -w /workspace \
    "$shellcheck_image" \
    "${shell_files[@]}"
