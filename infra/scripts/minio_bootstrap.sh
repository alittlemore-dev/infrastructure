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
backup_access_key="$(read_secret_file "${secrets_dir}/backup_minio_access_key")"
backup_secret_key="$(read_secret_file "${secrets_dir}/backup_minio_secret_key")"
auth_api_access_key="$(read_secret_file "${secrets_dir}/auth_api_minio_access_key")"
auth_api_secret_key="$(read_secret_file "${secrets_dir}/auth_api_minio_secret_key")"

mc alias set alittlemore http://minio:9000 "$root_access_key" "$root_secret_key"

mc mb --ignore-existing alittlemore/media
mc mb --ignore-existing alittlemore/knowledge-private
mc mb --ignore-existing alittlemore/resume-private
mc mb --ignore-existing alittlemore/database-backups
mc mb --ignore-existing alittlemore/auth-avatars

mc admin policy create alittlemore personal-workspace /policies/personal-workspace.json
mc admin policy create alittlemore competency-trainer /policies/competency-trainer.json
mc admin policy create alittlemore backup /policies/backup.json
mc admin policy create alittlemore auth-api /policies/auth-api.json

mc admin user add alittlemore "$personal_workspace_access_key" "$personal_workspace_secret_key"
mc admin user add alittlemore "$competency_access_key" "$competency_secret_key"
mc admin user add alittlemore "$backup_access_key" "$backup_secret_key"
mc admin user add alittlemore "$auth_api_access_key" "$auth_api_secret_key"

mc admin policy attach alittlemore personal-workspace --user "$personal_workspace_access_key"
mc admin policy attach alittlemore competency-trainer --user "$competency_access_key"
mc admin policy attach alittlemore backup --user "$backup_access_key"
mc admin policy attach alittlemore auth-api --user "$auth_api_access_key"

# The dedicated backup identity has exactly the configured policy.
policy_info="$(mktemp)"
trap 'rm -f "$policy_info"' EXIT HUP INT TERM
mc --json admin user info alittlemore "$backup_access_key" >"$policy_info"
if ! jq -e 'type == "object" and .status == "success" and (.policyName | type == "string") and (.policyName | split(",") | length <= 32) and (.policyName | split(",") | all(. == "" or test("^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")))' "$policy_info" >/dev/null; then
    echo "Could not validate backup identity policy assignments." >&2
    exit 1
fi
attached_policies="$(jq -r '.policyName | split(",") | .[] | select(. != "" and . != "backup")' "$policy_info")"
while IFS= read -r policy_name; do
    if [ -n "$policy_name" ]; then
        mc admin policy detach alittlemore "$policy_name" --user "$backup_access_key"
    fi
done <<EOF
$attached_policies
EOF
rm -f "$policy_info"
trap - EXIT HUP INT TERM
