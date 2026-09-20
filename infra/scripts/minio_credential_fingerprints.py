#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import os
import sys


SECRET_FILES = (
    ("MINIO_ROOT_ACCESS_KEY", "COMPOSE_MINIO_ROOT_ACCESS_KEY_FILE"),
    ("MINIO_ROOT_SECRET_KEY", "COMPOSE_MINIO_ROOT_SECRET_KEY_FILE"),
    ("PERSONAL_WORKSPACE_MINIO_ACCESS_KEY", "COMPOSE_PERSONAL_WORKSPACE_MINIO_ACCESS_KEY_FILE"),
    ("PERSONAL_WORKSPACE_MINIO_SECRET_KEY", "COMPOSE_PERSONAL_WORKSPACE_MINIO_SECRET_KEY_FILE"),
    ("COMPETENCY_MINIO_ACCESS_KEY", "COMPOSE_COMPETENCY_MINIO_ACCESS_KEY_FILE"),
    ("COMPETENCY_MINIO_SECRET_KEY", "COMPOSE_COMPETENCY_MINIO_SECRET_KEY_FILE"),
    ("DATABASUS_MINIO_ACCESS_KEY", "COMPOSE_DATABASUS_MINIO_ACCESS_KEY_FILE"),
    ("DATABASUS_MINIO_SECRET_KEY", "COMPOSE_DATABASUS_MINIO_SECRET_KEY_FILE"),
    ("AUTH_API_MINIO_ACCESS_KEY", "COMPOSE_AUTH_API_MINIO_ACCESS_KEY_FILE"),
    ("AUTH_API_MINIO_SECRET_KEY", "COMPOSE_AUTH_API_MINIO_SECRET_KEY_FILE"),
)

LEGACY_SECRET_NAMES = {
    "MINIO_ROOT_SECRET_KEY",
    "PERSONAL_WORKSPACE_MINIO_SECRET_KEY",
    "COMPETENCY_MINIO_SECRET_KEY",
    "DATABASUS_MINIO_SECRET_KEY",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Hash MinIO identities without exposing their values."
    )
    parser.add_argument(
        "--legacy-secret-keys-only",
        action="store_true",
        help="render the four-line marker written before access keys were pinned",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    selected_files = SECRET_FILES
    if args.legacy_secret_keys_only:
        selected_files = tuple(
            item for item in SECRET_FILES if item[0] in LEGACY_SECRET_NAMES
        )
    for name, file_variable in selected_files:
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
