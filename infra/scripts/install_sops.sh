#!/usr/bin/env bash
set -euo pipefail

readonly SOPS_VERSION=3.13.3
readonly SOPS_LINUX_AMD64_SHA256=e5bec3346a873ae91d871550f3e698c1aad962aff462a080e40f25fde17fef6b
readonly SOPS_URL="https://github.com/getsops/sops/releases/download/v${SOPS_VERSION}/sops-v${SOPS_VERSION}.linux.amd64"

destination="${1:?destination path is required}"
temporary="$(mktemp "${destination}.tmp.XXXXXX")"
trap 'rm -f "$temporary"' EXIT

curl --connect-timeout 15 --max-time 120 --fail --silent --show-error --location \
    "$SOPS_URL" --output "$temporary"
printf '%s  %s\n' "$SOPS_LINUX_AMD64_SHA256" "$temporary" | sha256sum --check --status
chmod 755 "$temporary"
mv -f "$temporary" "$destination"
trap - EXIT
