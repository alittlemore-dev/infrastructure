#!/usr/bin/env python3
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path


def validate_private_regular_file(path: Path, expected_uid: int) -> None:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("must be a regular file, not a symlink or special file")
    if metadata.st_uid != expected_uid:
        raise ValueError("must be owned by the current user")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise ValueError("must not be accessible by group or other users")


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) == 2 else Path("")
    try:
        validate_private_regular_file(path, os.getuid())
    except (OSError, ValueError) as exc:
        print(f"{path or '<missing>'}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
