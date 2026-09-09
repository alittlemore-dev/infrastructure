#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "infra/deploy/runtime-env.manifest.json"
ENV_EXAMPLE = ROOT / ".env.example"
COMPOSE = ROOT / "docker-compose.yml"
COMMON = ROOT / "infra/scripts/common.sh"


def env_example_names() -> set[str]:
    return {
        line.split("=", 1)[0]
        for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    }


def manifest_names() -> tuple[set[str], set[str]]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    all_names = {
        entry["name"]
        for group in ("computed", "vars", "secrets")
        for entry in manifest[group]
    }
    allow_empty = {
        entry["name"]
        for group in ("computed", "vars", "secrets")
        for entry in manifest[group]
        if entry["allowEmpty"]
    }
    return all_names, allow_empty


def bash_array(name: str) -> set[str]:
    source = COMMON.read_text(encoding="utf-8")
    match = re.search(rf"readonly {name}=\(\n(?P<body>.*?)\n\)", source, re.DOTALL)
    if match is None:
        raise AssertionError(f"Could not find {name} in common.sh")
    return {line.strip() for line in match.group("body").splitlines() if line.strip()}


def compose_service_blocks(compose: str) -> dict[str, str]:
    services = compose.split("\nservices:\n", maxsplit=1)[1].split(
        "\nnetworks:\n", maxsplit=1
    )[0]
    starts = list(re.finditer(r"(?m)^  ([a-z0-9-]+):\n", services))
    return {
        match.group(1): services[
            match.start() : (
                starts[index + 1].start() if index + 1 < len(starts) else len(services)
            )
        ]
        for index, match in enumerate(starts)
    }


class EnvironmentContractTest(unittest.TestCase):
    def test_manifest_and_example_declare_the_same_runtime_values(self) -> None:
        names, _ = manifest_names()
        self.assertEqual(names, env_example_names())

    def test_shell_validation_matches_manifest_empty_value_policy(self) -> None:
        names, allow_empty = manifest_names()
        self.assertEqual(allow_empty, bash_array("ALLOW_EMPTY_ENVIRONMENT_VARIABLES"))
        self.assertEqual(names - allow_empty, bash_array("REQUIRED_ENVIRONMENT_VARIABLES"))

    def test_compose_variables_are_declared_or_generated_by_runtime(self) -> None:
        names, _ = manifest_names()
        compose_variables = set(
            re.findall(
                r"(?<!\$)\$\{([A-Z][A-Z0-9_]*)",
                COMPOSE.read_text(encoding="utf-8"),
            )
        )
        generated = {
            "PERSONAL_WORKSPACE_ACTIVE_BACKEND",
            "PERSONAL_WORKSPACE_ACTIVE_FRONTEND",
            "COMPETENCY_ACTIVE_BACKEND",
            "COMPETENCY_ACTIVE_FRONTEND",
            "NGINX_IMAGE",
            "COMPOSE_MINIO_ROOT_ACCESS_KEY_FILE",
            "COMPOSE_MINIO_ROOT_SECRET_KEY_FILE",
            "COMPOSE_DATABASUS_MINIO_ACCESS_KEY_FILE",
            "COMPOSE_DATABASUS_MINIO_SECRET_KEY_FILE",
            "COMPOSE_PERSONAL_WORKSPACE_APP_SECRET_KEY_FILE",
            "COMPOSE_PERSONAL_WORKSPACE_DB_PASSWORD_FILE",
            "COMPOSE_PERSONAL_WORKSPACE_MINIO_ACCESS_KEY_FILE",
            "COMPOSE_PERSONAL_WORKSPACE_MINIO_SECRET_KEY_FILE",
            "COMPOSE_PERSONAL_WORKSPACE_OWNER_PASSWORD_HASH_FILE",
            "COMPOSE_PERSONAL_WORKSPACE_SENTRY_DSN_FILE",
            "COMPOSE_COMPETENCY_APP_SECRET_KEY_FILE",
            "COMPOSE_COMPETENCY_AUTH_PRIVATE_KEY_FILE",
            "COMPOSE_COMPETENCY_DB_PASSWORD_FILE",
            "COMPOSE_COMPETENCY_MINIO_ACCESS_KEY_FILE",
            "COMPOSE_COMPETENCY_MINIO_SECRET_KEY_FILE",
            "COMPOSE_COMPETENCY_OWNER_INIT_PASSWORD_FILE",
            "COMPOSE_COMPETENCY_SENTRY_DSN_FILE",
            "COMPOSE_COMPETENCY_AGENT_ISSUING_CERTIFICATE_FILE",
            "COMPOSE_COMPETENCY_AGENT_ISSUING_PRIVATE_KEY_FILE",
            "COMPOSE_COMPETENCY_AGENT_CERTIFICATE_CHAIN_FILE",
        }
        self.assertEqual(set(), compose_variables - names - generated)

    def test_host_ports_preserve_public_and_vpn_boundaries(self) -> None:
        compose = COMPOSE.read_text(encoding="utf-8")
        port_lines = {
            line.strip().removeprefix("- ").strip('"')
            for line in compose.splitlines()
            if re.fullmatch(r'\s+- "[^" ]+"', line)
            and re.search(r"(?:80|443|1808[1-3])", line)
        }
        self.assertEqual(
            {
                "80:8080",
                "443:8443",
                "${VPN_BIND_ADDRESS}:18081:18081",
                "${VPN_BIND_ADDRESS}:18082:18082",
                "${VPN_BIND_ADDRESS}:18083:18083",
                "80:80",
            },
            port_lines,
        )

    def test_object_storage_and_backup_control_plane_are_shared(self) -> None:
        compose = COMPOSE.read_text(encoding="utf-8")
        service_blocks = compose_service_blocks(compose)
        service_names = list(service_blocks)

        self.assertEqual(1, service_names.count("minio"))
        self.assertEqual(1, service_names.count("minio-bootstrap"))
        self.assertEqual(1, service_names.count("databasus"))
        self.assertFalse(any(name.endswith("-minio") for name in service_names))
        self.assertFalse(any(name.endswith("-databasus") for name in service_names))

        for service_name in ("minio", "minio-bootstrap", "databasus"):
            block = service_blocks[service_name]
            self.assertIn("- personal-workspace-network", block)
            self.assertIn("- competency-network", block)

        self.assertEqual(2, compose.count("MINIO_HOST: minio"))

    def test_shared_minio_identities_have_scoped_bucket_policies(self) -> None:
        policies_dir = ROOT / "infra/minio/policies"

        def expected_policy(resources: list[str], *, list_buckets: bool = False) -> dict:
            statements = []
            if list_buckets:
                statements.append(
                    {
                        "Effect": "Allow",
                        "Action": ["s3:ListAllMyBuckets"],
                        "Resource": ["arn:aws:s3:::*"],
                    }
                )
            statements.append(
                {
                    "Effect": "Allow",
                    "Action": ["s3:*"],
                    "Resource": resources,
                }
            )
            return {"Version": "2012-10-17", "Statement": statements}

        def policy(name: str) -> dict:
            return json.loads((policies_dir / name).read_text(encoding="utf-8"))

        self.assertEqual(
            expected_policy(
                [
                    "arn:aws:s3:::media",
                    "arn:aws:s3:::media/*",
                    "arn:aws:s3:::knowledge-private",
                    "arn:aws:s3:::knowledge-private/*",
                ],
            ),
            policy("personal-workspace.json"),
        )
        self.assertEqual(
            expected_policy(["arn:aws:s3:::media", "arn:aws:s3:::media/*"]),
            policy("competency-trainer.json"),
        )
        self.assertEqual(
            expected_policy(
                [
                    "arn:aws:s3:::database-backups",
                    "arn:aws:s3:::database-backups/*",
                ],
                list_buckets=True,
            ),
            policy("databasus.json"),
        )

        bootstrap = (ROOT / "infra/scripts/minio_bootstrap.sh").read_text(encoding="utf-8")
        for bucket in ("media", "knowledge-private", "database-backups"):
            self.assertEqual(1, bootstrap.count(f"mc mb --ignore-existing alittlemore/{bucket}"))
        for identity, policy_name in (
            ("personal_workspace", "personal-workspace"),
            ("competency", "competency-trainer"),
            ("databasus", "databasus"),
        ):
            self.assertEqual(
                1,
                bootstrap.count(
                    f"mc admin policy create alittlemore {policy_name} "
                    f"/policies/{policy_name}.json"
                ),
            )
            self.assertEqual(
                1,
                bootstrap.count(
                    f'mc admin user add alittlemore "${identity}_access_key" '
                    f'"${identity}_secret_key"'
                ),
            )
            self.assertEqual(
                1,
                bootstrap.count(
                    f'mc admin policy attach alittlemore {policy_name} '
                    f'--user "${identity}_access_key"'
                ),
            )
        self.assertNotIn("mc admin user rm", bootstrap)
        self.assertNotIn("mc admin policy rm", bootstrap)

        common = COMMON.read_text(encoding="utf-8")
        self.assertIn("MinIO secret keys must all be different.", common)

    def test_minio_credentials_are_pinned_before_live_rollout_changes(self) -> None:
        run_script = (ROOT / "infra/scripts/run.sh").read_text(encoding="utf-8")
        main = run_script.split("\nacquire_runtime_lock\n", maxsplit=1)[1]

        self.assertLess(
            main.index('verify_minio_credential_fingerprints "$previous_slot"'),
            main.index("prepare_compose_secret_files"),
        )
        self.assertLess(
            main.index('compose_up_wait --build "${INFRASTRUCTURE_SERVICES[@]}"'),
            main.index("record_minio_credential_fingerprints"),
        )
        self.assertLess(
            main.index("record_minio_credential_fingerprints"),
            main.index("run_backend_initializers"),
        )
        self.assertIn("Refusing to mutate live MinIO credentials automatically.", run_script)
        self.assertIn("MinIO credential rotation is not supported during make run.", run_script)

    def test_compose_has_no_privileged_or_docker_socket_access(self) -> None:
        compose = COMPOSE.read_text(encoding="utf-8")
        self.assertNotIn("privileged: true", compose)
        self.assertNotIn("network_mode: host", compose)
        self.assertNotIn("/var/run/docker.sock", compose)

    def test_application_runtime_has_a_read_only_root_filesystem(self) -> None:
        compose = COMPOSE.read_text(encoding="utf-8")
        backend_anchor = compose.split("x-backend-runtime:", maxsplit=1)[1].split(
            "x-frontend-runtime:", maxsplit=1
        )[0]
        frontend_anchor = compose.split("x-frontend-runtime:", maxsplit=1)[1].split(
            "x-backend-healthcheck:", maxsplit=1
        )[0]
        self.assertIn("read_only: true", backend_anchor)
        self.assertIn("read_only: true", frontend_anchor)

    def test_application_images_use_registry_latest_and_infrastructure_is_pinned(self) -> None:
        compose = COMPOSE.read_text(encoding="utf-8")
        for image in (
            "personal-workspace-backend:latest",
            "personal-workspace-frontend:latest",
            "competency-trainer-backend:latest",
            "competency-trainer-frontend:latest",
        ):
            self.assertIn('${IMAGE_REGISTRY:?IMAGE_REGISTRY must be set}/' + image, compose)
        for image in (
            "postgres:18.4-alpine",
            "valkey/valkey:9.0.1",
            "minio/mc:RELEASE.2025-08-13T08-35-41Z",
            "databasus/databasus:v3.47.1",
            "certbot/certbot:v5.2.2",
        ):
            self.assertIn(image, compose)

if __name__ == "__main__":
    unittest.main()
