#!/usr/bin/env sh
set -eu

readonly failure_limit="${NGINX_LIVENESS_FAILURE_LIMIT:?NGINX_LIVENESS_FAILURE_LIMIT must be set}"
readonly failure_file=/tmp/nginx-liveness-failures
readonly temporary_failure_file="${failure_file}.$$"

case "$failure_limit" in
    0 | *[!0-9]*)
        echo "NGINX_LIVENESS_FAILURE_LIMIT must be a positive integer." >&2
        exit 1
        ;;
esac

if wget -q -T 3 -O /dev/null http://127.0.0.1:8080/nginx-healthz; then
    rm -f "$failure_file"
    exit 0
fi

failure_count=0
if [ -f "$failure_file" ]; then
    IFS= read -r failure_count <"$failure_file" || failure_count=0
fi
case "$failure_count" in
    '' | *[!0-9]*)
        failure_count=0
        ;;
esac

failure_count=$((failure_count + 1))
printf '%s\n' "$failure_count" >"$temporary_failure_file"
mv "$temporary_failure_file" "$failure_file"

echo "nginx local liveness probe failed (${failure_count}/${failure_limit})." >&2
if [ "$failure_count" -ge "$failure_limit" ]; then
    echo "nginx local liveness failure limit reached; terminating PID 1 for container recovery." >&2
    kill -TERM 1
fi

exit 1
