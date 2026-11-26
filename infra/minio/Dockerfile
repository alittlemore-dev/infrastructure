FROM --platform=$BUILDPLATFORM golang:1.26.11-alpine3.24@sha256:ecb67f987aa1a57794c91d19ade7ae0209f2f0f5496aaef9e6ae85ddf92d02ab AS builder

ARG TARGETOS
ARG TARGETARCH
COPY --chmod=755 infra/scripts/build_minio_release.sh /usr/local/bin/alittlemore-build-minio
RUN ["/usr/local/bin/alittlemore-build-minio"]

FROM alpine:3.24.4@sha256:f59abe656b692d901e184be3445e084786602108599d2a51d229c0f5a48c71bf

COPY --from=builder /usr/local/bin/minio /usr/local/bin/minio
COPY --chmod=755 infra/scripts/minio_entrypoint.sh /usr/local/bin/alittlemore-minio-entrypoint
COPY --chmod=755 infra/scripts/minio_image_setup.sh /usr/local/bin/alittlemore-minio-image-setup

RUN ["/usr/local/bin/alittlemore-minio-image-setup"]

USER 10002:10002
ENTRYPOINT ["alittlemore-minio-entrypoint"]
CMD ["server", "/data", "--console-address", ":9001"]
