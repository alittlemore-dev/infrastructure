#!/usr/bin/env bash
set -euo pipefail

activate_remote_payload() {
    local deploy_path="$1"
    local stage_name="$2"
    local issue_certificates="$3"
    local sentinel
    local state_path
    local releases_path
    local certificates_path
    local stage_path
    local lock_file
    local stale_stage
    local stale_name
    local release_name
    local release_path
    local current_link
    local old_current_target=""
    local old_current_path=""
    local active_slot_file
    local committed_slot=""
    local committed_release=""
    local extra_field=""
    local committed_target
    local committed_path
    local recovery_previous
    local recovery_current
    local previous_link
    local existing_previous_target
    local existing_previous_path=""
    local temporary_current
    local temporary_previous=""
    local deployment_status=0

    [[ "$stage_name" =~ ^incoming-[0-9]+-[0-9]+$ ]]
    case "$issue_certificates" in
        true | false) ;;
        *) exit 1 ;;
    esac
    [ -d "$deploy_path" ]
    [ ! -L "$deploy_path" ]
    [ "$(readlink -f "$deploy_path")" = "$deploy_path" ]
    [ "$(stat -c '%U' "$deploy_path")" = "$(id -un)" ]
    [ "$(stat -c '%a' "$deploy_path")" = "700" ]
    sentinel="$deploy_path/.alittlemore-infra-deploy-root"
    [ -f "$sentinel" ]
    [ ! -L "$sentinel" ]
    [ "$(cat "$sentinel")" = "alittlemore-infra" ]
    [ "$(stat -c '%U' "$sentinel")" = "$(id -un)" ]
    [ "$(stat -c '%a' "$sentinel")" = "600" ]

    state_path="$deploy_path/.deploy-state"
    [ -d "$state_path" ]
    [ ! -L "$state_path" ]
    [ "$(readlink -f "$state_path")" = "$state_path" ]
    [ "$(stat -c '%U' "$state_path")" = "$(id -un)" ]
    [ "$(stat -c '%a' "$state_path")" = "700" ]
    releases_path="$state_path/releases"
    [ -d "$releases_path" ]
    [ ! -L "$releases_path" ]
    [ "$(readlink -f "$releases_path")" = "$releases_path" ]
    [ "$(stat -c '%U' "$releases_path")" = "$(id -un)" ]
    [ "$(stat -c '%a' "$releases_path")" = "700" ]
    certificates_path="$deploy_path/certificates"
    [ -d "$certificates_path" ]
    [ ! -L "$certificates_path" ]
    [ "$(readlink -f "$certificates_path")" = "$certificates_path" ]
    [ "$(stat -c '%U' "$certificates_path")" = "$(id -un)" ]
    [ "$(stat -c '%a' "$certificates_path")" = "751" ]
    stage_path="$state_path/$stage_name"
    [ -d "$stage_path" ]
    [ ! -L "$stage_path" ]
    [ "$(readlink -f "$stage_path")" = "$stage_path" ]
    [ "$(stat -c '%U' "$stage_path")" = "$(id -un)" ]
    [ "$(stat -c '%a' "$stage_path")" = "700" ]

    lock_file="$state_path/runtime.lock"
    [ ! -L "$lock_file" ]
    if [ -e "$lock_file" ]; then
        [ -f "$lock_file" ]
        [ "$(stat -c '%U' "$lock_file")" = "$(id -un)" ]
    fi
    exec 9>"$lock_file"
    chmod 600 "$lock_file"
    if ! flock -n 9; then
        echo "Another infrastructure mutation is already running." >&2
        exit 1
    fi
    export ALITTLEMORE_RUNTIME_LOCK_HELD=1

    while IFS= read -r stale_stage; do
        [ "$stale_stage" = "$stage_path" ] && continue
        stale_name="$(basename "$stale_stage")"
        [[ "$stale_name" =~ ^incoming-[0-9]+-[0-9]+$ ]]
        [ -d "$stale_stage" ]
        [ ! -L "$stale_stage" ]
        [ "$(readlink -f "$stale_stage")" = "$stale_stage" ]
        [ "$(stat -c '%U' "$stale_stage")" = "$(id -un)" ]
        [ "$(stat -c '%a' "$stale_stage")" = "700" ]
        rm -rf -- "$stale_stage"
    done < <(find "$state_path" -mindepth 1 -maxdepth 1 -type d -name 'incoming-*' -print)

    for required_path in \
        .sops.yaml \
        Makefile \
        docker-compose.yml \
        config/platform/production.env \
        config/personal-workspace/production.env \
        config/competency-trainer/production.env \
        secrets/platform/production.sops.yaml \
        secrets/personal-workspace/production.sops.yaml \
        secrets/competency-trainer/production.sops.yaml \
        infra/deploy/runtime-config.manifest.json \
        infra/deploy/runtime-secrets.manifest.json \
        infra/scripts/run.sh; do
        [ -f "$stage_path/$required_path" ]
        [ ! -L "$stage_path/$required_path" ]
    done
    [ ! -e "$stage_path/.alittlemore-runtime-root" ]
    [ ! -L "$stage_path/.alittlemore-runtime-root" ]
    printf '%s\n' "$deploy_path" >"$stage_path/.alittlemore-runtime-root"
    chmod 600 "$stage_path/.alittlemore-runtime-root"
    [ ! -e "$stage_path/infra/nginx/certs" ]
    [ ! -L "$stage_path/infra/nginx/certs" ]
    ln -s "$certificates_path" "$stage_path/infra/nginx/certs"

    release_name="release-${stage_name#incoming-}"
    [[ "$release_name" =~ ^release-[0-9]+-[0-9]+$ ]]
    release_path="$releases_path/$release_name"
    [ ! -e "$release_path" ]
    [ ! -L "$release_path" ]
    current_link="$deploy_path/current"
    if [ -e "$current_link" ] || [ -L "$current_link" ]; then
        [ -L "$current_link" ]
        old_current_target="$(readlink "$current_link")"
        [[ "$old_current_target" =~ ^\.deploy-state/releases/release-[0-9]+-[0-9]+$ ]]
        old_current_path="$deploy_path/$old_current_target"
        [ -d "$old_current_path" ]
        [ ! -L "$old_current_path" ]
        [ "$(readlink -f "$old_current_path")" = "$old_current_path" ]
    fi

    active_slot_file="$state_path/active-slot"
    if [ -e "$active_slot_file" ] || [ -L "$active_slot_file" ]; then
        [ -f "$active_slot_file" ]
        [ ! -L "$active_slot_file" ]
        [ "$(stat -c '%U' "$active_slot_file")" = "$(id -un)" ]
        [ "$(stat -c '%a' "$active_slot_file")" = "600" ]
        read -r committed_slot committed_release extra_field <"$active_slot_file"
        [[ "$committed_slot" =~ ^(blue|green)$ ]]
        [ -z "$extra_field" ]
    fi
    if [ -n "$committed_release" ]; then
        [[ "$committed_release" =~ ^release-[0-9]+-[0-9]+$ ]]
        committed_target=".deploy-state/releases/$committed_release"
        committed_path="$deploy_path/$committed_target"
        [ -d "$committed_path" ]
        [ ! -L "$committed_path" ]
        [ "$(readlink -f "$committed_path")" = "$committed_path" ]
        if [ "$old_current_target" != "$committed_target" ]; then
            if [ -n "$old_current_target" ]; then
                recovery_previous="$deploy_path/.previous-recovery-$release_name"
                [ ! -e "$recovery_previous" ]
                [ ! -L "$recovery_previous" ]
                ln -s "$old_current_target" "$recovery_previous"
                mv -Tf "$recovery_previous" "$deploy_path/previous"
            fi
            recovery_current="$deploy_path/.current-recovery-$release_name"
            [ ! -e "$recovery_current" ]
            [ ! -L "$recovery_current" ]
            ln -s "$committed_target" "$recovery_current"
            mv -Tf "$recovery_current" "$current_link"
            old_current_target="$committed_target"
            old_current_path="$committed_path"
            echo "Reconciled current to the last runtime-committed payload."
        fi
    fi

    previous_link="$deploy_path/previous"
    if [ -e "$previous_link" ] || [ -L "$previous_link" ]; then
        [ -L "$previous_link" ]
        existing_previous_target="$(readlink "$previous_link")"
        [[ "$existing_previous_target" =~ ^\.deploy-state/releases/release-[0-9]+-[0-9]+$ ]]
        existing_previous_path="$deploy_path/$existing_previous_target"
        [ -d "$existing_previous_path" ]
        [ ! -L "$existing_previous_path" ]
        [ "$(readlink -f "$existing_previous_path")" = "$existing_previous_path" ]
    fi

    prune_runtime_releases() {
        local keep_one="$1"
        local keep_two="${2:-}"
        local keep_three="${3:-}"
        local candidate
        local candidate_name

        while IFS= read -r candidate; do
            candidate_name="$(basename "$candidate")"
            [[ "$candidate_name" =~ ^release-[0-9]+-[0-9]+$ ]] || continue
            case "$candidate" in
                "$keep_one" | "$keep_two" | "$keep_three") continue ;;
            esac
            [ -d "$candidate" ]
            [ ! -L "$candidate" ]
            [ "$(readlink -f "$candidate")" = "$candidate" ]
            [ "$(stat -c '%U' "$candidate")" = "$(id -un)" ]
            rm -rf -- "$candidate"
        done < <(find "$releases_path" -mindepth 1 -maxdepth 1 -type d -name 'release-*' -print)
    }

    # shellcheck disable=SC2329 # Called by the EXIT/signal trap handler.
    runtime_release_is_committed() {
        local active_slot=""
        local active_release=""
        local extra=""

        [ -f "$active_slot_file" ] || return 1
        [ ! -L "$active_slot_file" ] || return 1
        [ "$(stat -c '%U' "$active_slot_file")" = "$(id -un)" ] || return 1
        [ "$(stat -c '%a' "$active_slot_file")" = "600" ] || return 1
        read -r active_slot active_release extra <"$active_slot_file"
        [[ "$active_slot" =~ ^(blue|green)$ ]] || return 1
        [ -z "$extra" ] || return 1
        [ "$active_release" = "$release_name" ]
    }

    temporary_current="$deploy_path/.current-$release_name"
    [ ! -e "$temporary_current" ]
    [ ! -L "$temporary_current" ]
    mv "$stage_path" "$release_path"
    ln -s ".deploy-state/releases/$release_name" "$temporary_current"
    if [ -n "$old_current_target" ]; then
        temporary_previous="$deploy_path/.previous-$release_name"
        [ ! -e "$temporary_previous" ]
        [ ! -L "$temporary_previous" ]
        ln -s "$old_current_target" "$temporary_previous"
    fi

    # shellcheck disable=SC2329 # Called by the EXIT/signal trap handler.
    cleanup_pointer_temporaries() {
        if [ -L "$temporary_current" ]; then
            rm -f -- "$temporary_current"
        fi
        if [ -n "$temporary_previous" ] && [ -L "$temporary_previous" ]; then
            rm -f -- "$temporary_previous"
        fi
    }

    commit_payload_pointer() {
        if [ -L "$temporary_current" ]; then
            mv -Tf "$temporary_current" "$current_link"
        fi
        [ -L "$current_link" ]
        [ "$(readlink "$current_link")" = ".deploy-state/releases/$release_name" ]
        if [ -n "$temporary_previous" ] && [ -L "$temporary_previous" ]; then
            mv -Tf "$temporary_previous" "$deploy_path/previous" || \
                echo "Current is committed, but the previous payload pointer could not be updated." >&2
        fi
    }

    # shellcheck disable=SC2329 # Invoked dynamically by the traps below.
    handle_activation_exit() {
        local activation_status="$1"

        trap - EXIT HUP INT TERM
        if runtime_release_is_committed; then
            commit_payload_pointer || \
                echo "Runtime committed, but current could not be reconciled automatically." >&2
            prune_runtime_releases "$release_path" "$old_current_path" || \
                echo "Could not prune older runtime payloads after interrupted activation." >&2
        else
            cleanup_pointer_temporaries
            prune_runtime_releases \
                "$release_path" \
                "$old_current_path" \
                "$existing_previous_path" || \
                echo "Could not prune older runtime payloads after interrupted activation." >&2
        fi
        exit "$activation_status"
    }
    trap 'handle_activation_exit $?' EXIT
    trap 'handle_activation_exit 130' HUP INT TERM

    cd "$release_path"
    export ALITTLEMORE_RELEASE_ID="$release_name"
    if [ "$issue_certificates" = true ]; then
        timeout 15m make certbot-issue || deployment_status=$?
    fi
    if [ "$deployment_status" -eq 0 ]; then
        timeout 30m make run || deployment_status=$?
    fi
    if [ "$deployment_status" -ne 0 ]; then
        exit "$deployment_status"
    fi
    commit_payload_pointer
    trap - EXIT HUP INT TERM
    prune_runtime_releases "$release_path" "$old_current_path" || \
        echo "Deployment committed, but older runtime payloads could not be fully pruned." >&2
}

if [ "${1:-}" = "--remote" ]; then
    shift
    activate_remote_payload "$@"
    exit 0
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
requested_certificate_issue="${ISSUE_CERTIFICATES:?ISSUE_CERTIFICATES must be set}"
timeout 50m ssh \
    -i "$HOME/.ssh/alittlemore-infra" \
    -o IdentitiesOnly=yes \
    -o StrictHostKeyChecking=yes \
    -o UserKnownHostsFile="$HOME/.ssh/known_hosts" \
    "$VALIDATED_REMOTE_USER@$VALIDATED_REMOTE_HOST" \
    'bash -s' -- \
    --remote \
    "$VALIDATED_REMOTE_PATH" \
    "$VALIDATED_DEPLOY_STAGE" \
    "$requested_certificate_issue" \
    <"$script_dir/deploy_activate_payload.sh"
