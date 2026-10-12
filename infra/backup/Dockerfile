FROM valkey/valkey:9.1.2-alpine AS valkey
FROM postgres:18.6-alpine

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PATH=/app/.venv/bin:${PATH}
WORKDIR /app
COPY --from=valkey /usr/local/bin/valkey-cli /usr/local/bin/valkey-check-rdb /usr/local/bin/
COPY infra/backup/requirements.txt /app/requirements.txt
COPY --chmod=755 infra/scripts/backup_image_setup.sh /usr/local/bin/backup-image-setup
RUN ["/usr/local/bin/backup-image-setup"]
COPY infra/backup/src/infra_backup /app/infra_backup/
USER 10004:10004
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 CMD ["python", "-m", "infra_backup.status.liveness", "--state-dir", "/state"]
ENTRYPOINT ["python", "-m", "infra_backup.worker"]
CMD ["--config", "/etc/backup/sources.json", "--destinations", "/etc/backup/destinations.json", "--state-dir", "/state", "--work-dir", "/work", "--projection-dir", "/projection"]
