#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "infra/scripts"
def create_checkout(root: Path, name: str) -> Path:
    checkout = root / name
    for component in ("backend", "frontend"):
        directory = checkout / component
        directory.mkdir(parents=True)
        (directory / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    return checkout


def run_state_preparer(
    state_dir: Path,
    personal_workspace: Path,
    competency_trainer: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "prepare_dev_state.py"),
            "--repo-dir",
            str(ROOT),
            "--state-dir",
            str(state_dir),
            "--personal-workspace-dir",
            str(personal_workspace),
            "--competency-trainer-dir",
            str(competency_trainer),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )


def make_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


class DevStateTest(unittest.TestCase):
    def test_first_run_creates_private_stable_credentials_and_valid_pki(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            state_dir = root / "dev-state"
            personal_workspace = create_checkout(root, "personal-workspace")
            competency_trainer = create_checkout(root, "competency-trainer")
            first = run_state_preparer(state_dir, personal_workspace, competency_trainer)
            self.assertEqual(0, first.returncode, first.stderr)

            stable_files = (
                state_dir / "credentials",
                state_dir / "secrets/platform/minio_root_secret_key",
                state_dir / "secrets/personal-workspace/app_secret_key",
                state_dir / "secrets/competency-trainer/app_secret_key",
                state_dir / "secrets/competency-trainer/auth_private_key",
                state_dir / "secrets/competency-trainer/agent_issuing_private_key",
                state_dir / "tls/local-development-ca.key.pem",
            )
            initial_contents = {path: path.read_bytes() for path in stable_files}

            second = run_state_preparer(state_dir, personal_workspace, competency_trainer)
            self.assertEqual(0, second.returncode, second.stderr)
            self.assertEqual(initial_contents, {path: path.read_bytes() for path in stable_files})

            self.assertEqual(0o700, stat.S_IMODE(state_dir.stat().st_mode))
            for path in stable_files:
                self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode), path)

            for certificate in (
                state_dir / "tls/fullchain.pem",
                state_dir / "secrets/competency-trainer/agent_issuing_certificate",
            ):
                verified = subprocess.run(
                    ["openssl", "x509", "-in", str(certificate), "-noout"],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(0, verified.returncode, verified.stderr)

            credentials = (state_dir / "credentials").read_text(encoding="utf-8")
            self.assertIn("Personal Workspace: owner / ", credentials)
            self.assertIn("Competency Trainer: owner / ", credentials)
            self.assertNotIn("production", credentials.lower())

    def test_state_preparer_rejects_a_symlink_state_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            real_state = root / "real-state"
            real_state.mkdir()
            linked_state = root / "linked-state"
            linked_state.symlink_to(real_state, target_is_directory=True)
            personal_workspace = create_checkout(root, "personal-workspace")
            competency_trainer = create_checkout(root, "competency-trainer")

            result = run_state_preparer(linked_state, personal_workspace, competency_trainer)

        self.assertEqual(1, result.returncode)
        self.assertIn("must not be a symlink", result.stderr)

    def test_state_preparer_rejects_missing_application_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            competency_trainer = create_checkout(root, "competency-trainer")
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "prepare_dev_state.py"),
                    "--repo-dir",
                    str(ROOT),
                    "--state-dir",
                    str(root / "state"),
                    "--personal-workspace-dir",
                    str(root / "missing-personal-workspace"),
                    "--competency-trainer-dir",
                    str(competency_trainer),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )

        self.assertEqual(1, result.returncode)
        self.assertIn("Personal Workspace checkout", result.stderr)


class DevComposeTest(unittest.TestCase):
    def test_resolved_local_compose_uses_checkout_builds_and_no_production_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            state_dir = root / "dev-state"
            personal_workspace = create_checkout(root, "personal-workspace")
            competency_trainer = create_checkout(root, "competency-trainer")
            prepared = run_state_preparer(state_dir, personal_workspace, competency_trainer)
            self.assertEqual(0, prepared.returncode, prepared.stderr)

            result = subprocess.run(
                [
                    "docker",
                    "compose",
                    "--project-name",
                    "alittlemore-dev",
                    "--env-file",
                    str(ROOT / "config/platform/development.env"),
                    "--env-file",
                    str(state_dir / "compose.env"),
                    "--file",
                    str(ROOT / "docker-compose.yml"),
                    "--file",
                    str(ROOT / "docker-compose.dev.yml"),
                    "config",
                    "--format",
                    "json",
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=20,
            )

        self.assertEqual(0, result.returncode, result.stderr)
        compose = json.loads(result.stdout)
        services = compose["services"]

        expected_builds = {
            "personal-workspace-backend-blue": personal_workspace / "backend",
            "personal-workspace-frontend-blue": personal_workspace / "frontend",
            "competency-backend-blue": competency_trainer / "backend",
            "competency-frontend-blue": competency_trainer / "frontend",
        }
        for service_name, context in expected_builds.items():
            service = services[service_name]
            self.assertEqual(str(context.resolve()), service["build"]["context"])
            self.assertEqual("never", service["pull_policy"])
            self.assertTrue(service["image"].startswith("alittlemore-dev/"))

        self.assertEqual(
            "personal-workspace.localhost",
            services["personal-workspace-backend-blue"]["environment"]["APP_DOMAIN"],
        )
        competency_backend = services["competency-backend-blue"]
        self.assertEqual("competency.localhost", competency_backend["environment"]["APP_DOMAIN"])
        self.assertTrue(
            competency_backend["environment"]["AUTH_PUBLIC_KEY"].startswith(
                "-----BEGIN PUBLIC KEY-----\n"
            )
        )
        self.assertEqual(
            str((state_dir / "tls").resolve()),
            next(
                volume["source"]
                for volume in services["nginx"]["volumes"]
                if volume["target"] == "/certs"
            ),
        )
        for inactive_service in (
            "personal-workspace-backend-green",
            "personal-workspace-frontend-green",
            "personal-workspace-taskiq-worker-green",
            "personal-workspace-taskiq-scheduler-green",
            "competency-backend-green",
            "competency-frontend-green",
            "competency-taskiq-worker-green",
            "competency-taskiq-scheduler-green",
            "certbot",
            "cert-sync",
        ):
            self.assertNotIn(inactive_service, services)

        resolved = result.stdout
        for forbidden in (
            "ghcr.io/alittlemore-dev",
            "production.env",
            ".deploy-state",
            "10.77.0.1",
            "SOPS_AGE_KEY_FILE",
        ):
            self.assertNotIn(forbidden, resolved)


class DevOrchestrationTest(unittest.TestCase):
    def test_dev_trust_installs_the_ca_once_and_then_reports_it_trusted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            state_dir = root / "state"
            binary_dir = root / "bin"
            binary_dir.mkdir()
            personal_workspace = create_checkout(root, "personal-workspace")
            competency_trainer = create_checkout(root, "competency-trainer")
            trust_marker = root / "trusted"
            security_log = root / "security.log"
            make_executable(
                binary_dir / "security",
                "#!/bin/sh\n"
                "printf '%s\\n' \"$*\" >>\"$FAKE_SECURITY_LOG\"\n"
                "if [ \"$1\" = verify-cert ]; then test -f \"$FAKE_TRUST_MARKER\"; exit $?; fi\n"
                "if [ \"$1\" = add-trusted-cert ]; then : >\"$FAKE_TRUST_MARKER\"; fi\n"
                "exit 0\n",
            )
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{binary_dir}:{environment['PATH']}",
                    "HOME": str(root),
                    "ALITTLEMORE_DEV_PLATFORM": "Darwin",
                    "ALITTLEMORE_DEV_STATE_DIR": str(state_dir),
                    "PERSONAL_WORKSPACE_DIR": str(personal_workspace),
                    "COMPETENCY_TRAINER_DIR": str(competency_trainer),
                    "FAKE_SECURITY_LOG": str(security_log),
                    "FAKE_TRUST_MARKER": str(trust_marker),
                }
            )

            first = subprocess.run(
                ["/bin/bash", str(SCRIPTS / "dev_tls.sh"), "trust"],
                cwd=ROOT,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=20,
            )
            second = subprocess.run(
                ["/bin/bash", str(SCRIPTS / "dev_tls.sh"), "trust"],
                cwd=ROOT,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=20,
            )

            self.assertEqual(0, first.returncode, first.stderr)
            self.assertEqual(0, second.returncode, second.stderr)
            invocations = security_log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(1, sum(line.startswith("add-trusted-cert ") for line in invocations))
            self.assertIn("already trusted", second.stdout)

    def test_dev_runs_build_init_start_and_edge_smokes_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            state_dir = root / "state"
            binary_dir = root / "bin"
            binary_dir.mkdir()
            docker_log = root / "docker.log"
            curl_log = root / "curl.log"
            personal_workspace = create_checkout(root, "personal-workspace")
            competency_trainer = create_checkout(root, "competency-trainer")

            make_executable(
                binary_dir / "docker",
                "#!/bin/sh\n"
                "printf '%s\\n' \"$*\" >>\"$FAKE_DOCKER_LOG\"\n"
                "if [ \"$1 $2 $3\" = 'compose version --short' ]; then printf '2.24.0\\n'; fi\n"
                "if [ \"$1\" = run ]; then printf '%s\\n' '$argon2id$v=19$m=65536,t=3,p=4$localhash'; fi\n"
                "exit 0\n",
            )
            make_executable(
                binary_dir / "curl",
                "#!/bin/sh\nprintf '%s\\n' \"$*\" >>\"$FAKE_CURL_LOG\"\nexit 0\n",
            )
            make_executable(binary_dir / "security", "#!/bin/sh\nexit 0\n")

            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{binary_dir}:{environment['PATH']}",
                    "ALITTLEMORE_DEV_STATE_DIR": str(state_dir),
                    "PERSONAL_WORKSPACE_DIR": str(personal_workspace),
                    "COMPETENCY_TRAINER_DIR": str(competency_trainer),
                    "FAKE_DOCKER_LOG": str(docker_log),
                    "FAKE_CURL_LOG": str(curl_log),
                }
            )
            result = subprocess.run(
                ["/bin/bash", str(SCRIPTS / "dev.sh")],
                cwd=ROOT,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            docker_calls = docker_log.read_text(encoding="utf-8")
            self.assertLess(docker_calls.index(" build "), docker_calls.index(" up "))
            self.assertLess(
                docker_calls.index("personal-workspace-backend-init"),
                docker_calls.rindex("personal-workspace-backend-blue"),
            )
            self.assertIn("--project-name alittlemore-dev", docker_calls)
            self.assertIn("--pull never", docker_calls)
            self.assertIn("--pull missing --remove-orphans", docker_calls)

            curl_calls = curl_log.read_text(encoding="utf-8")
            for url in (
                "https://personal-workspace.localhost/api/healthcheck",
                "https://personal-workspace.localhost/healthz",
                "https://competency.localhost/api/healthcheck",
                "https://competency.localhost/healthz",
                "https://s3.localhost/minio/health/live",
            ):
                self.assertIn(url, curl_calls)

            self.assertIn("Personal Workspace: owner / ", result.stdout)
            self.assertIn("Competency Trainer: owner / ", result.stdout)


if __name__ == "__main__":
    unittest.main()
