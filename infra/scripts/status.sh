#!/usr/bin/env bash
set -euo pipefail

repo_dir=""
if [ "$#" -gt 0 ]; then
    if [ "$#" -ne 2 ] || [ "$1" != "--repo-dir" ] || [ -z "$2" ]; then
        echo "Usage: $0 [--repo-dir PATH]" >&2
        exit 2
    fi
    repo_dir="$2"
fi
if [ -z "$repo_dir" ]; then
    script_dir="$(cd -- "${BASH_SOURCE[0]%/*}" && pwd)"
    repo_dir="$(cd -- "${script_dir}/../.." && pwd)"
fi

state_root="$repo_dir"
runtime_root_marker="${repo_dir}/.alittlemore-runtime-root"
if [ -f "$runtime_root_marker" ] && [ ! -L "$runtime_root_marker" ]; then
    IFS= read -r state_root <"$runtime_root_marker"
    if [[ "$state_root" != /* ]] || [ "$state_root" = / ]; then
        echo "Runtime-root marker contains an invalid path." >&2
        exit 1
    fi
fi
state_dir="${state_root}/.deploy-state"
active_marker="${state_dir}/active-slot"

if [ ! -e "$active_marker" ] && [ ! -L "$active_marker" ]; then
    printf '%s\n' "Active deployment: none"
elif [ ! -f "$active_marker" ] || [ -L "$active_marker" ]; then
    echo "Active-slot marker must be a regular file." >&2
    exit 1
else
    active_slot=""
    active_release=""
    extra_field=""
    read -r active_slot active_release extra_field <"$active_marker"
    if [[ ! "$active_slot" =~ ^(blue|green)$ ]] \
        || { [ -n "$active_release" ] && [[ ! "$active_release" =~ ^release-[0-9]+-[0-9]+$ ]]; } \
        || [ -n "$extra_field" ]; then
        echo "Active-slot marker contains invalid deployment metadata." >&2
        exit 1
    fi
    printf 'Active slot: %s\n' "$active_slot"
    if [ -n "$active_release" ]; then
        printf 'Active release: %s\n' "$active_release"
    else
        printf '%s\n' "Active release: local/manual"
    fi
fi

if ! command -v docker >/dev/null 2>&1; then
    echo "docker could not be found; container status is unavailable." >&2
    exit 1
fi
docker ps \
    --filter label=com.docker.compose.project=alittlemore-infra \
    --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'
