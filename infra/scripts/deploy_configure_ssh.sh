#!/usr/bin/env bash
set -euo pipefail

[[ "$REMOTE_HOST" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*$ ]]
[[ "$REMOTE_USER" =~ ^[a-z_][a-z0-9_-]*$ ]]
[[ "$REMOTE_PATH" =~ ^/[A-Za-z0-9._/-]+$ ]]
[[ "$REMOTE_PATH" != "/" ]]
[[ "$REMOTE_PATH" != *"//"* ]]
case "/${REMOTE_PATH#/}/" in
    */./* | */../*) exit 1 ;;
esac
[[ "$SSH_HOST_KEY_FINGERPRINT" =~ ^SHA256:[A-Za-z0-9+/=]+$ ]]

umask 077
mkdir -p "$HOME/.ssh"
printf '%s\n' "$SSH_PRIVATE_KEY" >"$HOME/.ssh/alittlemore-infra"
timeout 30s ssh-keyscan "$REMOTE_HOST" >"$HOME/.ssh/known_hosts.candidate"
: >"$HOME/.ssh/known_hosts"
while IFS= read -r host_key; do
    printf '%s\n' "$host_key" >"$HOME/.ssh/known_host.line"
    presented_fingerprint="$(
        ssh-keygen -lf "$HOME/.ssh/known_host.line" -E sha256 | awk '{print $2}'
    )"
    if [ "$presented_fingerprint" = "$SSH_HOST_KEY_FINGERPRINT" ]; then
        printf '%s\n' "$host_key" >>"$HOME/.ssh/known_hosts"
    fi
done <"$HOME/.ssh/known_hosts.candidate"
[ -s "$HOME/.ssh/known_hosts" ]
rm -f "$HOME/.ssh/known_hosts.candidate" "$HOME/.ssh/known_host.line"
chmod 600 "$HOME/.ssh/alittlemore-infra" "$HOME/.ssh/known_hosts"

{
    printf 'VALIDATED_REMOTE_HOST=%s\n' "$REMOTE_HOST"
    printf 'VALIDATED_REMOTE_PATH=%s\n' "$REMOTE_PATH"
    printf 'VALIDATED_REMOTE_USER=%s\n' "$REMOTE_USER"
    printf 'VALIDATED_DEPLOY_STAGE=incoming-%s-%s\n' \
        "$GITHUB_RUN_ID" "$GITHUB_RUN_ATTEMPT"
} >>"$GITHUB_ENV"
