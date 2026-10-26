FROM alpine:3.24.3@sha256:be2656061b536c1883c4034790e21d0a09c5aaaf51968b89368a3c0371ea2c9a

ARG TARGETARCH

COPY --chmod=755 infra/scripts/install_minio_release.sh /usr/local/bin/alittlemore-install-minio
RUN ["/usr/local/bin/alittlemore-install-minio", "minio"]

COPY --chmod=755 infra/scripts/minio_entrypoint.sh /usr/local/bin/alittlemore-minio-entrypoint
COPY --chmod=755 infra/scripts/minio_image_setup.sh /usr/local/bin/alittlemore-minio-image-setup

RUN ["/usr/local/bin/alittlemore-minio-image-setup"]

USER 10002:10002
ENTRYPOINT ["alittlemore-minio-entrypoint"]
CMD ["server", "/data", "--console-address", ":9001"]
