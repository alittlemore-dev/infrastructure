FROM alpine:3.22.2

RUN apk add --no-cache openssl=3.5.8-r0

COPY --chmod=755 infra/scripts/cert_sync.sh /usr/local/bin/alittlemore-cert-sync

# The base image's root user is retained to read Certbot's private key and hand
# ownership to nginx. Compose isolates this one-shot container and grants only
# CHOWN and DAC_OVERRIDE.

ENTRYPOINT ["alittlemore-cert-sync"]
