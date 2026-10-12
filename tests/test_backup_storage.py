import copy
import hashlib
import io
import json
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "infra/backup/src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "infra/scripts"))
import infra_backup.storage as storage
from infra_backup.config import DestinationConfig
from infra_backup.retention import RetentionRefs, protected_ids


class S3Error(Exception):
    def __init__(self, code):
        super().__init__("credential-private-error")
        self.response = {"Error": {"Code": code}}


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.fail = None
        self.corrupt = None
        self.events = []
        self.uploads = {}

    def get_object(self, Bucket, Key):
        self.events.append(("get", Key))
        if self.fail == ("get", Key):
            raise S3Error("Offline")
        if Key not in self.objects:
            raise S3Error("NoSuchKey")
        data = self.objects[Key]
        if self.corrupt == Key:
            data += b"bad"
        return {"Body": io.BytesIO(data), "ETag": hashlib.md5(data).hexdigest()}

    def put_object(self, Bucket, Key, Body, **kwargs):
        self.events.append(("put", Key))
        if self.fail == ("put", Key):
            raise S3Error("Offline")
        if kwargs.get("IfNoneMatch") == "*" and Key in self.objects:
            raise S3Error("PreconditionFailed")
        if "IfMatch" in kwargs and hashlib.md5(self.objects[Key]).hexdigest() != kwargs["IfMatch"]:
            raise S3Error("PreconditionFailed")
        self.objects[Key] = Body.read() if hasattr(Body, "read") else Body
        return {"ETag": hashlib.md5(self.objects[Key]).hexdigest()}

    def list_objects_v2(self, Bucket, Prefix, **kwargs):
        if self.fail == ("list", Prefix):
            raise S3Error("Offline")
        return {"Contents": [{"Key": key} for key in sorted(self.objects) if key.startswith(Prefix)]}

    def delete_object(self, Bucket, Key):
        self.events.append(("delete", Key))
        if self.fail == ("delete", Key):
            raise S3Error("Offline")
        self.objects.pop(Key, None)

    def create_multipart_upload(self, Bucket, Key):
        self.uploads[Key] = []
        return {"UploadId": Key}

    def list_multipart_uploads(self, Bucket, Prefix, **kwargs):
        return {"Uploads": [{"Key": key, "UploadId": key} for key in self.uploads if key.startswith(Prefix)]}

    def upload_part(self, Bucket, Key, UploadId, PartNumber, Body):
        self.events.append(("part", Key))
        self.uploads[Key].append(Body)
        if self.fail == ("part", Key):
            raise S3Error("Offline")
        return {"ETag": "multipart-etag"}

    def complete_multipart_upload(self, Bucket, Key, UploadId, MultipartUpload, IfNoneMatch):
        self.put_object(Bucket, Key, b"".join(self.uploads.pop(Key)), IfNoneMatch=IfNoneMatch)

    def abort_multipart_upload(self, Bucket, Key, UploadId):
        self.uploads.pop(Key, None)

    def close(self):
        pass


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.destination = DestinationConfig("internal", "http://example.test", "test", "backup-bucket",
                                             "managed/v1", self.root / "access", self.root / "secret", True)
        self.client = FakeS3()
        self.patch = patch.object(storage, "_client", return_value=self.client)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.namespace = "managed/v1/"
        self.captured_at = datetime(2026, 10, 11, tzinfo=timezone.utc)

    def run_directory(self, run_id="run-1", content=b"recovery data"):
        directory = self.root / run_id
        directory.mkdir()
        artifact_path = directory / "sources/database/database.dump"
        artifact_path.parent.mkdir(parents=True)
        artifact_path.write_bytes(content)
        artifact = {"path": "sources/database/database.dump", "bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest()}
        captured_at = self.captured_at.isoformat()
        self.captured_at += timedelta(seconds=1)
        manifest = {"schema_version": 1, "run_id": run_id, "complete": True,
                    "started_at": captured_at, "completed_at": captured_at,
                    "sources": [{"id": "database", "type": "postgres", "status": "success",
                                 "complete": True, "artifacts": [artifact]}], "artifacts": [artifact]}
        (directory / "manifest.json").write_text(json.dumps(manifest))
        return directory

    def key(self, run_id="run-1", suffix="payload/sources/database/database.dump"):
        return self.namespace + "sets/" + run_id + "/" + suffix

    def deliver(self, run_id="run-1", content=b"recovery data"):
        return storage.deliver(self.run_directory(run_id, content), self.destination)

    def change_json(self, key, mutate):
        value = json.loads(self.client.objects[key])
        mutate(value)
        self.client.objects[key] = json.dumps(value).encode()

    def test_upload_readback_commit_references_order_and_private_download(self):
        result = self.deliver()
        self.assertTrue(result.success)
        self.assertEqual(protected_ids(result.refs), {"run-1"})
        events = self.client.events
        self.assertLess(events.index(("get", self.key())), events.index(("put", self.key(suffix="manifest.json"))))
        self.assertLess(events.index(("put", self.key(suffix="manifest.json"))), events.index(("put", self.namespace + "retention.json")))
        output = self.root / "download"
        storage.download(self.destination, "run-1", output)
        self.assertEqual((output / "sources/database/database.dump").read_bytes(), b"recovery data")
        self.assertEqual(output.stat().st_mode & 0o777, 0o700)
        self.assertEqual((output / "manifest.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual(storage.read_retention(self.destination), result.refs)
        self.assertEqual(storage.retained_sets(self.destination)[0]["bytes"], len(b"recovery data"))

    def test_upload_verification_manifest_and_refs_faults_leave_prior_references(self):
        self.assertTrue(self.deliver("old").success)
        original = self.client.objects[self.namespace + "retention.json"]
        for index, (operation, suffix) in enumerate((("put", "payload/sources/database/database.dump"),
                                                   ("get", "payload/sources/database/database.dump"),
                                                   ("put", "manifest.json"), ("put", None))):
            run_id = "failed-" + str(index)
            key = self.key(run_id, suffix) if suffix else self.namespace + "retention.json"
            self.client.fail = (operation, key)
            result = self.deliver(run_id)
            self.assertFalse(result.success)
            self.assertNotIn("credential", json.dumps(result.error))
            self.assertEqual(self.client.objects[self.namespace + "retention.json"], original)
            self.assertIn(self.key("old"), self.client.objects)
            self.client.fail = None
            self.assertTrue(storage.deliver(self.root / run_id, self.destination).success)
            original = self.client.objects[self.namespace + "retention.json"]

    def test_corrupt_readback_never_commits_and_no_etag_hash_assumption(self):
        self.client.corrupt = self.key()
        result = self.deliver()
        self.assertFalse(result.success)
        self.assertNotIn(self.key(suffix="manifest.json"), self.client.objects)
        self.assertNotIn(self.namespace + "retention.json", self.client.objects)
        self.client.corrupt = None
        self.assertTrue(storage.deliver(self.root / "run-1", self.destination).success)

    def test_restart_aborts_only_managed_incomplete_payload_uploads(self):
        managed = self.key("interrupted")
        foreign = "outside/sets/interrupted/payload/sources/database/database.dump"
        unmanaged_suffix = self.key("interrupted", "operator-note")
        self.client.uploads = {managed: [], foreign: [], unmanaged_suffix: []}
        self.assertTrue(self.deliver().success)
        self.assertNotIn(managed, self.client.uploads)
        self.assertIn(foreign, self.client.uploads)
        self.assertIn(unmanaged_suffix, self.client.uploads)

    def test_same_id_retry_is_idempotent_and_changed_payload_is_rejected(self):
        result = self.deliver()
        original = copy.deepcopy(self.client.objects)
        self.assertEqual(storage.deliver(self.root / "run-1", self.destination), result)
        self.assertEqual(self.client.objects, original)
        shutil_path = self.root / "run-1" / "sources/database/database.dump"
        shutil_path.write_bytes(b"changed")
        manifest = json.loads((self.root / "run-1/manifest.json").read_text())
        artifact = manifest["artifacts"][0]
        artifact.update(bytes=7, sha256=hashlib.sha256(b"changed").hexdigest())
        manifest["sources"][0]["artifacts"] = [artifact]
        (self.root / "run-1/manifest.json").write_text(json.dumps(manifest))
        self.assertFalse(storage.deliver(self.root / "run-1", self.destination).success)
        self.assertEqual(self.client.objects, original)

    def test_partial_upload_identity_reserves_original_payload(self):
        self.client.fail = ("put", self.key())
        self.assertFalse(self.deliver().success)
        self.client.fail = None
        directory = self.root / "run-1"
        manifest = json.loads((directory / "manifest.json").read_text())
        (directory / "sources/database/database.dump").write_bytes(b"different")
        manifest["artifacts"][0].update(bytes=9, sha256=hashlib.sha256(b"different").hexdigest())
        manifest["sources"][0]["artifacts"] = manifest["artifacts"]
        (directory / "manifest.json").write_text(json.dumps(manifest))
        self.assertFalse(storage.deliver(directory, self.destination).success)
        self.assertNotIn(self.key(), self.client.objects)

    def test_delete_failure_is_distinct_from_successful_delivery(self):
        self.assertTrue(self.deliver("old").success)
        self.assertTrue(self.deliver("new").success)
        self.client.objects[self.key("unprotected")] = b"obsolete"
        self.client.fail = ("delete", self.key("unprotected"))
        result = self.deliver("latest")
        self.assertTrue(result.success)
        self.assertEqual(result.cleanup_error["code"], "cleanup_failed")
        self.assertEqual(storage.read_retention(self.destination), result.refs)
        self.assertIn(self.key("old"), self.client.objects)
        self.assertIn(self.key("latest"), self.client.objects)

    def test_prune_only_managed_known_keys_and_rejects_stale_refs(self):
        result = self.deliver()
        for key in ("foreign/object", "managed/v1-other/sets/x/payload/sources/x/data",
                    self.key("obsolete", "operator-file"), self.key("../escape")):
            self.client.objects[key] = b"keep"
        before = copy.deepcopy(self.client.objects)
        self.client.objects[self.key("obsolete")] = b"remove"
        self.assertFalse(storage.prune(self.destination, RetentionRefs()).success)
        self.assertTrue(storage.prune(self.destination, result.refs).success)
        self.assertEqual(self.client.objects, before)

    def test_retry_of_older_committed_set_does_not_roll_latest_backward(self):
        old = self.deliver("old")
        new = self.deliver("new")
        result = storage.deliver(self.root / "old", self.destination)
        self.assertTrue(result.success)
        self.assertEqual(result.refs, new.refs)
        self.assertEqual(result.completed_at, old.completed_at)

    def test_delayed_older_snapshot_is_superseded_without_upload_or_freshness_reset(self):
        old_directory = self.run_directory("delayed")
        latest = self.deliver("new")
        original = copy.deepcopy(self.client.objects)
        self.client.events.clear()
        result = storage.deliver(old_directory, self.destination)
        self.assertTrue(result.success)
        self.assertEqual(result.run_id, latest.run_id)
        self.assertEqual(result.completed_at, latest.completed_at)
        self.assertEqual(result.bytes, latest.bytes)
        self.assertEqual(result.refs, latest.refs)
        self.assertEqual(self.client.objects, original)
        self.assertFalse(any(operation == "put" for operation, _ in self.client.events))

    def test_superseded_retry_requires_valid_latest_commit_before_discarding_payload(self):
        old_directory = self.run_directory("delayed")
        self.assertTrue(self.deliver("new").success)
        original = copy.deepcopy(self.client.objects)
        self.client.corrupt = self.key("new", "manifest.json")
        result = storage.deliver(old_directory, self.destination)
        self.assertFalse(result.success)
        self.assertEqual(self.client.objects, original)
        self.assertTrue((old_directory / "sources/database/database.dump").exists())

    def test_destinations_are_independent(self):
        run = self.run_directory()
        bad = replace(self.destination, id="remote", prefix="remote/v1")
        self.client.fail = ("put", "remote/v1/sets/run-1/intent.json")
        self.assertFalse(storage.deliver(run, bad).success)
        self.assertTrue(storage.deliver(run, self.destination).success)

    def test_download_rejects_traversal_duplicate_invalid_checksum_and_wrong_id(self):
        self.assertTrue(self.deliver().success)
        original = self.client.objects[self.key(suffix="manifest.json")]
        for malformed in ("../outside", "/absolute", "sources/database/../outside", "sources//database/file", "sources/database\\escape"):
            self.change_json(self.key(suffix="manifest.json"), lambda m: (m["artifacts"][0].update(path=malformed), m["sources"][0]["artifacts"][0].update(path=malformed)))
            with self.assertRaises(storage.StorageFailure):
                storage.download(self.destination, "run-1", self.root / "output")
            self.assertFalse((self.root / "output").exists())
            self.client.objects[self.key(suffix="manifest.json")] = original
        for mutation in (lambda m: m.update(run_id="different"),
                         lambda m: m["artifacts"].append(m["artifacts"][0]),
                         lambda m: m.update(complete=False)):
            self.change_json(self.key(suffix="manifest.json"), mutation)
            with self.assertRaises(storage.StorageFailure):
                storage.download(self.destination, "run-1", self.root / "output")
            self.client.objects[self.key(suffix="manifest.json")] = original
        self.client.corrupt = self.key()
        with self.assertRaises(storage.StorageFailure):
            storage.download(self.destination, "run-1", self.root / "output")
        self.assertFalse((self.root / "output").exists())

    def test_download_never_overwrites_existing_or_symlinked_directories(self):
        self.assertTrue(self.deliver().success)
        existing = self.root / "production-data"
        existing.mkdir()
        (existing / "sentinel").write_bytes(b"keep")
        symlink = self.root / "linked"
        symlink.symlink_to(existing, target_is_directory=True)
        for output in (existing, symlink, symlink / "child", self.root / "missing/child"):
            with self.assertRaises(storage.StorageFailure):
                storage.download(self.destination, "run-1", output)
        self.assertEqual((existing / "sentinel").read_bytes(), b"keep")
        self.assertFalse((existing / "child").exists())

    def test_upload_rejects_parent_and_file_symlinks_and_incomplete_manifest(self):
        for index, parent in enumerate((False, True)):
            directory = self.run_directory("unsafe-" + str(index))
            path = directory / "sources/database/database.dump"
            content = self.root / ("external-" + str(index))
            if parent:
                path.unlink()
                path.parent.rmdir()
                content.mkdir()
                (content / "database.dump").write_bytes(b"recovery data")
                path.parent.symlink_to(content, target_is_directory=True)
            else:
                content.write_bytes(path.read_bytes())
                path.unlink()
                path.symlink_to(content)
            self.assertFalse(storage.deliver(directory, self.destination).success)
        self.assertFalse(self.client.objects)

    def test_multipart_is_verified_and_aborted_on_error(self):
        with patch.object(storage, "_CHUNK", 1), patch.object(storage, "_PART", 16):
            self.assertTrue(self.deliver("large", b"x" * 80).success)
            self.assertTrue(any(operation == "part" for operation, _ in self.client.events))
            self.client.fail = ("part", self.key("failed"))
            self.assertFalse(self.deliver("failed", b"x" * 80).success)
            self.assertFalse(self.client.uploads)
            self.assertNotIn(self.key("failed", "manifest.json"), self.client.objects)


if __name__ == "__main__":
    unittest.main()
