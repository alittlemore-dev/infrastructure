FROM alpine:3.24.4@sha256:f59abe656b692d901e184be3445e084786602108599d2a51d229c0f5a48c71bf

ARG TARGETARCH

COPY --chmod=755 infra/scripts/install_minio_release.sh /usr/local/bin/alittlemore-install-minio
RUN ["/usr/local/bin/alittlemore-install-minio", "mc"]

ENV MC_CONFIG_DIR=/tmp/mc
USER 10003:10003
ENTRYPOINT ["mc"]
