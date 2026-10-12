"""CLI exit codes, secret-free reads and manifest verification."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "infra/backup/src"))
sys.path.insert(0, str(ROOT / "infra/scripts"))
import infra_backup.cli as backup_cli  # noqa: E402
from infra_backup.state import run_lock  # noqa: E402
from test_backup_worker import Fixture  # noqa: E402


class BackupCliTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.fixture = Fixture(Path(temporary.name))

    def invoke(self, arguments):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = backup_cli.main(arguments)
        return code, output.getvalue()

    def run_arguments(self):
        return ["run", "--config", "/unused/sources", "--destinations", "/unused/destinations",
                "--state-dir", str(self.fixture.root / "state"), "--work-dir", str(self.fixture.root / "work"),
                "--projection-dir", str(self.fixture.root / "projection")]

    def test_status_and_health_need_no_config_or_secrets(self):
        worker = self.fixture.worker()
        worker.heartbeat()
        with patch.object(backup_cli, "load_config", side_effect=AssertionError("must not read config")):
            code, output = self.invoke(["status", "--state-dir", str(self.fixture.root / "state"), "--json"])
            self.assertEqual(code, 0)
            data = json.loads(output)
            self.assertEqual(data["run_state"], "initializing")
            self.assertNotIn("pending", data)
            code, _ = self.invoke(["health", "--state-dir", str(self.fixture.root / "state")])
            self.assertEqual(code, 1)

    def test_manual_required_optional_and_busy_exit_codes(self):
        for failing, expected in ((set(), 0), ({"external"}, 0), ({"internal"}, 1)):
            with self.subTest(failing=failing):
                self.fixture.failing = failing
                with patch.object(backup_cli, "load_config", return_value=self.fixture.config), patch.object(backup_cli, "BackupWorker", return_value=self.fixture.worker()):
                    code, _ = self.invoke(self.run_arguments())
                self.assertEqual(code, expected)
        with run_lock(self.fixture.root / "state"):
            with patch.object(backup_cli, "load_config", return_value=self.fixture.config), patch.object(backup_cli, "BackupWorker", return_value=self.fixture.worker()):
                code, output = self.invoke(self.run_arguments())
        self.assertEqual(code, 2)
        self.assertIn("busy", output)

    def test_safe_failure_output(self):
        with patch.object(backup_cli, "load_config", side_effect=RuntimeError("credential-value http://private")):
            code, output = self.invoke(self.run_arguments())
        self.assertEqual(code, 1)
        self.assertNotIn("credential-value", output)
        self.assertNotIn("http://", output)
        self.assertEqual(json.loads(output)["error"]["code"], "configuration_invalid")

    def test_verify_checks_payload_and_rejects_tamper(self):
        self.fixture.failing = {"external"}
        worker = self.fixture.worker()
        self.assertTrue(worker.run_once())
        run_id = self.fixture.collections[0]
        directory = self.fixture.root / "work" / run_id
        code, output = self.invoke(["verify", "--directory", str(directory)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)["run_id"], run_id)
        (directory / "sources/fixture/payload").write_bytes(b"corrupt")
        code, _ = self.invoke(["verify", "--directory", str(directory)])
        self.assertEqual(code, 1)

    def test_download_uses_only_destination_config(self):
        access, secret = self.fixture.root / "access", self.fixture.root / "secret"
        access.write_text("fixture-access")
        secret.write_text("fixture-secret")
        inventory = self.fixture.root / "destinations.json"
        inventory.write_text(json.dumps({"destinations": [{"id": "internal", "endpoint": "http://store", "region_env": "FIXTURE_REGION",
            "bucket": "backups", "prefix": "managed", "access_key_file": str(access), "secret_key_file": str(secret), "required": True, "internal": True}]}))
        with patch.dict("os.environ", {"FIXTURE_REGION": "region"}), patch.object(backup_cli, "download") as download:
            code, _ = self.invoke(["download", "--destination", "internal", "--run-id", "run-fixture", "--output-dir", str(self.fixture.root / "download"), "--destinations", str(inventory)])
        self.assertEqual(code, 0)
        self.assertEqual(download.call_args.args[1], "run-fixture")

    def test_verify_large_committed_manifest_still_checks_payload(self):
        content = b"recovery data"
        artifacts = [{"path": f"sources/fixture/{index:05d}/" + "x" * 240,
                      "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
                     for index in range(6000)]
        manifest = {"schema_version": 1, "run_id": "large", "complete": True,
                    "started_at": "2026-10-11T00:00:00Z", "completed_at": "2026-10-11T00:01:00Z",
                    "delivery_completed_at": "2026-10-11T00:02:00Z",
                    "sources": [{"id": "fixture", "type": "s3", "status": "success", "complete": True,
                                 "artifacts": artifacts}], "artifacts": artifacts}
        raw = json.dumps(manifest).encode()
        self.assertGreater(len(raw), 4 * 1024**2)

        def open_payload(_directory, path):
            return io.BytesIO(raw if path == "manifest.json" else content)

        with patch.object(backup_cli, "_open", side_effect=open_payload):
            code, output = self.invoke(["verify", "--directory", str(self.fixture.root)])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output), {"run_id": "large", "complete": True,
                                                 "total_bytes": len(content) * len(artifacts)})
            content = b"corrupt"
            code, _ = self.invoke(["verify", "--directory", str(self.fixture.root)])
            self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
