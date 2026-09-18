#!/usr/bin/env bash
set -euo pipefail

registry_token="${REGISTRY_TOKEN:?REGISTRY_TOKEN must be set}"
unset REGISTRY_TOKEN

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd -- "${script_dir}/../.." && pwd)"
platform_environment="${ALITTLEMORE_PLATFORM_ENVIRONMENT:-${repo_dir}/config/platform/production.env}"
image_registry="$(
    python3 "${script_dir}/read_env_value.py" "$platform_environment" IMAGE_REGISTRY
)"
registry_host="${image_registry%%/*}"
if [[ ! "$registry_host" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*(:[0-9]+)?$ ]]; then
    echo "IMAGE_REGISTRY contains an invalid registry host." >&2
    exit 1
fi

registry_username="${REGISTRY_USERNAME:?REGISTRY_USERNAME must be set}"
remote_host="${VALIDATED_REMOTE_HOST:?VALIDATED_REMOTE_HOST must be set}"
remote_user="${VALIDATED_REMOTE_USER:?VALIDATED_REMOTE_USER must be set}"
remote_path="${VALIDATED_REMOTE_PATH:?VALIDATED_REMOTE_PATH must be set}"
deploy_stage="${VALIDATED_DEPLOY_STAGE:?VALIDATED_DEPLOY_STAGE must be set}"
if [[ ! "$registry_username" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]]; then
    echo "REGISTRY_USERNAME contains unsupported characters." >&2
    exit 1
fi
if [[ ! "$remote_host" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*$ ]]; then
    echo "VALIDATED_REMOTE_HOST contains unsupported characters." >&2
    exit 1
fi
if [[ ! "$remote_user" =~ ^[a-z_][a-z0-9_-]*$ ]]; then
    echo "VALIDATED_REMOTE_USER contains unsupported characters." >&2
    exit 1
fi
if [[ ! "$remote_path" =~ ^/[A-Za-z0-9._/-]+$ ]] \
    || [ "$remote_path" = "/" ] \
    || [[ "$remote_path" = *"//"* ]]; then
    echo "VALIDATED_REMOTE_PATH is invalid." >&2
    exit 1
fi
case "/${remote_path#/}/" in
    */./* | */../*)
        echo "VALIDATED_REMOTE_PATH contains a traversal segment." >&2
        exit 1
        ;;
esac
if [[ ! "$deploy_stage" =~ ^incoming-[0-9]+-[0-9]+$ ]]; then
    echo "VALIDATED_DEPLOY_STAGE is invalid." >&2
    exit 1
fi
[ -f "$HOME/.ssh/alittlemore-infra" ]
[ -f "$HOME/.ssh/known_hosts" ]

docker_config_path="${remote_path}/.deploy-state/registry-auth-${deploy_stage#incoming-}"
remote_command="set -eu; umask 077; [ ! -e ${docker_config_path} ]; mkdir ${docker_config_path}; DOCKER_CONFIG=${docker_config_path} docker login ${registry_host} --username ${registry_username} --password-stdin"
printf '%s' "$registry_token" | timeout 2m ssh \
    -i "$HOME/.ssh/alittlemore-infra" \
    -o IdentitiesOnly=yes \
    -o StrictHostKeyChecking=yes \
    -o UserKnownHostsFile="$HOME/.ssh/known_hosts" \
    "$remote_user@$remote_host" \
    "$remote_command"
