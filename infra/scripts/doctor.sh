#!/usr/bin/env bash
set -euo pipefail

profile="${1:-quality}"
case "$profile" in
    quality | runtime) ;;
    *)
        echo "Usage: $0 {quality|runtime}" >&2
        exit 2
        ;;
esac

quality_commands=(bash docker make openssl python3 ssh-keygen)
runtime_commands=(curl flock mv readlink rsync sops stat timeout uname)
missing=0

check_commands() {
    local command_name
    for command_name in "$@"; do
        if command -v "$command_name" >/dev/null 2>&1; then
            printf 'OK      %s\n' "$command_name"
        else
            printf 'MISSING %s\n' "$command_name"
            missing=1
        fi
    done
}

check_commands "${quality_commands[@]}"
if [ "$profile" = runtime ]; then
    check_commands "${runtime_commands[@]}"
fi

if command -v docker >/dev/null 2>&1; then
    compose_version="$(docker compose version --short 2>/dev/null || true)"
    normalized_version="${compose_version#v}"
    normalized_version="${normalized_version%%-*}"
    if [[ "$normalized_version" =~ ^([0-9]+)\.([0-9]+)\.[0-9]+$ ]] \
        && ((BASH_REMATCH[1] > 2 || (BASH_REMATCH[1] == 2 && BASH_REMATCH[2] >= 24))); then
        printf 'OK      docker compose %s\n' "$compose_version"
    else
        printf 'MISSING Docker Compose v2.24.0 or newer (found %s)\n' "${compose_version:-none}"
        missing=1
    fi
    if docker info >/dev/null 2>&1; then
        printf 'OK      Docker daemon\n'
    else
        printf 'MISSING reachable Docker daemon\n'
        missing=1
    fi
fi

if [ "$profile" = runtime ] && command -v uname >/dev/null 2>&1; then
    if [ "$(uname -s)" = Linux ]; then
        printf 'OK      Linux production host\n'
    else
        printf 'MISSING Linux production host\n'
        missing=1
    fi
    for command_name in stat readlink mv; do
        if command -v "$command_name" >/dev/null 2>&1 \
            && "$command_name" --version >/dev/null 2>&1; then
            printf 'OK      GNU %s\n' "$command_name"
        elif command -v "$command_name" >/dev/null 2>&1; then
            printf 'MISSING GNU %s\n' "$command_name"
            missing=1
        fi
    done
fi

if [ "$missing" -ne 0 ]; then
    printf '%s\n' "Install the missing system prerequisites; Make never installs host packages." >&2
    exit 1
fi

if [ "$profile" = quality ]; then
    printf '%s\n' "Quality prerequisites are available. SOPS and age-keygen are managed by make tools."
else
    printf '%s\n' "Runtime prerequisites are available."
fi
