#!/usr/bin/env bash
set -euo pipefail

verify_remote_root() {
    local deploy_path="$1"
    local stage_name="$2"
    local sentinel
    local state_path
    local releases_path
    local certificates_path
    local stage_path

    case "$deploy_path" in
        /*/alittlemore-infra) ;;
        *)
            echo "REMOTE_PATH must be an absolute dedicated path ending in /alittlemore-infra." >&2
            exit 1
            ;;
    esac
    [ -d "$deploy_path" ]
    [ ! -L "$deploy_path" ]
    [ "$(readlink -f "$deploy_path")" = "$deploy_path" ]
    sentinel="$deploy_path/.alittlemore-infra-deploy-root"
    [ -f "$sentinel" ]
    [ ! -L "$sentinel" ]
    [ "$(cat "$sentinel")" = "alittlemore-infra" ]
    [ "$(stat -c '%U' "$deploy_path")" = "$(id -un)" ]
    [ "$(stat -c '%U' "$sentinel")" = "$(id -un)" ]
    [ "$(stat -c '%a' "$deploy_path")" = "700" ]
    [ "$(stat -c '%a' "$sentinel")" = "600" ]
    [[ "$stage_name" =~ ^incoming-[0-9]+-[0-9]+$ ]]
    command -v flock >/dev/null

    state_path="$deploy_path/.deploy-state"
    if [ ! -e "$state_path" ]; then
        mkdir "$state_path"
    fi
    [ -d "$state_path" ]
    [ ! -L "$state_path" ]
    [ "$(readlink -f "$state_path")" = "$state_path" ]
    [ "$(stat -c '%U' "$state_path")" = "$(id -un)" ]
    chmod 700 "$state_path"

    releases_path="$state_path/releases"
    if [ ! -e "$releases_path" ]; then
        mkdir "$releases_path"
    fi
    [ -d "$releases_path" ]
    [ ! -L "$releases_path" ]
    [ "$(readlink -f "$releases_path")" = "$releases_path" ]
    [ "$(stat -c '%U' "$releases_path")" = "$(id -un)" ]
    chmod 700 "$releases_path"

    certificates_path="$deploy_path/certificates"
    if [ ! -e "$certificates_path" ]; then
        mkdir "$certificates_path"
    fi
    [ -d "$certificates_path" ]
    [ ! -L "$certificates_path" ]
    [ "$(readlink -f "$certificates_path")" = "$certificates_path" ]
    [ "$(stat -c '%U' "$certificates_path")" = "$(id -un)" ]
    chmod 751 "$certificates_path"

    stage_path="$state_path/$stage_name"
    [ ! -e "$stage_path" ]
    mkdir "$stage_path"
    chmod 700 "$stage_path"
}

if [ "${1:-}" = "--remote" ]; then
    shift
    verify_remote_root "$@"
    exit 0
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
timeout 2m ssh \
    -i "$HOME/.ssh/alittlemore-infra" \
    -o IdentitiesOnly=yes \
    -o StrictHostKeyChecking=yes \
    -o UserKnownHostsFile="$HOME/.ssh/known_hosts" \
    "$VALIDATED_REMOTE_USER@$VALIDATED_REMOTE_HOST" \
    'bash -s' -- --remote "$VALIDATED_REMOTE_PATH" "$VALIDATED_DEPLOY_STAGE" \
    <"$script_dir/deploy_verify_remote_root.sh"
