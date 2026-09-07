#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "infra/scripts"))

from validate_private_file import validate_private_regular_file  # noqa: E402


class PrivateFileValidationTest(unittest.TestCase):
    def test_accepts_an_owner_only_regular_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / ".env"
            path.write_text("SECRET=value\n", encoding="utf-8")
            path.chmod(0o600)
            validate_private_regular_file(path, os.getuid())

    def test_rejects_group_or_world_access(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / ".env"
            path.write_text("SECRET=value\n", encoding="utf-8")
            path.chmod(0o640)
            with self.assertRaises(ValueError):
                validate_private_regular_file(path, os.getuid())

    def test_rejects_a_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "target"
            target.write_text("SECRET=value\n", encoding="utf-8")
            target.chmod(0o600)
            link = Path(temporary_directory) / ".env"
            link.symlink_to(target)
            with self.assertRaises(ValueError):
                validate_private_regular_file(link, os.getuid())


if __name__ == "__main__":
    unittest.main()
