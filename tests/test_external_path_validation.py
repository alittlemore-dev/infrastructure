#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "infra/scripts"))

from validate_external_path import resolve_external_directory  # noqa: E402


class ExternalPathValidationTest(unittest.TestCase):
    def test_accepts_an_external_absolute_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            external = Path(temporary_directory) / "pki"
            self.assertEqual(
                external.resolve(strict=False),
                resolve_external_directory(ROOT, external),
            )

    def test_rejects_relative_and_repository_local_paths(self) -> None:
        with self.assertRaises(ValueError):
            resolve_external_directory(ROOT, Path("relative/pki"))
        with self.assertRaises(ValueError):
            resolve_external_directory(ROOT, ROOT / "private-pki")
        with self.assertRaises(ValueError):
            resolve_external_directory(ROOT, Path("/"))

    def test_rejects_a_symlink_that_resolves_into_the_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            link = Path(temporary_directory) / "repository-link"
            link.symlink_to(ROOT, target_is_directory=True)
            with self.assertRaises(ValueError):
                resolve_external_directory(ROOT, link / "private-pki")


if __name__ == "__main__":
    unittest.main()
