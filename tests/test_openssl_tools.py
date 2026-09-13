#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "infra/scripts"
sys.path.insert(0, str(SCRIPTS))

import openssl_tools  # noqa: E402


def write_openssl_probe(path: Path, *, supports_ed25519: bool) -> None:
    exit_code = 0 if supports_ed25519 else 1
    path.write_text(
        "#!/bin/sh\n"
        "if [ \"$1 $2 $3\" = 'genpkey -algorithm ED25519' ]; then\n"
        f"    exit {exit_code}\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


class OpenSSLToolsTest(unittest.TestCase):
    def test_compatible_path_candidate_avoids_homebrew_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            binary_dir = Path(temporary_directory)
            compatible = binary_dir / "openssl"
            write_openssl_probe(compatible, supports_ed25519=True)

            with mock.patch.object(
                openssl_tools,
                "homebrew_openssl_candidates",
                side_effect=AssertionError("Homebrew lookup was unnecessary"),
            ):
                resolved = openssl_tools.resolve_openssl(
                    environment={"PATH": str(binary_dir)},
                )

        self.assertEqual(compatible.resolve(), resolved)

    def test_incompatible_path_candidate_falls_back_to_homebrew_openssl(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            path_dir = root / "path"
            path_dir.mkdir()
            incompatible = path_dir / "openssl"
            compatible = root / "homebrew-openssl"
            write_openssl_probe(incompatible, supports_ed25519=False)
            write_openssl_probe(compatible, supports_ed25519=True)

            resolved = openssl_tools.resolve_openssl(
                environment={"PATH": str(path_dir)},
                homebrew_candidates=(compatible,),
            )

        self.assertEqual(compatible.resolve(), resolved)

    def test_incompatible_explicit_binary_is_rejected_without_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            incompatible = root / "incompatible-openssl"
            compatible = root / "compatible-openssl"
            write_openssl_probe(incompatible, supports_ed25519=False)
            write_openssl_probe(compatible, supports_ed25519=True)

            with self.assertRaisesRegex(
                openssl_tools.OpenSSLResolutionError,
                "OPENSSL_BINARY.*Ed25519",
            ):
                openssl_tools.resolve_openssl(
                    environment={"OPENSSL_BINARY": str(incompatible), "PATH": ""},
                    homebrew_candidates=(compatible,),
                )

    def test_missing_compatible_binary_has_actionable_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            with self.assertRaisesRegex(
                openssl_tools.OpenSSLResolutionError,
                "OpenSSL with Ed25519 support.*brew install openssl@3",
            ):
                openssl_tools.resolve_openssl(
                    environment={"PATH": temporary_directory},
                    homebrew_candidates=(),
                )


if __name__ == "__main__":
    unittest.main()
