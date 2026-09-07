FROM minio/minio:RELEASE.2025-09-07T16-13-09Z

COPY --chmod=755 infra/scripts/minio_entrypoint.sh /usr/local/bin/alittlemore-minio-entrypoint
COPY --chmod=755 infra/scripts/minio_image_setup.sh /usr/local/bin/alittlemore-minio-image-setup

RUN ["/usr/local/bin/alittlemore-minio-image-setup"]

USER 10002:10002
ENTRYPOINT ["alittlemore-minio-entrypoint"]
CMD ["server", "/data", "--console-address", ":9001"]
