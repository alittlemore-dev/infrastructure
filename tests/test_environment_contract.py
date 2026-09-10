#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PUBLIC_MANIFEST = ROOT / "infra/deploy/runtime-config.manifest.json"
SECRET_MANIFEST = ROOT / "infra/deploy/runtime-secrets.manifest.json"
COMPOSE = ROOT / "docker-compose.yml"
STOP_COMPOSE = ROOT / "infra/compose/stop.yml"


def env_names(path: Path) -> set[str]:
    return {
        line.split("=", 1)[0]
        for line in path.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    }


def public_manifest() -> dict:
    return json.loads(PUBLIC_MANIFEST.read_text(encoding="utf-8"))


def secret_manifest() -> dict:
    return json.loads(SECRET_MANIFEST.read_text(encoding="utf-8"))


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
    def test_locally_built_image_names_do_not_duplicate_upstream_versions(self) -> None:
        compose = COMPOSE.read_text(encoding="utf-8")
        run_script = (ROOT / "infra/scripts/run.sh").read_text(encoding="utf-8")

        self.assertIn("image: alittlemore-infra/minio:local", compose)
        self.assertIn("image: alittlemore-infra/cert-sync:local", compose)
        self.assertIn("${NGINX_IMAGE:-alittlemore-infra/nginx:local}", compose)
        self.assertIn('${NGINX_IMAGE_REPOSITORY}:${target_slot}', run_script)
        self.assertIn('${NGINX_IMAGE_REPOSITORY}:${previous_slot}', run_script)
        self.assertNotRegex(
            compose,
            r"alittlemore-infra/(?:minio|nginx|cert-sync):[^\n]*[0-9]\.[0-9]",
        )

    def test_public_config_is_service_scoped_and_uses_native_application_names(self) -> None:
        manifest = public_manifest()
        configs = {entry["name"]: entry for entry in manifest["configs"]}

        self.assertEqual(
            {"platform", "personal-workspace", "competency-trainer"}, set(configs)
        )
        for config in configs.values():
            self.assertEqual(
                set(config["variables"]),
                env_names(ROOT / config["path"]),
            )
        for application in ("personal-workspace", "competency-trainer"):
            names = set(configs[application]["variables"])
            self.assertIn("APP_DEBUG", names)
            self.assertIn("APP_DOMAIN", names)
            self.assertIn("DB_NAME", names)
            for native_name in ("APP_DEBUG", "APP_DOMAIN", "APP_USE_CACHE", "DB_NAME", "DB_USER"):
                self.assertNotIn(f"PERSONAL_WORKSPACE_{native_name}", names)
                self.assertNotIn(f"COMPETENCY_{native_name}", names)

    def test_secret_contract_is_service_scoped_and_reuses_native_names(self) -> None:
        documents = {entry["name"]: entry for entry in secret_manifest()["documents"]}

        self.assertEqual(
            {"platform", "personal-workspace", "competency-trainer"}, set(documents)
        )
        personal_names = {entry["name"] for entry in documents["personal-workspace"]["secrets"]}
        competency_names = {
            entry["name"] for entry in documents["competency-trainer"]["secrets"]
        }
        for native_name in ("APP_SECRET_KEY", "DB_PASSWORD", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY", "SENTRY_DSN"):
            self.assertIn(native_name, personal_names)
            self.assertIn(native_name, competency_names)
        self.assertFalse(
            any(
                entry["name"].startswith(("PERSONAL_WORKSPACE_", "COMPETENCY_"))
                for document in documents.values()
                for entry in document["secrets"]
            )
        )
        competency_specs = {
            entry["name"]: entry for entry in documents["competency-trainer"]["secrets"]
        }
        self.assertEqual(
            {
                "AUTH_PRIVATE_KEY",
                "AGENT_ACCESS_ISSUING_CERTIFICATE",
                "AGENT_ACCESS_ISSUING_PRIVATE_KEY",
                "AGENT_ACCESS_CERTIFICATE_CHAIN",
            },
            {
                name
                for name, spec in competency_specs.items()
                if spec.get("encoding") == "pem"
            },
        )

    def test_compose_variables_are_declared_or_generated_by_runtime(self) -> None:
        runtime_names = {
            target
            for config in public_manifest()["configs"]
            for target in config["runtimeAliases"].values()
        }
        secret_path_names = {
            secret["composeVariable"]
            for document in secret_manifest()["documents"]
            for secret in document["secrets"]
        }
        compose_variables = set(
            re.findall(
                r"(?<!\$)\$\{([A-Z][A-Z0-9_]*)",
                COMPOSE.read_text(encoding="utf-8"),
            )
        )
        generated = {
            "PERSONAL_WORKSPACE_ACTIVE_BACKEND",
            "PERSONAL_WORKSPACE_ENV_FILE",
            "COMPETENCY_ACTIVE_BACKEND",
            "COMPETENCY_ENV_FILE",
            "NGINX_IMAGE",
        }
        self.assertEqual(set(), compose_variables - runtime_names - secret_path_names - generated)

    def test_backend_services_receive_native_config_from_service_env_files(self) -> None:
        compose = COMPOSE.read_text(encoding="utf-8")
        personal_anchor = compose.split("x-personal-workspace-backend:", maxsplit=1)[1].split(
            "x-competency-backend:", maxsplit=1
        )[0]
        competency_anchor = compose.split("x-competency-backend:", maxsplit=1)[1].split(
            "x-competency-agent-secrets:", maxsplit=1
        )[0]

        self.assertIn(
            "env_file:\n    - ${PERSONAL_WORKSPACE_ENV_FILE:-./config/personal-workspace/production.env}",
            personal_anchor,
        )
        self.assertIn(
            "env_file:\n    - ${COMPETENCY_ENV_FILE:-./config/competency-trainer/production.env}",
            competency_anchor,
        )
        self.assertNotIn("${PERSONAL_WORKSPACE_APP_DEBUG}", personal_anchor)
        self.assertNotIn("${COMPETENCY_APP_DEBUG}", competency_anchor)
        self.assertIn("APP_DOMAIN: ${APP_DOMAIN}", personal_anchor)
        self.assertIn("APP_DOMAIN: ${APP_DOMAIN}", competency_anchor)

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

        compose_secrets = (ROOT / "infra/scripts/compose_secrets.sh").read_text(encoding="utf-8")
        self.assertIn("MinIO secret keys must all be different.", compose_secrets)

    def test_minio_credentials_are_pinned_before_live_rollout_changes(self) -> None:
        run_script = (ROOT / "infra/scripts/run.sh").read_text(encoding="utf-8")
        compose_secrets = (ROOT / "infra/scripts/compose_secrets.sh").read_text(
            encoding="utf-8"
        )
        main = run_script.split("\nacquire_runtime_lock\n", maxsplit=1)[1]
        prepare = compose_secrets.split("prepare_compose_secret_files()", maxsplit=1)[1]

        self.assertLess(
            prepare.index("validate_minio_credentials"),
            prepare.index("verify_minio_credential_fingerprints"),
        )
        self.assertLess(
            prepare.index("verify_minio_credential_fingerprints"),
            prepare.index("switch_compose_secret_slot"),
        )
        self.assertLess(
            prepare.index("switch_compose_secret_slot"),
            prepare.index('mv -f "$candidate_fingerprints" "$fingerprint_marker"'),
        )
        self.assertLess(
            main.index("readonly target_slot"),
            main.index('prepare_compose_secret_files "$target_slot"'),
        )
        self.assertLess(
            main.index('prepare_compose_secret_files "$target_slot"'),
            main.index("pull_application_images"),
        )
        self.assertLess(
            main.index('compose_up_wait --build "${INFRASTRUCTURE_SERVICES[@]}"'),
            main.index("record_minio_credential_fingerprints"),
        )
        self.assertLess(
            main.index("record_minio_credential_fingerprints"),
            main.index("run_backend_initializers"),
        )
        self.assertIn(
            "Refusing to mutate live MinIO credentials automatically.", compose_secrets
        )
        self.assertIn(
            "MinIO credential rotation is not supported during make run.", compose_secrets
        )

    def test_emergency_stop_does_not_depend_on_runtime_configuration(self) -> None:
        full_services = set(
            compose_service_blocks(COMPOSE.read_text(encoding="utf-8"))
        )
        stop_services = set(
            compose_service_blocks(STOP_COMPOSE.read_text(encoding="utf-8"))
        )
        stop_script = (ROOT / "infra/scripts/stop.sh").read_text(encoding="utf-8")

        self.assertEqual(full_services, stop_services)
        self.assertIn("pin_compose_identity", stop_script)
        self.assertIn("--project-name alittlemore-infra", stop_script)
        self.assertIn('down --remove-orphans', stop_script)
        self.assertNotIn("compose_secrets.sh", stop_script)
        self.assertNotIn("stop-placeholder", stop_script)
        self.assertNotIn("COMPOSE_SECRET_FILE_VARIABLES", stop_script)

    def test_compose_has_no_privileged_or_docker_socket_access(self) -> None:
        compose = COMPOSE.read_text(encoding="utf-8")
        self.assertNotIn("privileged: true", compose)
        self.assertNotIn("network_mode: host", compose)
        self.assertNotIn("/var/run/docker.sock", compose)

    def test_runtime_config_and_encrypted_secrets_are_not_sent_to_image_builds(self) -> None:
        ignored = set((ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines())

        self.assertIn("config", ignored)
        self.assertIn("secrets", ignored)
        self.assertIn(".sops.yaml", ignored)

    def test_ci_executes_the_real_sops_round_trip(self) -> None:
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

        self.assertEqual(1, workflow.count("run: make quality"))
        for duplicated_command in (
            "bash infra/scripts/install_sops.sh",
            "bash infra/scripts/install_age_keygen.sh",
            "run: make tests",
            "run: make check",
            "run: make lint-dockerfiles",
            "run: make security-trivy-config",
        ):
            self.assertNotIn(duplicated_command, workflow)

    def test_ci_does_not_require_callers_to_configure_tool_paths(self) -> None:
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

        self.assertNotIn("${{ runner.temp }}", workflow)
        self.assertNotIn("configure_ci_environment", workflow)
        self.assertNotIn("SOPS_INTEGRATION_BINARY", workflow)
        self.assertNotIn("AGE_KEYGEN_INTEGRATION_BINARY", workflow)
        self.assertEqual(workflow.count("make quality"), 1)

    def test_backend_runtime_has_a_read_only_root_filesystem(self) -> None:
        compose = COMPOSE.read_text(encoding="utf-8")
        backend_anchor = compose.split("x-backend-runtime:", maxsplit=1)[1].split(
            "x-backend-healthcheck:", maxsplit=1
        )[0]
        self.assertIn("read_only: true", backend_anchor)
        self.assertNotIn("x-frontend-runtime:", compose)
        self.assertNotRegex(compose, r"(?m)^  (?:personal-workspace|competency)-frontend-")

    def test_application_images_use_registry_latest_and_infrastructure_is_pinned(self) -> None:
        compose = COMPOSE.read_text(encoding="utf-8")
        for image in (
            "personal-workspace-backend:latest",
            "competency-trainer-backend:latest",
        ):
            self.assertIn('${IMAGE_REGISTRY:?IMAGE_REGISTRY must be set}/' + image, compose)
        self.assertNotIn("-frontend:latest", compose)
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
