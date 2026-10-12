"""Scheduling, durable retry and safe independent status behavior."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "infra/backup/src"))
sys.path.insert(0, str(ROOT / "infra/scripts"))
from infra_backup.config import BackupConfig, DestinationConfig, SourceConfig  # noqa: E402
from infra_backup.retention import RetentionRefs, advance_retention  # noqa: E402
from infra_backup.sources import CollectionResult  # noqa: E402
from infra_backup.state import LockBusy, run_lock, write_json_atomic  # noqa: E402
from infra_backup.status.projection import read_healthcheck  # noqa: E402
from infra_backup.storage import DeliveryResult, _manifest  # noqa: E402
from infra_backup.worker import BackupWorker, WorkerStopped, stamp  # noqa: E402
from infra_backup import storage  # noqa: E402
from test_backup_storage import FakeS3, S3Error  # noqa: E402


class Fixture:
    def __init__(self, root):
        self.root = root
        self.now = datetime(2026, 1, 5, tzinfo=timezone.utc)
        self.collections, self.deliveries = [], []
        self.failing, self.source_failed = set(), False
        self.refs, self.metadata = {}, {}
        self.destinations = tuple(DestinationConfig(name, "http://store", "region", "backups", "managed", root / "access", root / "secret", required)
                                  for name, required in (("internal", True), ("external", False)))
        self.config = BackupConfig((SourceConfig("fixture", "valkey", host="fixture"),), self.destinations,
                                   interval_seconds=60, freshness_threshold_seconds=120, pending_budget_bytes=10000)

    def collect(self, config, directory, **kwargs):
        self.collections.append(directory.name)
        path = directory / "sources/fixture/payload"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"fixture-data")
        artifacts = [{"path": "sources/fixture/payload", "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}]
        source = {"id": "fixture", "type": "valkey", "complete": not self.source_failed,
                  "status": "failed" if self.source_failed else "success", "artifacts": artifacts}
        return CollectionResult(not self.source_failed, [source], artifacts)

    def deliver(self, directory, destination):
        self.deliveries.append((directory.name, destination.id))
        manifest = _manifest(json.loads((directory / "manifest.json").read_text()))
        if destination.id in self.failing:
            return DeliveryResult(False, directory.name, error={"code": "delivery_failed"})
        refs = advance_retention(self.refs.get(destination.id, RetentionRefs()), directory.name, self.now)
        self.refs[destination.id] = refs
        self.metadata.setdefault(destination.id, {})[directory.name] = {"run_id": directory.name, "completed_at": stamp(self.now), "bytes": sum(item["bytes"] for item in manifest["artifacts"])}
        return DeliveryResult(True, directory.name, stamp(self.now), 12, refs)

    def sets(self, destination):
        refs = self.refs[destination.id]
        return [item for run_id, item in self.metadata[destination.id].items() if run_id in (refs.latest, refs.weekly_current, refs.weekly_previous)]

    def worker(self, **kwargs):
        return BackupWorker(self.config, self.root / "state", self.root / "work", self.root / "projection",
                            clock=lambda: self.now, collector=self.collect, delivery=self.deliver,
                            sets=self.sets, retention=lambda dest: self.refs[dest.id], **kwargs)


class BackupWorkerTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.fixture = Fixture(Path(temporary.name))
        self.worker = self.fixture.worker()

    def health(self):
        return read_healthcheck(self.fixture.root / "projection", self.fixture.now)["systems"][0]

    def test_first_run_restart_deadline_and_one_overdue_attempt(self):
        self.assertTrue(self.worker.tick())
        self.assertEqual(self.health()["status"], "healthy")
        self.assertIsNone(self.fixture.worker().tick())
        self.fixture.now += timedelta(days=2)
        self.assertTrue(self.fixture.worker().tick())
        self.assertEqual(len(self.fixture.collections), 2)
        self.assertIsNone(self.fixture.worker().tick())
        sets = self.health()["backup"]["destinations"][0]["retained_sets"]
        self.assertEqual(sets[-1]["total_bytes"], 12)

    def test_failed_source_does_not_deliver_and_restart_is_throttled(self):
        self.fixture.source_failed = True
        self.assertFalse(self.worker.tick())
        self.assertEqual(self.fixture.deliveries, [])
        self.assertEqual(self.worker.snapshot()["pending"], [])
        self.assertEqual(self.health()["status"], "failed")
        self.assertIsNone(self.fixture.worker().tick())
        self.assertEqual(len(self.fixture.collections), 1)

    def test_retry_of_older_delivery_keeps_later_collection_failure(self):
        self.fixture.config = replace(self.fixture.config, interval_seconds=600, freshness_threshold_seconds=1200)
        self.worker = self.fixture.worker()
        self.fixture.failing = {"external"}
        self.assertTrue(self.worker.run_once())
        self.fixture.source_failed = True
        self.fixture.now += timedelta(seconds=1)
        self.assertFalse(self.worker.run_once())
        self.fixture.failing.clear()
        self.fixture.now += timedelta(seconds=300)
        self.assertFalse(self.fixture.worker().tick())
        self.assertEqual(self.worker.snapshot()["pending"], [])
        self.assertEqual(self.health()["status"], "degraded")
        self.assertEqual(self.health()["backup"]["last_error"]["code"], "source_failed")

    def test_optional_failure_retries_same_run_before_new_collection(self):
        self.fixture.failing = {"external"}
        self.assertTrue(self.worker.run_once())
        first = self.fixture.collections[0]
        self.assertEqual(self.health()["status"], "degraded")
        self.fixture.now += timedelta(seconds=60)
        self.fixture.failing.clear()
        self.assertTrue(self.fixture.worker().tick())
        self.assertEqual(self.fixture.deliveries[2], (first, "external"))
        self.assertEqual(len(self.fixture.collections), 2)
        self.assertEqual(self.worker.snapshot()["pending"], [])

    def test_required_failure_and_pending_retry_without_new_dump(self):
        self.fixture.failing = {"internal"}
        self.assertFalse(self.worker.run_once())
        first = self.fixture.collections[0]
        self.fixture.failing.clear()
        self.fixture.now += timedelta(seconds=1)
        with self.worker._state() as state:
            state["next_retry_at"] = stamp(self.fixture.now)
        self.assertTrue(self.fixture.worker().tick())
        self.assertEqual(self.fixture.collections, [first])
        self.assertEqual(self.fixture.deliveries[-1], (first, "internal"))
        self.assertEqual(self.health()["status"], "healthy")

    def test_lost_ack_retry_after_pruning_preserves_newer_backup_and_freshness(self):
        fixture = self.fixture
        fixture.root = fixture.root.resolve()

        class LostAck(FakeS3):
            lose_next = False

            def put_object(self, Bucket, Key, Body, **kwargs):
                result = super().put_object(Bucket, Key, Body, **kwargs)
                if self.lose_next and Key.endswith("/retention.json"):
                    self.lose_next = False
                    raise S3Error("ReadTimeout")
                return result

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixture.now

        client = LostAck()
        config = replace(fixture.config, destinations=fixture.destinations[:1])
        worker = BackupWorker(config, fixture.root / "state", fixture.root / "work",
                              fixture.root / "projection", clock=lambda: fixture.now,
                              collector=fixture.collect, delivery=storage.deliver,
                              retention=storage.read_retention, sets=storage.retained_sets)
        with patch.object(storage, "_client", return_value=client), patch.object(storage, "datetime", Clock):
            self.assertTrue(worker.run_once())
            fixture.now += timedelta(seconds=1)
            client.lose_next = True
            self.assertFalse(worker.run_once())
            old = worker.snapshot()["pending"][0]["run_id"]
            client.fail = ("get", f"managed/sets/{old}/payload/sources/fixture/payload")
            fixture.now += timedelta(seconds=1)
            self.assertTrue(worker.run_once())
            newest = fixture.collections[-1]
            refs = storage.read_retention(config.destinations[0])
            self.assertEqual(refs.latest, newest)
            self.assertNotIn(f"managed/sets/{old}/manifest.json", client.objects)
            last_success = worker.snapshot()["last_success_at"]

            client.fail = None
            fixture.now += timedelta(seconds=121)
            # Even if status rereading fails, the superseding backup remains visible.
            def fail_read(_destination):
                raise TimeoutError("private details")

            worker.sets = fail_read
            worker._deliver_pending()
            self.assertTrue(worker._finish())
            self.assertEqual(storage.read_retention(config.destinations[0]), refs)
            self.assertEqual(worker.snapshot()["pending"], [])
            self.assertEqual(worker.snapshot()["last_success_at"], last_success)
            self.assertEqual(worker.snapshot()["destinations"][0]["last_success_at"], last_success)
            self.assertNotIn(old, {item["run_id"] for item in worker.snapshot()["destinations"][0]["retained_sets"]})
            self.assertIn(newest, {item["run_id"] for item in worker.snapshot()["destinations"][0]["retained_sets"]})
            self.assertEqual(self.health()["status"], "stale")
            output = fixture.root / "recovery"
            storage.download(config.destinations[0], newest, output)
            self.assertEqual((output / "sources/fixture/payload").read_bytes(), b"fixture-data")

    def test_retained_status_read_failure_keeps_verified_sets_and_reports_degraded(self):
        for failed_destination in ("internal", "external"):
            with self.subTest(destination=failed_destination), tempfile.TemporaryDirectory() as temporary:
                fixture = Fixture(Path(temporary))
                worker = fixture.worker()
                self.assertTrue(worker.run_once())
                fixture.now += timedelta(seconds=1)

                def fail_read(destination):
                    if destination.id == failed_destination:
                        raise TimeoutError("private storage details")
                    return fixture.sets(destination)

                worker.sets = fail_read
                self.assertTrue(worker.run_once())
                self.assertEqual(worker.snapshot()["pending"], [])
                system = read_healthcheck(fixture.root / "projection", fixture.now)["systems"][0]
                self.assertEqual(system["status"], "degraded")
                destination = next(item for item in system["backup"]["destinations"] if item["id"] == failed_destination)
                self.assertEqual(destination["cleanup_error"]["code"], "cleanup_failed")
                self.assertEqual({item["run_id"] for item in destination["retained_sets"]}, set(fixture.collections))
                self.assertTrue(all(item["roles"] for item in destination["retained_sets"]))
                self.assertNotIn("private storage details", json.dumps(system))

    def test_byte_budget_keeps_freshest_and_reports_eviction(self):
        self.fixture.failing = {"external"}
        self.assertTrue(self.worker.run_once())
        first = self.fixture.collections[0]
        budget = self.worker.snapshot()["pending"][0]["bytes"] + 1
        object.__setattr__(self.fixture.config, "pending_budget_bytes", budget)
        self.fixture.now += timedelta(seconds=60)
        self.assertTrue(self.worker.run_once())
        state = self.worker.snapshot()
        self.assertEqual([item["run_id"] for item in state["pending"]], [self.fixture.collections[1]])
        self.assertFalse((self.fixture.root / "work" / first).exists())
        external = next(item for item in self.health()["backup"]["destinations"] if item["id"] == "external")
        self.assertEqual(external["delivery_gap_count"], 1)
        self.assertEqual(external["last_error"]["code"], "delivery_evicted")

    def test_interrupted_collection_recovered_without_early_retry(self):
        run_id = "interrupted-run"
        (self.fixture.root / "work" / run_id).mkdir()
        with self.worker._state() as state:
            state.update(current_run={"id": run_id, "phase": "collecting", "started_at": stamp(self.fixture.now)},
                         run_state="running", next_due_at=stamp(self.fixture.now + timedelta(seconds=60)))
        self.assertIsNone(self.fixture.worker().tick())
        self.assertEqual(self.health()["status"], "failed")
        self.assertEqual(self.worker.snapshot()["attempts"][-1]["error"]["code"], "interrupted")
        self.assertFalse((self.fixture.root / "work" / run_id).exists())

    def test_shared_lock_and_disabled_schedule(self):
        self.assertIsNone(self.worker.tick(schedule_enabled=False))
        self.assertEqual(self.health()["status"], "initializing")
        with run_lock(self.fixture.root / "state"):
            with self.assertRaises(LockBusy):
                self.worker.run_once()
            self.assertIsNone(self.worker.tick())
        self.assertEqual(self.fixture.collections, [])

    def test_projection_failure_does_not_cancel_success_and_no_private_fields(self):
        def failure(*_):
            raise RuntimeError("sensitive endpoint and secret")
        self.worker = self.fixture.worker(publisher=failure)
        self.assertTrue(self.worker.run_once())
        state_status = json.loads((self.fixture.root / "state/status.json").read_text())
        self.assertEqual(state_status["last_error"]["code"], "projection_failed")
        self.assertNotIn("pending", state_status)
        self.assertNotIn("artifacts", state_status["sources"][0])
        self.assertNotIn("secret", json.dumps(state_status))
        self.assertIsNotNone(self.worker.snapshot()["last_success_at"])

    def test_heartbeat_advances_during_blocking_collection(self):
        entered, release = threading.Event(), threading.Event()
        original = self.worker.collector
        def slow(*args, **kwargs):
            entered.set()
            release.wait(3)
            return original(*args, **kwargs)
        self.worker.collector = slow
        thread = threading.Thread(target=self.worker.serve, kwargs={"heartbeat_seconds": 0.02})
        thread.start()
        self.addCleanup(release.set)
        self.addCleanup(self.worker.stop_event.set)
        self.assertTrue(entered.wait(2))
        self.fixture.now += timedelta(seconds=100)
        limit = time.monotonic() + 2
        while self.worker.snapshot().get("worker_heartbeat_at") != stamp(self.fixture.now) and time.monotonic() < limit:
            time.sleep(0.01)
        self.assertEqual(self.worker.snapshot()["worker_heartbeat_at"], stamp(self.fixture.now))
        self.worker.stop_event.set()
        release.set()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertIsNone(self.worker.snapshot().get("last_success_at"))

    def test_signal_interrupt_does_not_publish_success(self):
        def interrupted(*args, **kwargs):
            raise WorkerStopped()
        self.worker.collector = interrupted
        self.assertFalse(self.worker.run_once())
        self.assertEqual(self.worker.snapshot()["last_error"]["code"], "interrupted")
        self.assertEqual(self.fixture.deliveries, [])

    def test_attempt_history_is_bounded(self):
        self.fixture.source_failed = True
        for _ in range(35):
            self.worker.run_once()
        self.assertEqual(len(self.worker.snapshot()["attempts"]), 32)


if __name__ == "__main__":
    unittest.main()
