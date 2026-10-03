from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class ApiDocsContractTest(unittest.TestCase):
    def test_nginx_serves_docs_and_only_reads_schemas_on_the_internal_listener(self) -> None:
        result = subprocess.run(
            ["python3", str(ROOT / "infra/scripts/test_api_docs_edge.py")],
            capture_output=True, text=True, check=False, timeout=130,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_compose_keeps_schema_listener_internal_and_configures_both_frontend_slots(self) -> None:
        manifest = json.loads((ROOT / "infra/deploy/runtime-secrets.manifest.json").read_text())
        environment = dict(os.environ)
        for document in manifest["documents"]:
            for secret in document["secrets"]:
                environment[secret["composeVariable"]] = "/dev/null"
        result = subprocess.run(
            ["docker", "compose", "--env-file", "config/platform/development.env", "config", "--format", "json"],
            cwd=ROOT, env=environment, capture_output=True, text=True, check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        services = json.loads(result.stdout)["services"]
        for slot in ("blue", "green"):
            origin = services[f"frontend-{slot}"]["environment"]["API_SCHEMA_ORIGIN"]
            self.assertEqual("http://nginx:18084", origin)
        self.assertFalse(any(port["target"] == 18084 for port in services["nginx"]["ports"]))
