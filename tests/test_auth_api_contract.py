from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_environment_contract import compose_service_blocks


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

    def test_auth_runtime_is_isolated_and_ready(self) -> None:
        compose = (ROOT / "docker-compose.yml").read_text()
        blocks = compose_service_blocks(compose)
        for slot in ("blue", "green"):
            backend = blocks[f"auth-api-backend-{slot}"]
            self.assertIn("*auth-api-healthcheck", backend)
            self.assertIn("auth-api-postgres", backend)
            self.assertIn("auth-api-valkey", backend)
            self.assertNotIn("minio", backend)
            self.assertNotIn("ports:", backend)
        self.assertIn("/api/auth/healthcheck/ready", compose)
        self.assertIn('--appendonly", "yes"', blocks["auth-api-valkey"])

    def test_edge_uses_canonical_auth_routes_without_cookie_rewriting(self) -> None:
        template = (ROOT / "infra/nginx/templates/site.conf.template").read_text()
        self.assertNotIn("/api/auth-api", template)
        for route in ("= /api/auth/login", "= /api/auth/refresh", "^~ /api/auth/"):
            block = template.split(f"location {route} {{", 1)[1].split("}", 1)[0]
            self.assertIn("proxy_pass http://auth_api_backend;", block)
            self.assertNotIn("proxy_cookie_path", block)
            self.assertIn("limit_req zone=auth_", block)
        self.assertIn("/api/auth/healthcheck", (ROOT / "infra/scripts/edge_checks.sh").read_text())
    def test_auth_trailing_slashes_cannot_bypass_endpoint_rate_limits(self) -> None:
        template = (ROOT / "infra/nginx/templates/site.conf.template").read_text()
        for endpoint in ("login", "refresh"):
            block = template.split(f"location = /api/auth/{endpoint}/ {{", 1)[1].split("}", 1)[0]
            self.assertIn(f"return 308 /api/auth/{endpoint}$is_args$args;", block)
            self.assertIn("absolute_redirect off;", block)
            self.assertNotIn("proxy_pass", block)
