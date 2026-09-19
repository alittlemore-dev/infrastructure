#!/usr/bin/env bash
set -euo pipefail

cleanup_remote_payload() {
    local deploy_path="$1"
    local stage_name="$2"
    local sentinel
    local state_path
    local lock_file
    local stage_path

    [[ "$stage_name" =~ ^incoming-[0-9]+-[0-9]+$ ]]
    [[ "$deploy_path" =~ ^/[A-Za-z0-9._/-]+$ ]]
    [ "$deploy_path" != "/" ]
    [[ "$deploy_path" != *"//"* ]]
    case "/${deploy_path#/}/" in
        */./* | */../*) exit 1 ;;
    esac
    [ -d "$deploy_path" ]
    [ ! -L "$deploy_path" ]
    [ "$(readlink -f "$deploy_path")" = "$deploy_path" ]
    sentinel="$deploy_path/.alittlemore-infra-deploy-root"
    [ -f "$sentinel" ]
    [ ! -L "$sentinel" ]
    [ "$(cat "$sentinel")" = "alittlemore-infra" ]
    [ "$(stat -c '%U' "$deploy_path")" = "$(id -un)" ]
    [ "$(stat -c '%a' "$deploy_path")" = "700" ]

    state_path="$deploy_path/.deploy-state"
    [ -d "$state_path" ]
    [ ! -L "$state_path" ]
    [ "$(readlink -f "$state_path")" = "$state_path" ]
    [ "$(stat -c '%U' "$state_path")" = "$(id -un)" ]
    [ "$(stat -c '%a' "$state_path")" = "700" ]

    lock_file="$state_path/runtime.lock"
    [ ! -L "$lock_file" ]
    if [ -e "$lock_file" ]; then
        [ -f "$lock_file" ]
        [ "$(stat -c '%U' "$lock_file")" = "$(id -un)" ]
    fi
    exec 9>"$lock_file"
    chmod 600 "$lock_file"
    if ! flock -n 9; then
        echo "Runtime lock is busy; leaving the staged payload for manual inspection." >&2
        exit 0
    fi

    stage_path="$state_path/$stage_name"
    if [ ! -e "$stage_path" ] && [ ! -L "$stage_path" ]; then
        exit 0
    fi
    [ -d "$stage_path" ]
    [ ! -L "$stage_path" ]
    [ "$(readlink -f "$stage_path")" = "$stage_path" ]
    [ "$(stat -c '%U' "$stage_path")" = "$(id -un)" ]
    [ "$(stat -c '%a' "$stage_path")" = "700" ]
    rm -rf -- "$stage_path"
}

if [ "${1:-}" = "--remote" ]; then
    shift
    cleanup_remote_payload "$@"
    exit 0
fi

if [ -z "${VALIDATED_REMOTE_HOST:-}" ] \
    || [ -z "${VALIDATED_REMOTE_USER:-}" ] \
    || [ -z "${VALIDATED_REMOTE_PATH:-}" ] \
    || [ -z "${VALIDATED_DEPLOY_STAGE:-}" ] \
    || [ ! -f "$HOME/.ssh/alittlemore-infra" ] \
    || [ ! -f "$HOME/.ssh/known_hosts" ]; then
    exit 0
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
timeout 2m ssh \
    -i "$HOME/.ssh/alittlemore-infra" \
    -o IdentitiesOnly=yes \
    -o StrictHostKeyChecking=yes \
    -o UserKnownHostsFile="$HOME/.ssh/known_hosts" \
    "$VALIDATED_REMOTE_USER@$VALIDATED_REMOTE_HOST" \
    'bash -s' -- \
    --remote \
    "$VALIDATED_REMOTE_PATH" \
    "$VALIDATED_DEPLOY_STAGE" \
    <"$script_dir/deploy_cleanup_payload.sh"
