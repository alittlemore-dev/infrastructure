FROM python:3.14-slim-trixie@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170

ENV PYTHONDONTWRITEBYTECODE=1
ENV MYPYPATH=/source
WORKDIR /app
COPY infra/backup/requirements-check.txt /app/requirements.txt
COPY --chmod=755 infra/scripts/backup_status_image_setup.sh /usr/local/bin/backup-check-setup
RUN ["/usr/local/bin/backup-check-setup"]
USER 10004:10004
ENTRYPOINT ["mypy", "--strict", "--cache-dir", "/tmp/mypy", "/source/infra_backup", "/checks/backup_deployment_metadata.py", "/checks/backup_status_edge_test.py", "/checks/backup_status_edge_fixture.py", "/checks/backup_smoke_client.py", "/checks/backup_status_http_checks.py", "/checks/minio_credential_fingerprints.py"]
