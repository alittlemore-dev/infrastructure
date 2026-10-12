from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "infra/backup/src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "infra/scripts"))
from infra_backup.status.projection import read_healthcheck, read_retained_sets


def projection(now: datetime) -> dict:
    stamp = now.isoformat()
    return {"schema_version": 1, "generated_at": stamp, "worker_heartbeat_at": stamp, "last_success_at": stamp, "freshness_threshold_seconds": 25200, "run_state": "idle", "destinations": [{"id": "internal", "required": True, "last_success_at": stamp, "retained_sets": [{"run_id": "fixture", "roles": ["latest", "weekly_current"], "complete": True, "completed_at": stamp, "total_bytes": 42}]}]}


class ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.now = datetime(2026, 10, 11, tzinfo=timezone.utc)

    def write(self, payload):
        (self.root / "status.json").write_text(json.dumps(payload))

    def status(self, now=None):
        return read_healthcheck(self.root, now or self.now)["systems"][0]["status"]

    def test_freshness_is_computed_at_request_time(self):
        payload = projection(self.now)
        self.write(payload)
        self.assertEqual(self.status(), "healthy")
        self.assertEqual(self.status(self.now + timedelta(seconds=121)), "stale")
        payload["worker_heartbeat_at"] = (self.now + timedelta(hours=8)).isoformat()
        self.write(payload)
        self.assertEqual(self.status(self.now + timedelta(hours=8)), "stale")

    def test_failed_attempt_does_not_show_previous_success_as_healthy(self):
        payload = projection(self.now)
        payload.update(run_state="failed", last_error={"code": "source_failed", "source_id": "postgres"})
        self.write(payload)
        self.assertEqual(self.status(), "degraded")
        payload["last_success_at"] = None
        self.write(payload)
        self.assertEqual(self.status(), "failed")

    def test_missing_corrupt_oversize_and_symlink_fail_closed(self):
        self.assertEqual(self.status(), "initializing")
        path = self.root / "status.json"
        path.write_text("{")
        self.assertEqual(self.status(), "unavailable")
        path.write_bytes(b" " * (1024 * 1024 + 1))
        self.assertEqual(self.status(), "unavailable")
        path.unlink()
        outside = self.root / "outside.json"
        outside.write_text(json.dumps(projection(self.now)))
        path.symlink_to(outside)
        self.assertEqual(self.status(), "unavailable")

    def test_allowlist_removes_private_values(self):
        payload = projection(self.now)
        payload["endpoint"] = "private-endpoint"
        payload["password"] = "private-secret"
        payload["destinations"][0]["secret_key"] = "private-secret"
        payload["last_error"] = {"code": "delivery_failed", "message": "private-secret"}
        self.write(payload)
        response = json.dumps(read_healthcheck(self.root, self.now))
        self.assertNotIn("private-secret", response)
        self.assertNotIn("private-endpoint", response)

    def test_running_job_and_retained_roles(self):
        payload = projection(self.now)
        payload.update(run_state="running", current_run={"id": "fixture2", "phase": "collecting", "started_at": self.now.isoformat()})
        self.write(payload)
        self.assertEqual(self.status(), "healthy")
        sets = read_retained_sets(self.root, self.now)["destinations"][0]["retained_sets"]
        self.assertEqual(len(sets), 1)
        self.assertEqual(sets[0]["roles"], ["latest", "weekly_current"])

    def test_invalid_schema_status_and_roles_are_unavailable(self):
        for key, value in (("schema_version", 2), ("schema_version", True), ("run_state", "unknown"), ("freshness_threshold_seconds", True)):
            with self.subTest(key=key):
                payload = projection(self.now)
                payload[key] = value
                self.write(payload)
                self.assertEqual(self.status(), "unavailable")

    def test_new_required_destination_cannot_inherit_old_success(self):
        payload = projection(self.now)
        payload["destinations"].append({"id": "second", "required": True})
        self.write(payload)
        self.assertEqual(self.status(), "unavailable")
