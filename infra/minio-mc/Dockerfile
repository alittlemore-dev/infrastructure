FROM alpine:3.22.2@sha256:4b7ce07002c69e8f3d704a9c5d6fd3053be500b7f1c69fc0d80990c2ad8dd412

ARG TARGETARCH

COPY --chmod=755 infra/scripts/install_minio_release.sh /usr/local/bin/alittlemore-install-minio
RUN ["/usr/local/bin/alittlemore-install-minio", "mc"]

ENV MC_CONFIG_DIR=/tmp/mc
USER 10003:10003
ENTRYPOINT ["mc"]
