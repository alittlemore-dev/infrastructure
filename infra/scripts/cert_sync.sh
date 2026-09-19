#!/bin/sh
set -eu
umask 077

readonly certificate_name="${TLS_CERTIFICATE_NAME:?TLS_CERTIFICATE_NAME must be set}"
readonly app_domain="${APP_DOMAIN:?APP_DOMAIN must be set}"
readonly minio_domain="${MINIO_DOMAIN:?MINIO_DOMAIN must be set}"
readonly source_directory="/etc/letsencrypt/live/${certificate_name}"
readonly releases_directory=/certs/releases
release_name="$(date -u +%Y%m%dT%H%M%SZ)-$(openssl rand -hex 12)"
readonly release_name
readonly staging_directory="${releases_directory}/.${release_name}"
readonly release_directory="${releases_directory}/${release_name}"
readonly temporary_link="/certs/.current-${release_name}"

cleanup() {
    rm -rf "$staging_directory"
    rm -f "$temporary_link"
}
trap cleanup EXIT HUP INT TERM

if [ ! -r "${source_directory}/fullchain.pem" ] || [ ! -r "${source_directory}/privkey.pem" ]; then
    echo "No readable certbot certificate found for ${certificate_name}; keeping the current release." >&2
    exit 1
fi

mkdir -p "$releases_directory"
chmod 751 "$releases_directory"
mkdir "$staging_directory"
cp "${source_directory}/fullchain.pem" "${staging_directory}/fullchain.pem"
cp "${source_directory}/privkey.pem" "${staging_directory}/privkey.pem"

openssl x509 -in "${staging_directory}/fullchain.pem" -noout -checkend 0 >/dev/null
openssl pkey -in "${staging_directory}/privkey.pem" -noout >/dev/null
for hostname in \
    "$app_domain" \
    "$minio_domain" \
    "agent.${app_domain}"; do
    openssl x509 -in "${staging_directory}/fullchain.pem" -noout -checkhost "$hostname" >/dev/null
done
certificate_public_key="$({
    openssl x509 -in "${staging_directory}/fullchain.pem" -pubkey -noout \
        | openssl pkey -pubin -outform DER
} | sha256sum | cut -d ' ' -f 1)"
private_public_key="$({
    openssl pkey -in "${staging_directory}/privkey.pem" -pubout -outform DER
} | sha256sum | cut -d ' ' -f 1)"
if [ "$certificate_public_key" != "$private_public_key" ]; then
    echo "Certificate and private key do not match; keeping the current release." >&2
    exit 1
fi

chmod 644 "${staging_directory}/fullchain.pem"
chmod 640 "${staging_directory}/privkey.pem"
chmod 751 "$staging_directory"
chown 101:101 \
    "$staging_directory" \
    "${staging_directory}/fullchain.pem" \
    "${staging_directory}/privkey.pem"
mv "$staging_directory" "$release_directory"
ln -s "releases/${release_name}" "$temporary_link"
mv -Tf "$temporary_link" /certs/current

current_release="$(readlink -f /certs/current)"
kept_old_releases=0
find "$releases_directory" -mindepth 1 -maxdepth 1 -type d -print \
    | sort -r \
    | while IFS= read -r candidate; do
        case "$(basename "$candidate")" in
            [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z-[0-9a-f]*) ;;
            *) continue ;;
        esac
        if [ "$(readlink -f "$candidate")" = "$current_release" ]; then
            continue
        fi
        kept_old_releases=$((kept_old_releases + 1))
        if [ "$kept_old_releases" -gt 2 ]; then
            rm -rf -- "$candidate"
        fi
    done

trap - EXIT HUP INT TERM
echo "Activated validated certificate release ${release_name}."
