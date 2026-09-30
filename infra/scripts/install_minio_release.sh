#!/bin/sh
set -eu

component="${1:?MinIO component is required}"
architecture="${TARGETARCH:?Docker target architecture is required}"

case "$component" in
    minio) release=RELEASE.2025-09-07T16-13-09Z ;;
    mc) release=RELEASE.2025-08-13T08-35-41Z ;;
    *) echo "Unsupported MinIO component: ${component}" >&2; exit 1 ;;
esac

# Pinned from the matching official GitHub release's .sha256sum assets.
case "${component}/${architecture}" in
    minio/amd64) checksum=7c5bd8512c6e966455b1d198209358b2d191c77a83ab377c4073281065fb855f ;;
    minio/arm64) checksum=5c83cd2cf151717ba0243f73e1c7802ff36e272b67144bdd7f1f7d684fd6f03d ;;
    mc/amd64) checksum=01f866e9c5f9b87c2b09116fa5d7c06695b106242d829a8bb32990c00312e891 ;;
    mc/arm64) checksum=14c8c9616cfce4636add161304353244e8de383b2e2752c0e9dad01d4c27c12c ;;
    *) echo "Unsupported MinIO target architecture: ${architecture}" >&2; exit 1 ;;
esac

apk upgrade --no-cache
apk add --no-cache ca-certificates curl

download="$(mktemp)"
trap 'rm -f "$download"' EXIT HUP INT TERM
curl --fail --location --retry 3 --silent --show-error \
    --output "$download" \
    "https://github.com/minio/${component}/releases/download/${release}/${component}.linux-${architecture}.${release}"
printf '%s  %s\n' "$checksum" "$download" | sha256sum -c -
chmod 755 "$download"
mv "$download" "/usr/local/bin/${component}"
"/usr/local/bin/${component}" --version
rm -f -- "$0"
