"""Recovery fixtures and safe source failures, without SDK/runtime dependencies."""
from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "infra/backup/src"))
sys.path.insert(0, str(ROOT / "infra/scripts"))
from infra_backup.config import BackupConfig, DestinationConfig, SourceConfig  # noqa: E402
from infra_backup.sources import collect_sources  # noqa: E402


class ObjectStore:
    def __init__(self, objects=None, versioned=False):
        self.objects = objects or {}
        self.versioned = versioned
        self.calls = []
        self.closed = False

    def head_bucket(self, **arguments):
        self.calls.append(("bucket", arguments))

    def list_objects_v2(self, **arguments):
        self.calls.append(("list", arguments))
        keys = sorted(self.objects)
        offset = int(arguments.get("ContinuationToken", 0))
        contents = [{"Key": key, "Size": len(self.objects[key]), "ETag": '"' + hashlib.md5(self.objects[key]).hexdigest() + '"'} for key in keys[offset:offset + 1]]
        truncated = offset + 1 < len(keys)
        return {"Contents": contents, "IsTruncated": truncated, **({"NextContinuationToken": str(offset + 1)} if truncated else {})}

    def head_object(self, **arguments):
        data = self.objects[arguments["Key"]]
        return {"ContentLength": len(data), "ETag": '"' + hashlib.md5(data).hexdigest() + '"', "ContentType": "text/plain", "Metadata": {"original": "fixture"}, **({"VersionId": "selected"} if self.versioned else {})}

    def get_object(self, **arguments):
        self.calls.append(("get", arguments))
        return {**self.head_object(**arguments), "Body": io.BytesIO(self.objects[arguments["Key"]])}

    def close(self):
        self.closed = True


class BackupSourcesTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.recovery = self.directory / "recovery"
        self.recovery.mkdir()
        self.run = self.directory / "run"
        self.secret = self.directory / "secret"
        self.secret.write_text("fixture-password:with\\escape\n")
        self.secret.chmod(0o600)

    def config(self, *sources, destinations=()):
        return BackupConfig(sources=tuple(sources), destinations=destinations, recovery_root=self.recovery)

    def s3(self):
        return SourceConfig(id="files", type="s3", endpoint="http://store", region="fixture", bucket="fixture-files", access_key_file=self.secret, secret_key_file=self.secret)

    def postgres(self):
        return SourceConfig(id="database", type="postgres", host="database", port=5432, database="fixture-db", user="fixture-user", password_file=self.secret)

    def create_config(self, relative, content):
        target = self.recovery / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return target

    def command(self, arguments, **kwargs):
        output = b""
        if "--version" in arguments:
            output = f"{arguments[0]} (PostgreSQL) 18.6\n".encode()
        elif arguments[0] == "psql":
            output = b"18.6\n"
        if arguments[0] in {"pg_dump", "pg_dumpall"} and "--version" not in arguments:
            Path(arguments[arguments.index("--file") + 1]).write_bytes(b"PGDMP fixture" if arguments[0] == "pg_dump" else b"CREATE ROLE fixture;\n")
        if arguments[0] == "valkey-cli":
            Path(arguments[-1]).write_bytes(b"REDIS0012fixture\xff" + b"\x00" * 8)
        return subprocess.CompletedProcess(arguments, 0, output, b"")

    def test_postgres_custom_dump_globals_versions_private_pgpass_and_checksums(self):
        calls, passwords = [], []
        def command(arguments, **kwargs):
            calls.append(arguments)
            self.assertGreater(kwargs["timeout"], 0)
            self.assertNotIn(self.secret.read_text().strip(), " ".join(arguments))
            self.assertNotIn("PGPASSWORD", kwargs["env"])
            if "PGPASSFILE" in kwargs["env"]:
                pgpass = Path(kwargs["env"]["PGPASSFILE"])
                self.assertEqual(pgpass.stat().st_mode & 0o777, 0o600)
                passwords.append(pgpass.read_text())
            return self.command(arguments, **kwargs)
        with patch.dict(os.environ, {"PGPASSWORD": "must-not-inherit"}), patch("infra_backup.sources.subprocess.run", side_effect=command):
            result = collect_sources(self.config(self.postgres()), self.run)
        self.assertTrue(result.complete)
        self.assertEqual(len(result.artifacts), 3)
        dump = next(args for args in calls if args[0] == "pg_dump" and "--file" in args)
        self.assertIn("custom", dump)
        self.assertIn("--lock-wait-timeout=5000", dump)
        globals_call = next(args for args in calls if args[0] == "pg_dumpall" and "--file" in args)
        self.assertIn("--no-role-passwords", globals_call)
        self.assertIn("--no-password", globals_call)
        self.assertEqual(passwords[0], "database:5432:fixture-db:fixture-user:fixture-password\\:with\\\\escape\n")
        self.assertFalse(list(self.run.rglob("pgpass")))
        self.assertEqual(json.loads((self.run / "sources/database/postgres.json").read_text())["server_version"], "18.6")
        for artifact in result.artifacts:
            data = (self.run / artifact["path"]).read_bytes()
            self.assertEqual(artifact["bytes"], len(data))
            self.assertEqual(artifact["sha256"], hashlib.sha256(data).hexdigest())

    def test_concurrent_ddl_retries_once_and_other_errors_do_not_retry(self):
        for stderr, count, success in ((b"lock timeout", 2, True), (b"permission denied private-password", 1, False), (b"lock timeout", 2, False)):
            with self.subTest(stderr=stderr, success=success):
                calls = []
                def command(arguments, **kwargs):
                    if arguments[0] == "pg_dump" and "--file" in arguments:
                        calls.append(arguments)
                        if len(calls) == 1 or not success:
                            return subprocess.CompletedProcess(arguments, 1, b"", stderr)
                    return self.command(arguments, **kwargs)
                with patch("infra_backup.sources.subprocess.run", side_effect=command):
                    result = collect_sources(self.config(self.postgres()), self.run / f"{count}-{success}")
                self.assertEqual(result.complete, success)
                self.assertEqual(len(calls), count)
                self.assertNotIn("private-password", repr(result))

    def test_timeout_and_database_failure_do_not_stop_other_sources(self):
        self.create_config("config/app/production.env", b"SETTING=enabled\n")
        source = SourceConfig(id="config", type="config", paths=("config/app/production.env",))
        with patch("infra_backup.sources.subprocess.run", side_effect=subprocess.TimeoutExpired(["private-endpoint"], 1, stderr="private-password")):
            result = collect_sources(self.config(self.postgres(), source), self.run)
        self.assertFalse(result.complete)
        self.assertEqual([entry["status"] for entry in result.sources], ["failed", "success"])
        self.assertEqual(result.sources[0]["error"]["code"], "source_failed")
        self.assertNotIn("private-", repr(result))
        self.assertEqual(len(result.artifacts), 1)

    def test_wrong_postgres_major_fails_closed(self):
        with patch("infra_backup.sources.subprocess.run", return_value=subprocess.CompletedProcess([], 0, b"pg_dump (PostgreSQL) 17.9\n", b"")):
            self.assertFalse(collect_sources(self.config(self.postgres()), self.run).complete)

    def test_s3_pagination_unusual_keys_metadata_and_version_selection(self):
        for versioned in (False, True):
            store = ObjectStore({"../../escape": b"one", "/absolute/файл.txt": b"two", "normal": b""}, versioned)
            with patch("infra_backup.sources._s3_client", return_value=store):
                result = collect_sources(self.config(self.s3()), self.run / str(versioned))
            self.assertTrue(result.complete)
            self.assertTrue(store.closed)
            index = json.loads((self.run / str(versioned) / "sources/files/objects.json").read_text())
            self.assertEqual({entry["key"] for entry in index["objects"]}, set(store.objects))
            for entry in index["objects"]:
                self.assertRegex(entry["path"], r"^[0-9a-f]{64}\.object$")
                self.assertEqual((self.run / str(versioned) / "sources/files" / entry["path"]).read_bytes(), store.objects[entry["key"]])
                self.assertEqual(entry["metadata"], {"original": "fixture"})
                self.assertEqual(entry["content_type"], "text/plain")
            self.assertTrue(any("ContinuationToken" in args for kind, args in store.calls if kind == "list"))
            gets = [args for kind, args in store.calls if kind == "get"]
            self.assertTrue(all("IfMatch" in args for args in gets))
            if versioned:
                self.assertTrue(all(args["VersionId"] == "selected" for args in gets))
            self.assertFalse((self.directory / "escape").exists())

    def test_empty_existing_bucket_is_complete(self):
        store = ObjectStore()
        with patch("infra_backup.sources._s3_client", return_value=store):
            result = collect_sources(self.config(self.s3()), self.run)
        self.assertTrue(result.complete)
        self.assertEqual(len(result.artifacts), 1)
        self.assertEqual(json.loads((self.run / "sources/files/objects.json").read_text())["objects"], [])
        self.assertEqual(store.calls[0][0], "bucket")

    def test_deleted_changed_or_truncated_object_fails_safely(self):
        for mode in ("deleted", "changed", "truncated"):
            store = ObjectStore({"private-user-key": b"fixture"})
            original = store.get_object
            def get(**arguments):
                if mode == "deleted":
                    raise RuntimeError("private-user-key private-credential")
                result = original(**arguments)
                if mode == "changed":
                    store.objects[arguments["Key"]] = b"changed"
                else:
                    result["Body"] = io.BytesIO(b"short")
                return result
            store.get_object = get
            with patch("infra_backup.sources._s3_client", return_value=store):
                result = collect_sources(self.config(self.s3()), self.run / mode)
            self.assertFalse(result.complete)
            self.assertNotIn("private-", repr(result))
            self.assertTrue(store.closed)

    def test_destination_buckets_are_never_read(self):
        destination = DestinationConfig(id="internal", endpoint="http://store", region="fixture", bucket="fixture-files", prefix="system/v1", access_key_file=self.secret, secret_key_file=self.secret, required=True)
        with patch("infra_backup.sources._s3_client") as client:
            self.assertFalse(collect_sources(self.config(self.s3(), destinations=(destination,)), self.run).complete)
            client.assert_not_called()

    def test_valkey_checks_structure_and_format(self):
        source = SourceConfig(id="cache", type="valkey", host="cache", port=6379)
        calls = []
        def command(arguments, **kwargs):
            calls.append(arguments)
            return self.command(arguments, **kwargs)
        with patch("infra_backup.sources.subprocess.run", side_effect=command):
            self.assertTrue(collect_sources(self.config(source), self.run).complete)
        self.assertEqual(calls[-1][0], "valkey-check-rdb")
        def invalid(arguments, **kwargs):
            result = self.command(arguments, **kwargs)
            if arguments[0] == "valkey-cli":
                Path(arguments[-1]).write_bytes(b"not-an-rdb-file")
            return result
        with patch("infra_backup.sources.subprocess.run", side_effect=invalid):
            self.assertFalse(collect_sources(self.config(source), self.run / "invalid").complete)

    def test_config_allowlist_ciphertext_and_injected_deployment_metadata(self):
        encrypted = b"DB_PASSWORD: ENC[AES256_GCM,data:fixture,iv:fixture,tag:fixture,type:str]\nsops:\n    mac: ENC[AES256_GCM,data:fixture]\n"
        self.create_config("secrets/app/production.sops.yaml", encrypted)
        self.create_config("config/app/production.env", b"SETTING=fixture\n")
        self.create_config("config/app/unlisted.env", b"unlisted-private-value")
        metadata_path = self.directory / "release.json"
        metadata = {"infra_release": "a" * 40, "application_images": {"app": "registry.example/app@sha256:" + "b" * 64}}
        metadata_path.write_text(json.dumps(metadata))
        source = SourceConfig(id="recovery", type="config", paths=("secrets/app/production.sops.yaml", "config/app/production.env"))
        result = collect_sources(self.config(source), self.run, deployment_metadata_path=metadata_path)
        self.assertTrue(result.complete)
        self.assertEqual((self.run / "sources/recovery/secrets/app/production.sops.yaml").read_bytes(), encrypted)
        self.assertFalse((self.run / "sources/recovery/config/app/unlisted.env").exists())
        self.assertEqual(json.loads((self.run / "sources/recovery/deployment.json").read_text()), metadata)
        self.assertEqual(len(result.artifacts), 3)

    def test_plaintext_and_mixed_sops_are_rejected(self):
        for content in (b"DB_PASSWORD: private-value\n", b"DB_PASSWORD: private-value\nsops:\n    mac: ENC[AES256_GCM,data:fixture]\n"):
            self.create_config("secrets/app/production.sops.yaml", content)
            source = SourceConfig(id="recovery", type="config", paths=("secrets/app/production.sops.yaml",))
            result = collect_sources(self.config(source), self.run / str(len(content)))
            self.assertFalse(result.complete)
            self.assertNotIn("private-value", repr(result))
            self.assertFalse(list((self.run / str(len(content))).rglob("*.yaml")))

    def test_recovery_parent_symlinks_and_nonallowlisted_files_are_rejected(self):
        outside = self.directory / "outside"
        outside.mkdir()
        (outside / "production.env").write_text("private-value")
        (self.recovery / "config").mkdir()
        (self.recovery / "config/app").symlink_to(outside, target_is_directory=True)
        source = SourceConfig(id="recovery", type="config", paths=("config/app/production.env",))
        self.assertFalse(collect_sources(self.config(source), self.run).complete)
        source = SourceConfig(id="recovery", type="config", paths=(".deploy-state/runtime.env",))
        self.assertFalse(collect_sources(self.config(source), self.run / "forbidden").complete)


if __name__ == "__main__":
    unittest.main()
