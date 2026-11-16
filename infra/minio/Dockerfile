FROM alpine:3.24.4@sha256:f59abe656b692d901e184be3445e084786602108599d2a51d229c0f5a48c71bf

ARG TARGETARCH

COPY --chmod=755 infra/scripts/install_minio_release.sh /usr/local/bin/alittlemore-install-minio
RUN ["/usr/local/bin/alittlemore-install-minio", "minio"]

COPY --chmod=755 infra/scripts/minio_entrypoint.sh /usr/local/bin/alittlemore-minio-entrypoint
COPY --chmod=755 infra/scripts/minio_image_setup.sh /usr/local/bin/alittlemore-minio-image-setup

RUN ["/usr/local/bin/alittlemore-minio-image-setup"]

USER 10002:10002
ENTRYPOINT ["alittlemore-minio-entrypoint"]
CMD ["server", "/data", "--console-address", ":9001"]
