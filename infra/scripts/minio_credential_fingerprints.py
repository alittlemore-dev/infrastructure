#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import os
import sys


SECRET_NAMES = (
    "MINIO_ROOT_SECRET_KEY",
    "PERSONAL_WORKSPACE_MINIO_SECRET_KEY",
    "COMPETENCY_MINIO_SECRET_KEY",
    "DATABASUS_MINIO_SECRET_KEY",
)


def main() -> int:
    for name in SECRET_NAMES:
        if name not in os.environ:
            print(f"{name} must be present to fingerprint MinIO credentials.", file=sys.stderr)
            return 1
        fingerprint = hashlib.sha256(os.environ[name].encode()).hexdigest()
        print(f"{name} {fingerprint}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
