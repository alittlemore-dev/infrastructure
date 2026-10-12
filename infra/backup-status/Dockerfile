FROM python:3.14-slim-trixie@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
WORKDIR /app

COPY infra/backup-status/requirements.txt /app/requirements.txt
COPY --chmod=755 infra/scripts/backup_status_image_setup.sh /usr/local/bin/backup-status-image-setup
RUN ["/usr/local/bin/backup-status-image-setup"]
COPY infra/backup/src/infra_backup/__init__.py infra/backup/src/infra_backup/contracts.py infra/backup/src/infra_backup/json_io.py /app/infra_backup/
COPY infra/backup/src/infra_backup/status /app/infra_backup/status/

USER 10004:10004
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=5s --retries=3 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/live', timeout=3)"]
CMD ["uvicorn", "infra_backup.status.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
