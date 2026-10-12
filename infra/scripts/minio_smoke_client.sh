#!/bin/sh
set -eu

# These files are deterministic fixtures created by minio_smoke.sh.
set_identity() {
    identity="$1"
    mc alias set smoke http://minio:9000 \
        "$(cat "/run/secrets/${identity}_access_key")" \
        "$(cat "/run/secrets/${identity}_secret_key")" >/dev/null
}

if [ "${1:-}" = attach-extra-policy ]; then
    set_identity minio_root
    mc admin policy attach smoke personal-workspace --user "$(cat /run/secrets/backup_minio_access_key)"
    exit 0
fi

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
set_identity backup_minio
mc ls --incomplete smoke/database-backups/system/v1/ >/dev/null
if mc cat smoke/database-backups/smoke.txt >/dev/null 2>&1; then
    echo "Backup identity unexpectedly read outside its managed namespace." >&2
    exit 1
fi
mc cp /tmp/object smoke/database-backups/system/v1/smoke.txt >/dev/null
mc cat smoke/database-backups/system/v1/smoke.txt >/dev/null
for bucket in media knowledge-private resume-private auth-avatars; do
    mc cat "smoke/${bucket}/smoke.txt" >/dev/null
    if mc cp /tmp/object "smoke/${bucket}/forbidden.txt" >/dev/null 2>&1; then
        echo "Backup identity unexpectedly wrote an application bucket." >&2
        exit 1
    fi
done
if mc cp /tmp/object smoke/database-backups/outside.txt >/dev/null 2>&1; then
    echo "Backup identity unexpectedly wrote outside its managed namespace." >&2
    exit 1
fi
