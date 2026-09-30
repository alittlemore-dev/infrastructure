#!/bin/sh
set -eu

# These files are deterministic fixtures created by minio_smoke.sh.
set_identity() {
    identity="$1"
    mc alias set smoke http://minio:9000 \
        "$(cat "/run/secrets/${identity}_access_key")" \
        "$(cat "/run/secrets/${identity}_secret_key")" >/dev/null
}

printf '%s\n' minio-smoke-object >/tmp/object
set_identity minio_root
for bucket in media resume-private knowledge-private auth-avatars database-backups; do
    mc cp /tmp/object "smoke/${bucket}/smoke.txt" >/dev/null
done

verify_identity() {
    set_identity "$1"
    allowed_bucket="$2"
    denied_bucket="$3"
    mc cp /tmp/object "smoke/${allowed_bucket}/smoke.txt" >/dev/null
    if [ "$(mc cat "smoke/${allowed_bucket}/smoke.txt")" != minio-smoke-object ]; then
        echo "MinIO smoke-test object did not round-trip for ${allowed_bucket}." >&2
        exit 1
    fi
    if mc cat "smoke/${denied_bucket}/smoke.txt" >/dev/null 2>&1; then
        echo "MinIO smoke-test identity unexpectedly read ${denied_bucket}." >&2
        exit 1
    fi
}

verify_identity personal_workspace_minio resume-private database-backups
verify_identity competency_minio media resume-private
verify_identity auth_api_minio auth-avatars knowledge-private
verify_identity databasus_minio database-backups auth-avatars
