from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "infra/deploy/runtime-secrets.manifest.json"


class TelegramProxyContractTest(unittest.TestCase):
    def test_all_personal_runtime_processes_share_one_proxy_secret(self) -> None:
        environment = dict(
            os.environ, PERSONAL_WORKSPACE_ACTIVE_BACKEND="personal-workspace-backend-green",
            PERSONAL_WORKSPACE_ENV_FILE=str(ROOT / "config/personal-workspace/production.env"),
        )
        for document in json.loads(MANIFEST.read_text())["documents"]:
            for secret in document["secrets"]:
                environment[secret["composeVariable"]] = "/dev/null"
        result = subprocess.run(
            ["docker", "compose", "--env-file", "config/platform/development.env", "-f",
             "docker-compose.yml", "config", "--format", "json"],
            env=environment, cwd=ROOT, capture_output=True, text=True, check=True, timeout=15,
        )
        compose = json.loads(result.stdout)
        for role in ("backend", "taskiq-worker", "taskiq-scheduler"):
            for slot in ("blue", "green"):
                service = compose["services"][f"personal-workspace-{role}-{slot}"]
                self.assertEqual(
                    "/run/secrets/telegram_proxy_urls",
                    service["environment"]["TELEGRAM_PROXY_URLS_FILE"],
                )
                self.assertNotIn("TELEGRAM_PROXY_URLS", service["environment"])
                self.assertIn(
                    {"source": "personal_workspace_telegram_proxy_urls", "target": "telegram_proxy_urls"},
                    service["secrets"],
                )

    def run_secret_tools(
        self, *, values: dict[str, str], secret_name: str = "TELEGRAM_PROXY_URLS",
        allow_empty: bool = True, allow_missing: object = True, builder: bool = False,
    ) -> tuple[subprocess.CompletedProcess[str], str | None, str]:
        with tempfile.TemporaryDirectory() as temporary:
            sandbox = Path(temporary)
            repo = sandbox / "repo"
            repo.mkdir()
            manifest = json.loads(MANIFEST.read_text())
            document = next(d for d in manifest["documents"] if d["name"] == "personal-workspace-telegram")
            proxy_spec = next(s for s in document["secrets"] if s["name"] == "TELEGRAM_PROXY_URLS")
            document["secrets"] = [dict(proxy_spec, name=secret_name, allowEmpty=allow_empty, allowMissing=allow_missing)]
            manifest_path = sandbox / "manifest.json"
            manifest_path.write_text(json.dumps({"documents": [document]}))
            source = sandbox / "source.env"
            source.write_text("".join(f"{name}='{value}'\n" for name, value in values.items()))
            source.chmod(0o600)
            encrypted = repo / document["path"]
            encrypted.parent.mkdir(parents=True)
            encrypted.write_text("fixture-encrypted")
            key = sandbox / "age-key"
            key.write_text("test-only-key")
            key.chmod(0o600)
            fake_sops = sandbox / "sops"
            fake_sops.write_text("""#!/usr/bin/env python3
import json, os, sys
print(json.dumps(json.load(sys.stdin) if 'encrypt' in sys.argv else json.loads(os.environ['TEST_VALUES'])))
""")
            fake_sops.chmod(0o755)
            environment = dict(os.environ, TEST_VALUES=json.dumps(values))
            common = ["--manifest", str(manifest_path), "--repo-dir", str(repo), "--sops-binary", str(fake_sops)]
            if builder:
                command = ["python3", str(ROOT / "infra/scripts/build_sops_documents.py"), *common,
                           "--source-env", f"personal-workspace-telegram={source}",
                           "--age-recipient", "age1" + "q" * 58,
                           "--age-recipient", "age1" + "p" * 58]
            else:
                command = ["python3", str(ROOT / "infra/scripts/materialize_sops_secrets.py"), *common,
                           "--output-dir", str(repo / "runtime"), "--compose-env-output", str(repo / "compose.env"),
                           "--age-key-file", str(key)]
            result = subprocess.run(command, env=environment, capture_output=True, text=True, check=False, timeout=5)
            secret_file = repo / "runtime" / proxy_spec["target"]
            if builder and result.returncode == 0:
                value = json.loads(encrypted.read_text()).get(secret_name)
            else:
                value = secret_file.read_text() if secret_file.exists() else None
            paths = (repo / "compose.env").read_text() if (repo / "compose.env").exists() else ""
            if secret_file.exists():
                self.assertEqual(0o444, secret_file.stat().st_mode & 0o777)
                self.assertEqual(0o700, secret_file.parent.stat().st_mode & 0o777)
            return result, value, paths

    def test_missing_proxy_is_materialized_empty_and_new_bootstrap_includes_direct_list(self) -> None:
        for builder in (False, True):
            result, value, _ = self.run_secret_tools(values={}, builder=builder)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertEqual("[]" if builder else "", value)

    def test_proxy_credentials_round_trip_without_output_or_compose_env_exposure(self) -> None:
        for value in (
            "", "[]",
            json.dumps(["socks5://test-user:test-pass@proxy.example.test:1080"]),
            json.dumps([
                "socks5://test-user:test-pass@proxy.example.test:1080",
                "http://test-user:p%40ss%3Aword@proxy.example.test:8080",
            ]),
        ):
            for builder in (False, True):
                result, actual, paths = self.run_secret_tools(values={"TELEGRAM_PROXY_URLS": value}, builder=builder)
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                self.assertEqual(value, actual)
                if value:
                    self.assertNotIn(value, result.stdout + result.stderr + paths)

    def test_missing_compatibility_does_not_extend_to_other_secrets_or_required_values(self) -> None:
        for builder in (False, True):
            for name, allow_empty, allow_missing in (
                ("TELEGRAM_SERVICE_SECRET", True, True),
                ("TELEGRAM_PROXY_URL", True, True),
                ("TELEGRAM_PROXY_URLS", False, True),
                ("TELEGRAM_PROXY_URLS", True, "true"),
            ):
                result, value, _ = self.run_secret_tools(
                    values={}, secret_name=name, allow_empty=allow_empty,
                    allow_missing=allow_missing, builder=builder,
                )
                self.assertNotEqual(0, result.returncode)
                self.assertIsNone(value)
