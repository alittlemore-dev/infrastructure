from __future__ import annotations

import os
import json
import subprocess
import tempfile
import unittest
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class AuthApiContractTest(unittest.TestCase):
    def test_maintenance_and_scan_render_without_starting_auth(self) -> None:
        docker = shutil.which("docker")
        self.assertIsNotNone(docker)
        manifest = json.loads((ROOT / "infra/deploy/runtime-secrets.manifest.json").read_text())
        for script_name, action in (("tls.sh", "sync"), ("trivy_scan.sh", "images")):
            for supplied in (None, "personal-workspace-backend-green"):
                with self.subTest(script=script_name, backend=supplied):
                    with tempfile.TemporaryDirectory() as temporary:
                        fixture = Path(temporary)
                        scripts = fixture / "infra/scripts"
                        scripts.mkdir(parents=True)
                        shutil.copy2(ROOT / "infra/scripts" / script_name, scripts / script_name)
                        shutil.copy2(
                            ROOT / "infra/scripts/list_compose_build_images.py",
                            scripts / "list_compose_build_images.py",
                        )
                        (scripts / "common.sh").write_text("""require_docker_compose() { :; }
require_command() { :; }
acquire_runtime_lock() { :; }
load_environment() { :; }
prepare_certificate_mount_directory() { :; }
""")
                        (scripts / "compose_secrets.sh").write_text(
                            "prepare_compose_secret_files() { :; }\n"
                        )
                        (scripts / "edge_checks.sh").write_text(
                            "verify_served_edge_certificates() { :; }\n"
                        )
                        binary = fixture / "bin/docker"
                        binary.parent.mkdir()
                        binary.write_text("""#!/usr/bin/env python3
import json, os, subprocess, sys
from pathlib import Path
args = sys.argv[1:]
with open(os.environ['TEST_DOCKER_LOG'], 'a') as stream:
    stream.write(json.dumps({'args': args, 'backend': os.environ.get('PERSONAL_WORKSPACE_ACTIVE_BACKEND')}) + '\\n')
subprocess.run([os.environ['TEST_REAL_DOCKER'], 'compose', '--env-file',
               os.environ['TEST_PLATFORM_ENV'], '-f', os.environ['TEST_COMPOSE'],
               'config', '--quiet'], check=True)
if args[:3] == ['compose', 'config', '--images']:
    print('registry.example.test/app:latest')
elif args[:4] == ['compose', 'config', '--format', 'json']:
    print(json.dumps({'services': {'app': {'image': 'registry.example.test/app:latest', 'build': {'context': '.'}}}}))
""")
                        binary.chmod(0o755)
                        log = fixture / "docker.log"
                        environment = dict(
                            os.environ,
                            PATH=f"{binary.parent}:{os.environ['PATH']}",
                            TEST_DOCKER_LOG=str(log), TEST_REAL_DOCKER=str(docker),
                            TEST_COMPOSE=str(ROOT / "docker-compose.yml"),
                            TEST_PLATFORM_ENV=str(ROOT / "config/platform/development.env"),
                        )
                        environment.pop("PERSONAL_WORKSPACE_ACTIVE_BACKEND", None)
                        if supplied is not None:
                            environment["PERSONAL_WORKSPACE_ACTIVE_BACKEND"] = supplied
                        for document in manifest["documents"]:
                            for secret in document["secrets"]:
                                environment[secret["composeVariable"]] = "/dev/null"
                        result = subprocess.run(
                            ["bash", str(scripts / script_name), action],
                            env=environment, capture_output=True, text=True,
                            check=False, timeout=30,
                        )
                        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                        calls = [json.loads(line) for line in log.read_text().splitlines()]
                        self.assertTrue(calls)
                        for call in calls:
                            self.assertEqual(
                                supplied or "personal-workspace-backend-blue", call["backend"],
                            )
                            self.assertNotIn("up", call["args"])
                            self.assertFalse(any(arg.startswith("auth-api-backend-") for arg in call["args"]))

    def test_auth_readiness_url_follows_the_active_personal_backend(self) -> None:
        manifest = json.loads((ROOT / "infra/deploy/runtime-secrets.manifest.json").read_text())
        for slot in ("blue", "green"):
            environment = dict(
                os.environ,
                IMAGE_REGISTRY="registry.example.test/app",
                PERSONAL_WORKSPACE_ACTIVE_BACKEND=f"personal-workspace-backend-{slot}",
            )
            for document in manifest["documents"]:
                for secret in document["secrets"]:
                    environment[secret["composeVariable"]] = "/dev/null"
            result = subprocess.run(
                ["docker", "compose", "--env-file", "config/platform/development.env",
                 "-f", "docker-compose.yml", "config", "--format", "json"],
                cwd=ROOT, env=environment, capture_output=True, text=True, check=True,
            )
            services = json.loads(result.stdout)["services"]
            personal = services[f"personal-workspace-backend-{slot}"]
            for auth_slot in ("blue", "green"):
                auth = services[f"auth-api-backend-{auth_slot}"]
                self.assertEqual(
                    f"http://personal-workspace-backend-{slot}:8080/api/internal/telegram/status",
                    auth["environment"]["TELEGRAM_PERSONAL_WORKSPACE_STATUS_URL"],
                )
                self.assertTrue(set(auth["networks"]) & set(personal["networks"]))

    def test_avatar_policy_is_private_and_least_privilege(self) -> None:
        policy = json.loads(
            (ROOT / "infra/minio/policies/auth-api.json").read_text(encoding="utf-8")
        )
        statements = policy["Statement"]

        self.assertEqual(2, len(statements))
        self.assertEqual(
            {"s3:GetBucketLocation", "s3:ListBucket"},
            set(statements[0]["Action"]),
        )
        self.assertEqual("arn:aws:s3:::auth-avatars", statements[0]["Resource"])
        self.assertEqual(
            {"s3:GetObject", "s3:PutObject", "s3:DeleteObject"},
            set(statements[1]["Action"]),
        )
        self.assertEqual("arn:aws:s3:::auth-avatars/*", statements[1]["Resource"])
        self.assertNotIn("Principal", repr(policy))
        self.assertNotIn("s3:*", repr(policy))

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
