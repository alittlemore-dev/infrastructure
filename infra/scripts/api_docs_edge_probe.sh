#!/usr/bin/env sh
set -eu

nginx -c /tmp/edge.conf -g 'daemon off;' &
nginx_pid=$!
trap 'kill "$nginx_pid" 2>/dev/null || true; wait "$nginx_pid" 2>/dev/null || true' EXIT

probe() {
    port="$1"
    method="$2"
    path="$3"
    expected="$4"
    body="$5"
    attempt=0
    while [ "$attempt" -lt 20 ]; do
        response="$(
            {
                printf '%s\r\n' "$method $path HTTP/1.1" 'Host: edge.test' 'Connection: close' ''
                sleep 0.1
            } | nc -w 2 127.0.0.1 "$port" || true
        )"
        case "$response" in
            *"HTTP/1.1 $expected "*"$body"*) return 0 ;;
        esac
        attempt=$((attempt + 1))
        sleep 0.1
    done
    printf 'API docs edge failed for %s %s: expected %s %s; got %s\n' "$method" "$path" "$expected" "$body" "$response" >&2
    return 1
}

probe 8081 GET /api/docs 200 /api/docs
probe 8081 GET /api/docs/initializer.js 200 /api/docs/initializer.js
probe 8081 GET /api/openapi.json 200 /api/openapi.json
probe 8081 GET /api/docsunknown 404 ''
probe 8081 GET /openapi/auth.json 404 ''
probe 18084 GET /openapi/auth.json 200 /api/auth/docs/openapi.json
probe 18084 GET /openapi/competency.json 200 /api/docs/openapi.json
probe 18084 GET /openapi/personal-workspace.json 200 /api/docs/openapi.json
probe 18084 GET /openapi/i18n.json 200 /api/i18n/docs/openapi.json
probe 18084 HEAD /openapi/auth.json 200 ''
probe 18084 POST /openapi/auth.json 405 ''
probe 18084 GET /api/auth/account/me 404 ''
probe 18084 GET /openapi/unknown.json 404 ''
