#!/usr/bin/env bash
set -euo pipefail

readonly AGE_VERSION=1.3.2
readonly AGE_LINUX_AMD64_SHA256=cbe24006683f8eb669266162894b9a522a1af52f2665fbc63a4bb032ed26ac10
readonly AGE_URL="https://github.com/FiloSottile/age/releases/download/v${AGE_VERSION}/age-v${AGE_VERSION}-linux-amd64.tar.gz"

destination="${1:?destination path is required}"
temporary_archive="$(mktemp "${destination}.archive.XXXXXX")"
temporary_directory="$(mktemp -d "${destination}.extract.XXXXXX")"
temporary_binary="$(mktemp "${destination}.tmp.XXXXXX")"
trap 'rm -f "$temporary_archive" "$temporary_binary"; rm -rf -- "$temporary_directory"' EXIT

curl --connect-timeout 15 --max-time 120 --fail --silent --show-error --location \
    "$AGE_URL" --output "$temporary_archive"
printf '%s  %s\n' "$AGE_LINUX_AMD64_SHA256" "$temporary_archive" \
    | sha256sum --check --status
tar --extract --gzip --file "$temporary_archive" --directory "$temporary_directory"
install -m 755 "$temporary_directory/age/age-keygen" "$temporary_binary"
mv -f "$temporary_binary" "$destination"
trap - EXIT
rm -f "$temporary_archive"
rm -rf -- "$temporary_directory"
