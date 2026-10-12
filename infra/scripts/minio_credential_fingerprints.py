#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import os
import re
from pathlib import Path
import sys


SECRET_FILES = (
    ("MINIO_ROOT_ACCESS_KEY", "COMPOSE_MINIO_ROOT_ACCESS_KEY_FILE"),
    ("MINIO_ROOT_SECRET_KEY", "COMPOSE_MINIO_ROOT_SECRET_KEY_FILE"),
    ("PERSONAL_WORKSPACE_MINIO_ACCESS_KEY", "COMPOSE_PERSONAL_WORKSPACE_MINIO_ACCESS_KEY_FILE"),
    ("PERSONAL_WORKSPACE_MINIO_SECRET_KEY", "COMPOSE_PERSONAL_WORKSPACE_MINIO_SECRET_KEY_FILE"),
    ("COMPETENCY_MINIO_ACCESS_KEY", "COMPOSE_COMPETENCY_MINIO_ACCESS_KEY_FILE"),
    ("COMPETENCY_MINIO_SECRET_KEY", "COMPOSE_COMPETENCY_MINIO_SECRET_KEY_FILE"),
    ("BACKUP_MINIO_ACCESS_KEY", "COMPOSE_BACKUP_MINIO_ACCESS_KEY_FILE"),
    ("BACKUP_MINIO_SECRET_KEY", "COMPOSE_BACKUP_MINIO_SECRET_KEY_FILE"),
    ("AUTH_API_MINIO_ACCESS_KEY", "COMPOSE_AUTH_API_MINIO_ACCESS_KEY_FILE"),
    ("AUTH_API_MINIO_SECRET_KEY", "COMPOSE_AUTH_API_MINIO_SECRET_KEY_FILE"),
)

def document_values(path: Path) -> tuple[str, ...]:
    lines = path.read_text().splitlines()
    if len(lines) != len(SECRET_FILES):
        raise ValueError("Incomplete credential fingerprints.")
    values: list[str] = []
    labels: set[str] = set()
    for line in lines:
        match = re.fullmatch(r"([A-Z][A-Z0-9_]{0,127}) ([0-9a-f]{64})", line)
        if match is None or match[1] in labels:
            raise ValueError("Invalid credential fingerprints.")
        labels.add(match[1])
        values.append(match[2])
    return tuple(values)


def main() -> int:
    parser = argparse.ArgumentParser(description="Hash and compare ordered MinIO credentials.")
    parser.add_argument("--compare", nargs=2, type=Path)
    args = parser.parse_args()
    if args.compare:
        try:
            return 0 if document_values(args.compare[0]) == document_values(args.compare[1]) else 1
        except (OSError, ValueError):
            return 1
    for name, file_variable in SECRET_FILES:
        secret_path = os.environ.get(file_variable)
        if secret_path is None:
            print(f"{file_variable} must point to a MinIO secret file.", file=sys.stderr)
            return 1
        try:
            with open(secret_path, "rb") as secret_file:
                secret_value = secret_file.read()
        except OSError as exc:
            print(f"Could not read {file_variable}: {exc}", file=sys.stderr)
            return 1
        fingerprint = hashlib.sha256(secret_value).hexdigest()
        print(f"{name} {fingerprint}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
