#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd -- "${script_dir}/../.." && pwd)"
# renovate: datasource=docker depName=hadolint/hadolint
readonly hadolint_image="hadolint/hadolint:v2.14.0@sha256:27086352fd5e1907ea2b934eb1023f217c5ae087992eb59fde121dce9c9ff21e"
# renovate: datasource=docker depName=koalaman/shellcheck
readonly shellcheck_image="koalaman/shellcheck:v0.11.0@sha256:61862eba1fcf09a484ebcc6feea46f1782532571a34ed51fedf90dd25f925a8d"
dockerfiles=()
shell_files=()

while IFS= read -r dockerfile; do
    dockerfiles+=("$dockerfile")
done < <(find infra -type f -name Dockerfile -print | sort)
while IFS= read -r shell_file; do
    shell_files+=("$shell_file")
done < <(find infra/scripts -type f -name '*.sh' -print | sort)

if [ "${#dockerfiles[@]}" -eq 0 ] || [ "${#shell_files[@]}" -eq 0 ]; then
    echo "Dockerfile and shell-script discovery must both produce at least one input." >&2
    exit 1
fi

docker run --rm \
    -v "${repo_dir}:/workspace:ro" \
    -w /workspace \
    "$hadolint_image" \
    hadolint \
    --failure-threshold error \
    "${dockerfiles[@]}"

docker run --rm \
    -v "${repo_dir}:/workspace:ro" \
    -w /workspace \
    "$shellcheck_image" \
    "${shell_files[@]}"
