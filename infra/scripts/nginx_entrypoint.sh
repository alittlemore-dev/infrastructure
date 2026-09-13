#!/usr/bin/env sh
set -eu

readonly template_file=/etc/nginx/runtime-templates/site.conf.template
readonly rendered_directory=/tmp/nginx-conf.d
readonly rendered_file="${rendered_directory}/site.conf"
readonly temporary_file="${rendered_file}.tmp"

mkdir -p "$rendered_directory"
# shellcheck disable=SC2016 # envsubst needs a literal allowlist of variable names.
envsubst '$APP_DOMAIN $PERSONAL_WORKSPACE_ACTIVE_BACKEND $AUTH_API_ACTIVE_BACKEND $COMPETENCY_ACTIVE_BACKEND $FRONTEND_ACTIVE $MINIO_DOMAIN $MINIO_PUBLIC_URL $SSL_CERT $SSL_KEY' \
    < "$template_file" > "$temporary_file"

if [ ! -s "$temporary_file" ]; then
    echo "Rendered nginx configuration is empty." >&2
    exit 1
fi

mv "$temporary_file" "$rendered_file"

nginx -t
if [ "${1:-run}" = "test" ]; then
    exit 0
fi
exec nginx -g "daemon off;"
