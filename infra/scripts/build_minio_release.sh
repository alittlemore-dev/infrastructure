#!/bin/sh
set -eu

release=RELEASE.2025-10-15T17-29-55Z
commit=9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a
checksum=be6d0bd3696c3a13a35f02d3a0280b64319c67918b4501c5c3d87f96d000085c
version=2025-10-15T17:29:55Z
root=github.com/minio/minio/cmd

apk add --no-cache curl
work_dir="$(mktemp -d)"
trap 'rm -rf -- "$work_dir"' EXIT
cd "$work_dir"
curl --fail --location --retry 3 --silent --show-error \
    --output source.tar.gz "https://codeload.github.com/minio/minio/tar.gz/refs/tags/$release"
printf '%s  source.tar.gz\n' "$checksum" | sha256sum -c -
tar -xzf source.tar.gz --strip-components=1

export GOTOOLCHAIN=local CGO_ENABLED=0
export GOOS="${TARGETOS:?Docker target OS is required}"
export GOARCH="${TARGETARCH:?Docker target architecture is required}"
go build -mod=readonly -buildvcs=false -tags kqueue -trimpath \
    -ldflags "-s -w -X $root.Version=$version -X $root.ReleaseTag=$release -X $root.CommitID=$commit -X $root.ShortCommitID=$(printf '%.12s' "$commit") -X $root.CopyrightYear=${version%%-*}" \
    -o /usr/local/bin/minio .
