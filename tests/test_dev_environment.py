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
    checkout.mkdir(parents=True)
    (checkout / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    return checkout


def create_frontend_checkout(root: Path) -> Path:
    checkout = root / "frontend"
    checkout.mkdir()
    (checkout / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    return checkout


def create_auth_api_checkout(root: Path) -> Path:
    create_checkout(root, "i18n")
    checkout = root / "auth-api"
    checkout.mkdir()
    (checkout / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    return checkout


def run_state_preparer(
    state_dir: Path,
    personal_workspace: Path,
    competency_trainer: Path,
    auth_api: Path,
    frontend: Path,
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
            "--i18n-dir",
            str(auth_api.parent / "i18n"),
            "--auth-api-dir",
            str(auth_api),
            "--frontend-dir",
            str(frontend),
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
    def test_first_run_creates_private_stable_secrets_and_valid_pki(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            state_dir = root / "dev-state"
            personal_workspace = create_checkout(root, "personal-workspace")
            competency_trainer = create_checkout(root, "competency-trainer")
            auth_api = create_auth_api_checkout(root)
            frontend = create_frontend_checkout(root)
            first = run_state_preparer(
                state_dir, personal_workspace, competency_trainer, auth_api, frontend
            )
            self.assertEqual(0, first.returncode, first.stderr)
            telegram_secret_paths = (
                state_dir / "secrets/personal-workspace/telegram_bot_token",
                state_dir / "secrets/personal-workspace/telegram_webhook_secret",
            )
            self.assertTrue(all(path.is_file() for path in telegram_secret_paths))
            self.assertTrue(all(path.read_text(encoding="utf-8") == "" for path in telegram_secret_paths))

            private_stable_files = (
                state_dir / "secrets/platform/minio_root_secret_key",
                state_dir / "secrets/competency-trainer/agent_issuing_private_key",
                state_dir / "tls/local-development-ca.key.pem",
            )
            auth_api_compose_files = (
                state_dir / "secrets/auth-api/app_secret_key",
                state_dir / "secrets/auth-api/auth_private_key",
                state_dir / "secrets/auth-api/auth_public_key",
                state_dir / "secrets/auth-api/db_password",
                state_dir / "secrets/auth-api/minio_access_key",
                state_dir / "secrets/auth-api/minio_secret_key",
                state_dir / "secrets/auth-api/sentry_dsn",
            )
            stable_files = private_stable_files + auth_api_compose_files + telegram_secret_paths
            initial_contents = {path: path.read_bytes() for path in stable_files}
            legacy_owner_files = (
                state_dir / "credentials",
                state_dir / "owner-password",
                state_dir / "secrets/auth-api/owner_init_password",
            )
            for path in legacy_owner_files:
                path.write_text("obsolete-test-value", encoding="utf-8")

            second = run_state_preparer(
                state_dir, personal_workspace, competency_trainer, auth_api, frontend
            )
            self.assertEqual(0, second.returncode, second.stderr)
            self.assertEqual(initial_contents, {path: path.read_bytes() for path in stable_files})
            self.assertFalse(any(path.exists() for path in legacy_owner_files))
            self.assertTrue(
                (state_dir / "secrets/auth-api/minio_access_key")
                .read_text(encoding="utf-8")
                .startswith("local-auth-api-")
            )

            for directory in (
                state_dir,
                state_dir / "secrets",
                state_dir / "secrets/auth-api",
            ):
                self.assertEqual(0o700, stat.S_IMODE(directory.stat().st_mode), directory)
            for path in private_stable_files:
                self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode), path)
            for path in auth_api_compose_files:
                self.assertEqual(0o444, stat.S_IMODE(path.stat().st_mode), path)

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

            certificate_details = subprocess.run(
                [
                    "openssl",
                    "x509",
                    "-in",
                    str(state_dir / "tls/fullchain.pem"),
                    "-noout",
                    "-text",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, certificate_details.returncode, certificate_details.stderr)
            for hostname in (
                "alittlemore.localhost",
                "agent.alittlemore.localhost",
                "s3.localhost",
            ):
                self.assertIn(f"DNS:{hostname}", certificate_details.stdout)

            auth_private_key = state_dir / "secrets/auth-api/auth_private_key"
            auth_public_key = state_dir / "secrets/auth-api/auth_public_key"
            auth_key_details = subprocess.run(
                ["openssl", "pkey", "-in", str(auth_private_key), "-text", "-noout"],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, auth_key_details.returncode, auth_key_details.stderr)
            self.assertIn("ED25519", auth_key_details.stdout.upper())
            derived_auth_public_key = subprocess.run(
                ["openssl", "pkey", "-in", str(auth_private_key), "-pubout"],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                auth_public_key.read_text(encoding="utf-8"),
                derived_auth_public_key.stdout,
            )

    def test_state_preparer_rejects_mismatched_existing_auth_api_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            state_dir = root / "dev-state"
            personal_workspace = create_checkout(root, "personal-workspace")
            competency_trainer = create_checkout(root, "competency-trainer")
            auth_api = create_auth_api_checkout(root)
            frontend = create_frontend_checkout(root)
            first = run_state_preparer(
                state_dir, personal_workspace, competency_trainer, auth_api, frontend
            )
            self.assertEqual(0, first.returncode, first.stderr)

            replacement_private_key = root / "replacement_private_key"
            replacement_public_key = root / "replacement_public_key"
            openssl = os.environ.get("OPENSSL_BINARY", "openssl")
            subprocess.run(
                [openssl, "genpkey", "-algorithm", "ED25519", "-out", replacement_private_key],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                [
                    openssl,
                    "pkey",
                    "-in",
                    replacement_private_key,
                    "-pubout",
                    "-out",
                    replacement_public_key,
                ],
                check=True,
                capture_output=True,
            )
            replacement_public_key.replace(state_dir / "secrets/auth-api/auth_public_key")

            second = run_state_preparer(
                state_dir, personal_workspace, competency_trainer, auth_api, frontend
            )

        self.assertEqual(1, second.returncode)
        self.assertIn("Auth API auth key pair does not match", second.stderr)

    def test_state_preparer_rejects_existing_auth_api_keys_with_wrong_algorithm(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            state_dir = root / "dev-state"
            personal_workspace = create_checkout(root, "personal-workspace")
            competency_trainer = create_checkout(root, "competency-trainer")
            auth_api = create_auth_api_checkout(root)
            frontend = create_frontend_checkout(root)
            first = run_state_preparer(
                state_dir, personal_workspace, competency_trainer, auth_api, frontend
            )
            self.assertEqual(0, first.returncode, first.stderr)

            replacement_private_key = root / "replacement_private_key"
            replacement_public_key = root / "replacement_public_key"
            openssl = os.environ.get("OPENSSL_BINARY", "openssl")
            subprocess.run(
                [
                    openssl,
                    "genpkey",
                    "-algorithm",
                    "EC",
                    "-pkeyopt",
                    "ec_paramgen_curve:P-256",
                    "-out",
                    replacement_private_key,
                ],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                [
                    openssl,
                    "pkey",
                    "-in",
                    replacement_private_key,
                    "-pubout",
                    "-out",
                    replacement_public_key,
                ],
                check=True,
                capture_output=True,
            )
            replacement_private_key.replace(state_dir / "secrets/auth-api/auth_private_key")
            replacement_public_key.replace(state_dir / "secrets/auth-api/auth_public_key")

            second = run_state_preparer(
                state_dir, personal_workspace, competency_trainer, auth_api, frontend
            )

        self.assertEqual(1, second.returncode)
        self.assertIn("Auth API auth key pair must use Ed25519", second.stderr)

    def test_state_preparer_rejects_a_symlink_state_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            real_state = root / "real-state"
            real_state.mkdir()
            linked_state = root / "linked-state"
            linked_state.symlink_to(real_state, target_is_directory=True)
            personal_workspace = create_checkout(root, "personal-workspace")
            competency_trainer = create_checkout(root, "competency-trainer")
            auth_api = create_auth_api_checkout(root)
            frontend = create_frontend_checkout(root)

            result = run_state_preparer(
                linked_state, personal_workspace, competency_trainer, auth_api, frontend
            )

        self.assertEqual(1, result.returncode)
        self.assertIn("must not be a symlink", result.stderr)

    def test_state_preparer_rejects_missing_application_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            competency_trainer = create_checkout(root, "competency-trainer")
            auth_api = create_auth_api_checkout(root)
            frontend = create_frontend_checkout(root)
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
                    "--i18n-dir",
                    str(auth_api.parent / "i18n"),
                    "--auth-api-dir",
                    str(auth_api),
                    "--frontend-dir",
                    str(frontend),
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
            auth_api = create_auth_api_checkout(root)
            frontend = create_frontend_checkout(root)
            prepared = run_state_preparer(
                state_dir, personal_workspace, competency_trainer, auth_api, frontend
            )
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
            "personal-workspace-backend-blue": personal_workspace,
            "competency-backend-blue": competency_trainer,
            "i18n-backend-blue": auth_api.parent / "i18n",
            "auth-api-backend-blue": auth_api,
            "frontend-blue": frontend,
        }
        for service_name, context in expected_builds.items():
            service = services[service_name]
            self.assertEqual(str(context.resolve()), service["build"]["context"])
            self.assertEqual("never", service["pull_policy"])

        self.assertEqual(
            "alittlemore.localhost",
            services["personal-workspace-backend-blue"]["environment"]["APP_DOMAIN"],
        )
        self.assertIn("i18n-network", services["frontend-blue"]["networks"])
        self.assertEqual(
            "http://i18n-backend-blue:8080",
            services["frontend-blue"]["environment"]["SSR_I18N_ORIGIN"],
        )
        competency_backend = services["competency-backend-blue"]
        self.assertEqual("alittlemore.localhost", competency_backend["environment"]["APP_DOMAIN"])
        self.assertNotIn("AUTH_PUBLIC_KEY", competency_backend["environment"])
        auth_backend = services["auth-api-backend-blue"]
        self.assertEqual("alittlemore.localhost", auth_backend["environment"]["APP_DOMAIN"])
        self.assertEqual("minio", auth_backend["environment"]["MINIO_HOST"])
        self.assertEqual("auth-avatars", auth_backend["environment"]["MINIO_BUCKET"])
        self.assertIn("auth-api-network", services["minio"]["networks"])
        self.assertIn("auth-api-network", services["minio-bootstrap"]["networks"])
        self.assertEqual(
            "service_completed_successfully",
            auth_backend["depends_on"]["minio-bootstrap"]["condition"],
        )
        self.assertEqual(
            {
                "app_secret_key",
                "auth_private_key",
                "auth_public_key",
                "db_password",
                "minio_access_key",
                "minio_secret_key",
                "sentry_dsn",
            },
            {secret["target"] for secret in auth_backend["secrets"]},
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
            "personal-workspace-taskiq-worker-green",
            "personal-workspace-taskiq-scheduler-green",
            "competency-backend-green",
            "competency-taskiq-worker-green",
            "competency-taskiq-scheduler-green",
            "i18n-backend-green",
            "auth-api-backend-green",
            "auth-api-taskiq-worker-green",
            "auth-api-taskiq-scheduler-green",
            "frontend-green",
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
            auth_api = create_auth_api_checkout(root)
            frontend = create_frontend_checkout(root)
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
                    "I18N_DIR": str(auth_api.parent / "i18n"),
                    "AUTH_API_DIR": str(auth_api),
                    "FRONTEND_DIR": str(frontend),
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
            auth_api = create_auth_api_checkout(root)
            frontend = create_frontend_checkout(root)

            make_executable(
                binary_dir / "docker",
                "#!/bin/sh\n"
                "printf '%s\\n' \"$*\" >>\"$FAKE_DOCKER_LOG\"\n"
                "if [ \"$1 $2 $3\" = 'compose version --short' ]; then printf '2.24.0\\n'; fi\n"
                "exit 0\n",
            )
            make_executable(
                binary_dir / "curl",
                "#!/bin/sh\n"
                "printf '%s\\n' \"$*\" >>\"$FAKE_CURL_LOG\"\n"
                "case \" $* \" in *' --write-out '*) printf '404'; exit 0 ;; esac\n"
                "output=''\n"
                "while [ \"$#\" -gt 0 ]; do\n"
                "  case \"$1\" in\n"
                "    --output) output=\"$2\"; shift 2 ;;\n"
                "    *) shift ;;\n"
                "  esac\n"
                "done\n"
                "if [ -n \"$output\" ] && [ \"$output\" != /dev/null ]; then\n"
                "  printf '%s' 'i18n.bundle.shared.ru i18n.bundle.how-this-site-is-built.ru "
                "i18n.bundle.shared.en i18n.bundle.how-this-site-is-built.en' >\"$output\"\n"
                "fi\n"
                "exit 0\n",
            )
            make_executable(binary_dir / "security", "#!/bin/sh\nexit 0\n")

            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{binary_dir}:{environment['PATH']}",
                    "ALITTLEMORE_DEV_PLATFORM": "Darwin",
                    "ALITTLEMORE_DEV_STATE_DIR": str(state_dir),
                    "PERSONAL_WORKSPACE_DIR": str(personal_workspace),
                    "COMPETENCY_TRAINER_DIR": str(competency_trainer),
                    "I18N_DIR": str(auth_api.parent / "i18n"),
                    "AUTH_API_DIR": str(auth_api),
                    "FRONTEND_DIR": str(frontend),
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
            self.assertLess(
                docker_calls.index("auth-api-backend-init"),
                docker_calls.rindex("auth-api-backend-blue"),
            )
            self.assertIn("--project-name alittlemore-dev", docker_calls)
            self.assertIn("--pull never", docker_calls)
            self.assertIn("--pull missing --remove-orphans", docker_calls)
            self.assertIn("frontend-blue", docker_calls)
            self.assertIn("auth-api-postgres", docker_calls)
            self.assertIn("auth-api-valkey", docker_calls)

            curl_calls = curl_log.read_text(encoding="utf-8")
            for url in (
                "https://alittlemore.localhost/healthz",
                "https://alittlemore.localhost/ru/how-this-site-is-built",
                "https://alittlemore.localhost/en/how-this-site-is-built",
                "https://alittlemore.localhost/api/personal-workspace/healthcheck",
                "https://alittlemore.localhost/api/competency/healthcheck",
                "https://alittlemore.localhost/api/auth/healthcheck",
                "https://alittlemore.localhost/api/i18n/bundles/ru",
                "https://alittlemore.localhost/api/personal-workspace/i18n/bundles/ru",
                "https://s3.localhost/minio/health/live",
            ):
                self.assertIn(url, curl_calls)

            self.assertNotIn("Auth API: owner / ", result.stdout)


if __name__ == "__main__":
    unittest.main()
