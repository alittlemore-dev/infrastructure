#!/usr/bin/env sh
set -eu

nginx -c /tmp/edge.conf -g 'daemon off;' &
nginx_pid=$!
trap 'kill "$nginx_pid" 2>/dev/null || true; wait "$nginx_pid" 2>/dev/null || true' EXIT

probe() {
    path="$1"
    expected="$2"
    attempt=0
    while [ "$attempt" -lt 20 ]; do
        response="$(wget -S -O /dev/null "http://127.0.0.1:8081${path}" 2>&1 || true)"
        case "$response" in
            *"HTTP/1.1 ${expected} "*) return 0 ;;
        esac
        attempt=$((attempt + 1))
        sleep 0.1
    done
    printf 'nginx boundary failed for %s (expected %s): %s\n' "$path" "$expected" "$response" >&2
    return 1
}

probe /api/personal-workspace/telegram/status 200
probe /api/personal-workspace/internal/telegram/status 404
probe '/api/personal-workspace/internal/telegram/status?probe=1' 404
probe /api/personal-workspace/internal/unknown 404
