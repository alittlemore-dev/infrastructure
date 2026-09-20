#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd -- "${script_dir}/../.." && pwd)"

usage() {
    cat >&2 <<'EOF'
Usage: bootstrap_sops_secrets.sh \
  --platform-env /absolute/path/platform.production.env \
  --personal-workspace-env /absolute/path/personal-workspace.production.env \
  --competency-trainer-env /absolute/path/competency-trainer.production.env \
  --auth-api-env /absolute/path/auth-api.production.env \
  --i18n-env /absolute/path/i18n.production.env \
  --age-recipient age1... \
  --age-recipient age1...
EOF
}

platform_env=""
personal_workspace_env=""
competency_trainer_env=""
auth_api_env=""
i18n_env=""
recipient_args=()

while [ "$#" -gt 0 ]; do
    case "$1" in
        --platform-env|--personal-workspace-env|--competency-trainer-env|--auth-api-env|--i18n-env|--age-recipient)
            if [ "$#" -lt 2 ]; then
                usage
                exit 2
            fi
            ;;
    esac
    case "$1" in
        --platform-env)
            platform_env="${2:-}"
            shift 2
            ;;
        --personal-workspace-env)
            personal_workspace_env="${2:-}"
            shift 2
            ;;
        --competency-trainer-env)
            competency_trainer_env="${2:-}"
            shift 2
            ;;
        --auth-api-env)
            auth_api_env="${2:-}"
            shift 2
            ;;
        --i18n-env)
            i18n_env="${2:-}"
            shift 2
            ;;
        --age-recipient)
            recipient_args+=(--age-recipient "${2:-}")
            shift 2
            ;;
        *)
            usage
            exit 2
            ;;
    esac
done

if [ -z "$platform_env" ] \
    || [ -z "$personal_workspace_env" ] \
    || [ -z "$competency_trainer_env" ] \
    || [ -z "$auth_api_env" ] \
    || [ -z "$i18n_env" ] \
    || [ "${#recipient_args[@]}" -lt 4 ]; then
    usage
    exit 2
fi

python3 "$script_dir/build_sops_documents.py" \
    --manifest "$repo_dir/infra/deploy/runtime-secrets.manifest.json" \
    --repo-dir "$repo_dir" \
    --sops-binary "${SOPS_BINARY:-sops}" \
    --source-env "platform=${platform_env}" \
    --source-env "personal-workspace=${personal_workspace_env}" \
    --source-env "competency-trainer=${competency_trainer_env}" \
    --source-env "auth-api=${auth_api_env}" \
    --source-env "i18n=${i18n_env}" \
    "${recipient_args[@]}"
