#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ROOT / "docker-compose.yml"
NGINX_TEMPLATE = ROOT / "infra/nginx/templates/site.conf.template"
DEPLOY_WORKFLOW = ROOT / ".github/workflows/deploy.yml"
SOPS_BOOTSTRAP_SCRIPT = ROOT / "infra/scripts/bootstrap_sops_secrets.sh"
SSH_CONFIGURE_SCRIPT = ROOT / "infra/scripts/deploy_configure_ssh.sh"

AGENT_LOCATIONS = {
    "location = /internal/agent/v1/matrix/question-claims {": "POST",
    "location = /internal/agent/v1/matrix/authoring-context {": "GET",
    "location = /internal/agent/v1/matrix/resources {": "GET",
    'location ~ "^/internal/agent/v1/matrix/question-claims/[0-9a-f]{32}/draft$" {': "PUT",
    'location ~ "^/internal/agent/v1/matrix/question-claims/[0-9a-f]{32}$" {': "DELETE",
    "location = /internal/agent/v1/certificate-rotations {": "POST",
    'location ~ "^/internal/agent/v1/certificate-rotations/[0-9a-f]{32}/confirm$" {': "POST",
}


def server_blocks(config: str) -> list[str]:
    blocks: list[str] = []
    for match in re.finditer(r"(?m)^server\s*\{", config):
        depth = 0
        for index in range(match.start(), len(config)):
            if config[index] == "{":
                depth += 1
            elif config[index] == "}":
                depth -= 1
                if depth == 0:
                    blocks.append(config[match.start() : index + 1])
                    break
    return blocks


def compose_service_blocks(compose: str) -> dict[str, str]:
    services = compose.split("\nservices:\n", maxsplit=1)[1].split("\nnetworks:\n", maxsplit=1)[0]
    starts = list(re.finditer(r"(?m)^  ([a-z0-9-]+):\n", services))
    return {
        match.group(1): services[
            match.start() : starts[index + 1].start() if index + 1 < len(starts) else len(services)
        ]
        for index, match in enumerate(starts)
    }


def published_port_entries(service_block: str) -> list[str]:
    match = re.search(r"(?m)^    ports:\n(?P<entries>(?:      - .+\n)+)", service_block)
    if match is None:
        return []
    return [line.strip().removeprefix("- ") for line in match.group("entries").splitlines()]


class EdgeSecurityContractTest(unittest.TestCase):
    def test_only_edge_and_profiled_certbot_publish_ports(self) -> None:
        blocks = compose_service_blocks(COMPOSE.read_text(encoding="utf-8"))
        publishers = {name for name, block in blocks.items() if "\n    ports:\n" in block}
        self.assertEqual({"nginx", "certbot"}, publishers)
        self.assertIn("profiles:\n      - letsencrypt", blocks["certbot"])
        self.assertEqual(
            [
                '"80:8080"',
                '"443:8443"',
                '"${VPN_BIND_ADDRESS}:18081:18081"',
                '"${VPN_BIND_ADDRESS}:18082:18082"',
                '"${VPN_BIND_ADDRESS}:18083:18083"',
            ],
            published_port_entries(blocks["nginx"]),
        )
        self.assertEqual(['"80:80"'], published_port_entries(blocks["certbot"]))

    def test_background_processes_are_slotted_and_schedulers_do_not_overlap(self) -> None:
        blocks = compose_service_blocks(COMPOSE.read_text(encoding="utf-8"))
        for application in ("personal-workspace", "competency"):
            self.assertNotIn(f"{application}-taskiq-worker", blocks)
            self.assertNotIn(f"{application}-taskiq-scheduler", blocks)
            for slot in ("blue", "green"):
                self.assertIn(f"{application}-taskiq-worker-{slot}", blocks)
                self.assertIn(f"{application}-taskiq-scheduler-{slot}", blocks)

        run_script = (ROOT / "infra/scripts/run.sh").read_text(encoding="utf-8")
        transition = run_script.split("start_target_background_processes()", maxsplit=1)[1].split(
            "restore_previous_background_processes()", maxsplit=1
        )[0]
        self.assertLess(transition.index("docker compose stop"), transition.index("compose_up_wait"))
        self.assertIn('taskiq-scheduler-${previous_slot}', transition)
        self.assertIn('taskiq-scheduler-${target_slot}', transition)

    def test_slot_commit_is_atomic_and_first_deploy_failure_closes_the_edge(self) -> None:
        run_script = (ROOT / "infra/scripts/run.sh").read_text(encoding="utf-8")
        save_slot = run_script.split("save_active_slot()", maxsplit=1)[1].split(
            "stop_previous_slot()", maxsplit=1
        )[0]
        self.assertIn('printf \'%s %s\\n\' "$1" "$release_id"', save_slot)
        self.assertIn('mv -f "$temporary_slot_file" "$ACTIVE_SLOT_FILE"', save_slot)
        self.assertIn("validate_private_file.py", run_script)
        self.assertIn("Active-slot destination is not a regular file", save_slot)
        self.assertIn('if ! save_active_slot "$target_slot"; then', run_script)
        self.assertIn("runtime_commit_is_recorded()", run_script)

        restore_edge = run_script.split("restore_previous_edge()", maxsplit=1)[1].split(
            "handle_edge_interruption()", maxsplit=1
        )[0]
        self.assertIn("docker compose stop nginx", restore_edge)
        self.assertIn('personal-workspace-backend-${target_slot}', restore_edge)
        interruption = run_script.split("handle_edge_interruption()", maxsplit=1)[1].split(
            "require_docker_compose", maxsplit=1
        )[0]
        self.assertIn("if runtime_commit_is_recorded; then", interruption)
        self.assertLess(
            run_script.index('if ! save_active_slot "$target_slot"; then'),
            run_script.index("trap - HUP INT TERM\nstop_previous_slot"),
        )

    def test_certificate_mount_root_has_explicit_traversal_permissions(self) -> None:
        common = (ROOT / "infra/scripts/common.sh").read_text(encoding="utf-8")
        run_script = (ROOT / "infra/scripts/run.sh").read_text(encoding="utf-8")
        tls_script = (ROOT / "infra/scripts/tls.sh").read_text(encoding="utf-8")
        self.assertIn('chmod 751 "$certificate_directory"', common)
        self.assertIn("Deployment runtime-root marker must be owner-only", common)
        self.assertIn("prepare_certificate_mount_directory", run_script)
        self.assertIn("prepare_certificate_mount_directory", tls_script)

    def test_runtime_requires_compose_with_rollout_flags(self) -> None:
        common = (ROOT / "infra/scripts/common.sh").read_text(encoding="utf-8")
        run_script = (ROOT / "infra/scripts/run.sh").read_text(encoding="utf-8")
        tls_script = (ROOT / "infra/scripts/tls.sh").read_text(encoding="utf-8")
        self.assertIn("Docker Compose v2.24.0 or newer is required", common)
        self.assertIn("require_docker_compose", run_script)
        self.assertIn("require_docker_compose", tls_script)

    def test_certificate_releases_are_unique_and_bounded(self) -> None:
        sync = (ROOT / "infra/scripts/cert_sync.sh").read_text(encoding="utf-8")
        self.assertIn("openssl rand -hex 12", sync)
        self.assertIn("umask 077", sync)
        self.assertIn('chmod 751 "$releases_directory"', sync)
        self.assertIn('kept_old_releases" -gt 2', sync)
        self.assertIn('readlink -f /certs/current', sync)

    def test_private_agent_listener_has_exact_allowlist_and_mtls(self) -> None:
        config = NGINX_TEMPLATE.read_text(encoding="utf-8")
        private = [block for block in server_blocks(config) if "listen 18083 ssl;" in block]
        self.assertEqual(1, len(private))
        private_listener = private[0]
        for directive in (
            "ssl_client_certificate /run/secrets/agent_client_ca_certificate;",
            "ssl_verify_client on;",
            "ssl_verify_depth 2;",
            "limit_req zone=competency_agent_api_per_certificate",
            "proxy_set_header X-Agent-Client-Certificate $ssl_client_escaped_cert;",
        ):
            self.assertIn(directive, private_listener)
        for location, method in AGENT_LOCATIONS.items():
            self.assertEqual(1, private_listener.count(location))
            self.assertIn(f"if ($request_method != {method}) {{ return 405; }}", private_listener)
        self.assertEqual(7, private_listener.count("proxy_pass http://competency_backend;"))
        self.assertEqual(7, private_listener.count("limit_req zone=competency_agent_api_per_certificate"))
        self.assertIn("location / {\n        return 404;", private_listener)

    def test_public_agent_contour_is_closed_and_header_is_stripped(self) -> None:
        config = NGINX_TEMPLATE.read_text(encoding="utf-8")
        blocks = server_blocks(config)
        public_agent = [
            block
            for block in blocks
            if "listen 8443 ssl;" in block and "server_name agent.${COMPETENCY_DOMAIN};" in block
        ]
        self.assertEqual(1, len(public_agent))
        self.assertIn("return 404;", public_agent[0])
        self.assertNotIn("proxy_pass", public_agent[0])

        public_competency = [
            block
            for block in blocks
            if "listen 8443 ssl;" in block and "server_name ${COMPETENCY_DOMAIN};" in block
        ]
        self.assertEqual(1, len(public_competency))
        self.assertIn('proxy_set_header X-Agent-Client-Certificate "";', public_competency[0])
        self.assertRegex(
            public_competency[0],
            r"location \^~ /internal/agent/v1\s*\{\s*return 404;",
        )

    def test_http_redirects_only_expected_public_hostnames(self) -> None:
        config = NGINX_TEMPLATE.read_text(encoding="utf-8")
        blocks = server_blocks(config)
        default_http = [block for block in blocks if "listen 8080 default_server;" in block]
        self.assertEqual(1, len(default_http))
        self.assertIn("server_name _;", default_http[0])
        self.assertIn("return 404;", default_http[0])
        self.assertNotIn("return 301", default_http[0])
        self.assertNotIn(".well-known/acme-challenge", default_http[0])

        redirect = [
            block
            for block in blocks
            if "listen 8080;" in block and "return 301 https://$host$request_uri;" in block
        ]
        self.assertEqual(1, len(redirect))
        for hostname in (
            "${PERSONAL_WORKSPACE_DOMAIN}",
            "${COMPETENCY_DOMAIN}",
            "${MINIO_DOMAIN}",
        ):
            self.assertIn(hostname, redirect[0])
        self.assertNotIn("agent.${COMPETENCY_DOMAIN}", redirect[0])

    def test_private_shared_storage_buckets_are_not_public(self) -> None:
        config = NGINX_TEMPLATE.read_text(encoding="utf-8")
        s3_blocks = [
            block
            for block in server_blocks(config)
            if "listen 8443 ssl;" in block and "server_name ${MINIO_DOMAIN};" in block
        ]
        self.assertEqual(1, len(s3_blocks))
        for bucket in ("knowledge-private", "database-backups"):
            self.assertRegex(
                s3_blocks[0],
                rf"location = /{bucket}\s*\{{\s*return 404;",
            )
            self.assertRegex(
                s3_blocks[0],
                rf"location \^~ /{bucket}/\s*\{{\s*return 404;",
            )

    def test_shared_object_storage_has_one_public_hostname(self) -> None:
        config = NGINX_TEMPLATE.read_text(encoding="utf-8")
        storage_blocks = [
            block
            for block in server_blocks(config)
            if "listen 8443 ssl;" in block and "server_name ${MINIO_DOMAIN};" in block
        ]

        self.assertEqual(1, len(storage_blocks))
        self.assertNotIn("s3.${PERSONAL_WORKSPACE_DOMAIN}", config)
        self.assertNotIn("s3.${COMPETENCY_DOMAIN}", config)

        compose = COMPOSE.read_text(encoding="utf-8")
        self.assertEqual(3, compose.count("MINIO_PUBLIC_URL: ${APP_URL_SCHEMA}://${MINIO_DOMAIN}"))
        self.assertNotIn("s3.${PERSONAL_WORKSPACE_DOMAIN}", compose)
        self.assertNotIn("s3.${COMPETENCY_DOMAIN}", compose)

    def test_deploy_payload_preserves_local_runtime_state(self) -> None:
        workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
        prepare = (ROOT / "infra/scripts/deploy_prepare_payload.sh").read_text(encoding="utf-8")
        sync = (ROOT / "infra/scripts/deploy_sync_payload.sh").read_text(encoding="utf-8")
        self.assertIn("bash infra/scripts/deploy_prepare_payload.sh", workflow)
        self.assertIn("bash infra/scripts/deploy_sync_payload.sh", workflow)
        self.assertIn(
            "cp -a .dockerignore .sops.yaml Makefile docker-compose.yml config/ secrets/ infra/ .deploy-payload/",
            prepare,
        )
        self.assertLess(
            prepare.index("rm -rf -- .deploy-payload"),
            prepare.index("mkdir -p .deploy-payload"),
        )
        self.assertNotIn(".env .deploy-payload/", prepare)
        self.assertNotIn("GITHUB_ENV_VARS_JSON", workflow)
        self.assertNotIn("GITHUB_SECRETS_JSON", workflow)
        for excluded in (
            "--exclude '.deploy-state'",
            "--exclude '.alittlemore-infra-deploy-root'",
            "--exclude 'infra/nginx/certs/'",
        ):
            self.assertIn(excluded, sync)

    def test_sops_bootstrap_reads_local_scoped_files_without_github_secret_aliases(self) -> None:
        bootstrap = SOPS_BOOTSTRAP_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("--platform-env", bootstrap)
        self.assertIn("--personal-workspace-env", bootstrap)
        self.assertIn("--competency-trainer-env", bootstrap)
        self.assertIn("--age-recipient", bootstrap)
        self.assertNotIn("GITHUB_SECRETS_JSON", bootstrap)
        self.assertNotIn("source ", bootstrap)
        self.assertNotIn("git push", bootstrap)
        self.assertFalse((ROOT / ".github/workflows/migrate-secrets-to-sops.yml").exists())
        self.assertFalse((ROOT / "infra/scripts/migrate_github_secrets_to_sops.sh").exists())
        manifest = json.loads(
            (ROOT / "infra/deploy/runtime-secrets.manifest.json").read_text(encoding="utf-8")
        )
        for document in manifest["documents"]:
            for secret in document["secrets"]:
                self.assertNotIn("githubName", secret)

    def test_sops_bootstrap_reports_a_missing_option_value_as_usage_error(self) -> None:
        result = subprocess.run(
            ["bash", str(SOPS_BOOTSTRAP_SCRIPT), "--platform-env"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )

        self.assertEqual(2, result.returncode)
        self.assertIn("Usage:", result.stderr)
        self.assertNotIn("shift count", result.stderr)

    def test_deploy_connection_is_serialized_and_pinned_before_use(self) -> None:
        workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
        configure = (ROOT / "infra/scripts/deploy_configure_ssh.sh").read_text(encoding="utf-8")
        verify = (ROOT / "infra/scripts/deploy_verify_remote_root.sh").read_text(encoding="utf-8")
        activation = (ROOT / "infra/scripts/deploy_activate_payload.sh").read_text(encoding="utf-8")
        sync = (ROOT / "infra/scripts/deploy_sync_payload.sh").read_text(encoding="utf-8")
        cleanup = (ROOT / "infra/scripts/deploy_cleanup_payload.sh").read_text(encoding="utf-8")
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertIn('[[ "$REMOTE_USER" =~ ^[a-z_][a-z0-9_-]*$ ]]', configure)
        self.assertIn('[[ "$REMOTE_PATH" =~ ^/[A-Za-z0-9._/-]+$ ]]', configure)
        self.assertIn('if [ "$presented_fingerprint" = "$SSH_HOST_KEY_FINGERPRINT" ]', configure)
        self.assertIn("VALIDATED_REMOTE_USER", configure)
        self.assertIn("VALIDATED_REMOTE_PATH", configure)
        self.assertIn('[ "$(stat -c \'%a\' "$deploy_path")" = "700" ]', verify)
        self.assertIn('[ "$(stat -c \'%a\' "$sentinel")" = "600" ]', verify)
        self.assertLess(activation.index("flock -n 9"), activation.index('mv "$stage_path" "$release_path"'))
        self.assertLess(
            activation.index('mv "$stage_path" "$release_path"'),
            activation.index("make run"),
        )
        self.assertIn("ALITTLEMORE_RUNTIME_LOCK_HELD=1", activation)
        self.assertIn('cd "$release_path"', activation)
        pointer_commit = activation.split("commit_payload_pointer()", maxsplit=1)[1].split(
            "handle_activation_exit()", maxsplit=1
        )[0]
        self.assertLess(
            pointer_commit.index('mv -Tf "$temporary_current" "$current_link"'),
            pointer_commit.index('mv -Tf "$temporary_previous" "$deploy_path/previous"'),
        )
        self.assertLess(
            activation.index("make run"),
            activation.rindex("\n    commit_payload_pointer\n"),
        )
        self.assertIn("prune_runtime_releases", activation)
        self.assertIn('[[ "$candidate_name" =~ ^release-[0-9]+-[0-9]+$ ]]', activation)
        self.assertIn("cleanup_pointer_temporaries", activation)
        self.assertIn("runtime_release_is_committed", activation)
        self.assertIn("commit_payload_pointer", activation)
        self.assertIn("handle_activation_exit", activation)
        self.assertIn('export ALITTLEMORE_RELEASE_ID="$release_name"', activation)
        self.assertIn("Reconciled current to the last runtime-committed payload", activation)
        self.assertIn("existing_previous_path", activation)
        self.assertIn('"$existing_previous_path" ||', activation)
        self.assertIn("Deployment committed, but older runtime payloads could not be fully pruned", activation)
        self.assertIn("timeout 10m rsync", sync)
        self.assertIn('[[ "$stale_name" =~ ^incoming-[0-9]+-[0-9]+$ ]]', activation)
        self.assertIn("if: always()", workflow)
        self.assertIn("flock -n 9", cleanup)
        self.assertIn('rm -rf -- "$stage_path"', cleanup)

    def test_ssh_host_pin_ignores_keyscan_comments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            matching_key = temporary_path / "matching"
            unrelated_key = temporary_path / "unrelated"
            for key_path in (matching_key, unrelated_key):
                subprocess.run(
                    ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key_path)],
                    check=True,
                    capture_output=True,
                    text=True,
                )

            def known_hosts_line(key_path: Path) -> str:
                key_type, key_blob, *_ = key_path.with_suffix(".pub").read_text(
                    encoding="utf-8"
                ).split()
                return f"example.com {key_type} {key_blob}"

            matching_line = known_hosts_line(matching_key)
            keyscan_fixture = temporary_path / "keyscan-output"
            keyscan_fixture.write_text(
                "\n".join(
                    (
                        "# example.com:22 SSH-2.0-test-server",
                        known_hosts_line(unrelated_key),
                        "# example.com:22 SSH-2.0-test-server",
                        "example.com not-a-key invalid-data",
                        matching_line,
                    )
                )
                + "\n",
                encoding="utf-8",
            )

            fake_bin = temporary_path / "bin"
            fake_bin.mkdir()
            fake_keyscan = fake_bin / "ssh-keyscan"
            fake_keyscan.write_text(
                '#!/usr/bin/env bash\nexec /bin/cat "$SSH_KEYSCAN_FIXTURE"\n',
                encoding="utf-8",
            )
            fake_keyscan.chmod(0o755)
            fake_timeout = fake_bin / "timeout"
            fake_timeout.write_text(
                '#!/usr/bin/env bash\nshift\nexec "$@"\n',
                encoding="utf-8",
            )
            fake_timeout.chmod(0o755)

            fingerprint = subprocess.run(
                ["ssh-keygen", "-lf", str(matching_key.with_suffix(".pub")), "-E", "sha256"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.split()[1]
            home = temporary_path / "home"
            github_environment = temporary_path / "github-env"
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{fake_bin}:{environment['PATH']}",
                    "HOME": str(home),
                    "SSH_KEYSCAN_FIXTURE": str(keyscan_fixture),
                    "REMOTE_HOST": "example.com",
                    "REMOTE_USER": "deploy",
                    "REMOTE_PATH": "/srv/alittlemore-dev",
                    "SSH_HOST_KEY_FINGERPRINT": fingerprint,
                    "SSH_PRIVATE_KEY": "test-private-key",
                    "GITHUB_RUN_ID": "123",
                    "GITHUB_RUN_ATTEMPT": "1",
                    "GITHUB_ENV": str(github_environment),
                }
            )

            result = subprocess.run(
                ["bash", str(SSH_CONFIGURE_SCRIPT)],
                cwd=ROOT,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(
                [matching_line],
                (home / ".ssh/known_hosts").read_text(encoding="utf-8").splitlines(),
            )


if __name__ == "__main__":
    unittest.main()
