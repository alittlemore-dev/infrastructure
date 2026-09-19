from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class AuthApiContractTest(unittest.TestCase):
    def validate_keys(self, algorithm: str, *, mismatch: bool = False) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            keys = root / "auth-api"
            keys.mkdir()
            private = keys / "auth_private_key"
            public = keys / "auth_public_key"
            command = ["openssl", "genpkey", "-algorithm", algorithm]
            if algorithm == "EC":
                command += ["-pkeyopt", "ec_paramgen_curve:P-256"]
            subprocess.run([*command, "-out", str(private)], check=True, capture_output=True)
            subprocess.run(
                ["openssl", "pkey", "-in", str(private), "-pubout", "-out", str(public)],
                check=True, capture_output=True,
            )
            if mismatch:
                subprocess.run([*command, "-out", str(private)], check=True, capture_output=True)
            return subprocess.run(
                ["bash", "-c", 'set -euo pipefail; source "$1"; validate_auth_api_pki "$2"',
                 "auth-test", str(ROOT / "infra/scripts/compose_secrets.sh"), str(root)],
                env=os.environ.copy(), text=True, capture_output=True,
            )

    def test_matching_ed25519_pair_is_accepted(self) -> None:
        result = self.validate_keys("ED25519")
        self.assertEqual(0, result.returncode, result.stderr)

    def test_mismatched_pair_is_rejected_without_disclosing_keys(self) -> None:
        result = self.validate_keys("ED25519", mismatch=True)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("does not match", result.stderr)
        self.assertNotIn("BEGIN", result.stdout + result.stderr)

    def test_matching_wrong_algorithm_is_rejected(self) -> None:
        result = self.validate_keys("EC")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("Ed25519", result.stderr)
