#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd -- "${script_dir}/../.." && pwd)"
quality_compose=(docker compose --file "${repo_dir}/docker-compose.quality.yml")
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

"${quality_compose[@]}" run --rm --no-deps \
    --volume "${repo_dir}:/workspace:ro" \
    --workdir /workspace \
    hadolint \
    --failure-threshold error \
    "${dockerfiles[@]}"

"${quality_compose[@]}" run --rm --no-deps \
    --volume "${repo_dir}:/workspace:ro" \
    --workdir /workspace \
    shellcheck \
    "${shell_files[@]}"
