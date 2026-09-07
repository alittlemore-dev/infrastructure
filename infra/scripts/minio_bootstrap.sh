#!/bin/sh
set -eu

read_secret_file() {
    file_path="$1"

    if [ ! -r "$file_path" ]; then
        echo "MinIO bootstrap secret file is not readable: ${file_path}" >&2
        exit 1
    fi

    cat "$file_path"
}

secrets_dir="${MINIO_BOOTSTRAP_SECRETS_DIR:-/run/secrets}"
root_access_key="$(read_secret_file "${secrets_dir}/minio_root_access_key")"
root_secret_key="$(read_secret_file "${secrets_dir}/minio_root_secret_key")"
personal_workspace_access_key="$(read_secret_file "${secrets_dir}/personal_workspace_minio_access_key")"
personal_workspace_secret_key="$(read_secret_file "${secrets_dir}/personal_workspace_minio_secret_key")"
competency_access_key="$(read_secret_file "${secrets_dir}/competency_minio_access_key")"
competency_secret_key="$(read_secret_file "${secrets_dir}/competency_minio_secret_key")"
databasus_access_key="$(read_secret_file "${secrets_dir}/databasus_minio_access_key")"
databasus_secret_key="$(read_secret_file "${secrets_dir}/databasus_minio_secret_key")"

mc alias set alittlemore http://minio:9000 "$root_access_key" "$root_secret_key"

mc mb --ignore-existing alittlemore/media
mc mb --ignore-existing alittlemore/knowledge-private
mc mb --ignore-existing alittlemore/database-backups

mc admin policy create alittlemore personal-workspace /policies/personal-workspace.json
mc admin policy create alittlemore competency-trainer /policies/competency-trainer.json
mc admin policy create alittlemore databasus /policies/databasus.json

mc admin user add alittlemore "$personal_workspace_access_key" "$personal_workspace_secret_key"
mc admin user add alittlemore "$competency_access_key" "$competency_secret_key"
mc admin user add alittlemore "$databasus_access_key" "$databasus_secret_key"

mc admin policy attach alittlemore personal-workspace --user "$personal_workspace_access_key"
mc admin policy attach alittlemore competency-trainer --user "$competency_access_key"
mc admin policy attach alittlemore databasus --user "$databasus_access_key"
