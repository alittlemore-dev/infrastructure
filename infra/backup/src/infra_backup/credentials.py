"""Read bounded secret files without exposing their paths or values."""
from __future__ import annotations

import os
import stat
from pathlib import Path


def read_secret(path: Path) -> str:
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Backup credential file is invalid.")
        content = stream.read(65537)
    if not content.strip() or len(content) > 65536:
        raise ValueError("Backup credential file is invalid.")
    value = content.decode().rstrip("\r\n")
    if "\r" in value or "\n" in value or "\x00" in value:
        raise ValueError("Backup credential file is invalid.")
    return value
