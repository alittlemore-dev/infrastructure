FROM alpine:3.24.2@sha256:294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6

ARG TARGETARCH

COPY --chmod=755 infra/scripts/install_minio_release.sh /usr/local/bin/alittlemore-install-minio
RUN ["/usr/local/bin/alittlemore-install-minio", "minio"]

COPY --chmod=755 infra/scripts/minio_entrypoint.sh /usr/local/bin/alittlemore-minio-entrypoint
COPY --chmod=755 infra/scripts/minio_image_setup.sh /usr/local/bin/alittlemore-minio-image-setup

RUN ["/usr/local/bin/alittlemore-minio-image-setup"]

USER 10002:10002
ENTRYPOINT ["alittlemore-minio-entrypoint"]
CMD ["server", "/data", "--console-address", ":9001"]
