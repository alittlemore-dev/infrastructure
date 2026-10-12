#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "infra/backup/src"))
sys.path.insert(0, str(ROOT / "infra/scripts"))

from infra_backup.config import BackupConfigError, load_config  # noqa: E402


class BackupConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.secret = self.directory / "credential"
        self.secret.write_bytes(os.urandom(32).hex().encode())
        self.secret.chmod(0o600)
        self.sources_path = self.directory / "sources.json"
        self.destinations_path = self.directory / "destinations.json"
        self.sources = {
            "sources": [
                {"id": "application-db", "type": "postgres", "host": "application-db", "database_env": "TEST_DB_NAME", "user_env": "TEST_DB_USER", "password_file": str(self.secret)},
                {"id": "cache", "type": "valkey", "host": "application-cache"},
                {"id": "files", "type": "s3", "endpoint": "http://object-store:9000", "internal": True, "region_env": "TEST_REGION", "bucket": "application-files", "access_key_file": str(self.secret), "secret_key_file": str(self.secret)},
                {"id": "config", "type": "config", "paths": ["config/application/production.env", "secrets/application/production.sops.yaml", "docker-compose.yml"]},
            ]
        }
        self.destinations = {
            "destinations": [{"id": "internal", "endpoint": "http://object-store:9000", "internal": True, "region_env": "TEST_REGION", "bucket": "system-backups", "prefix": "system/v1", "access_key_file": str(self.secret), "secret_key_file": str(self.secret), "required": True}]
        }
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {"TEST_DB_NAME": "application", "TEST_DB_USER": "backup_reader", "TEST_REGION": "region-one"}, clear=True).start()

    def load(self):
        self.sources_path.write_text(json.dumps(self.sources), encoding="utf-8")
        self.destinations_path.write_text(json.dumps(self.destinations), encoding="utf-8")
        return load_config(self.sources_path, self.destinations_path)

    def test_loads_immutable_config_with_resolved_environment_and_secret_references(self) -> None:
        config = self.load()
        self.assertEqual(config.interval_seconds, 21600)
        self.assertEqual(config.freshness_threshold_seconds, 25200)
        self.assertEqual(config.sources[0].database, "application")
        self.assertEqual(config.sources[0].user, "backup_reader")
        self.assertEqual(config.sources[0].password_file, self.secret)
        self.assertEqual(config.sources[1].port, 6379)
        self.assertEqual(config.destinations[0].region, "region-one")
        self.assertEqual(config.recovery_root, Path("/recovery"))
        self.assertIsInstance(config.sources, tuple)
        with self.assertRaises(FrozenInstanceError):
            config.interval_seconds = 1
        self.assertNotIn(self.secret.read_text(), repr(config))

    def test_loads_two_independent_destinations(self) -> None:
        external = copy.deepcopy(self.destinations["destinations"][0])
        external.update(id="secondary", endpoint="https://archive.example.net", bucket="other-backups", prefix="other/v2", internal=False, required=False)
        self.destinations["destinations"].append(external)
        config = self.load()
        self.assertEqual(len(config.destinations), 2)
        self.assertEqual(config.destinations[1].id, "secondary")
        self.assertFalse(config.destinations[1].required)
        self.assertNotEqual(config.destinations[0].prefix, config.destinations[1].prefix)

    def test_rejects_unknown_source_type_and_duplicate_identifiers(self) -> None:
        with self.subTest("unknown source"):
            self.sources["sources"][0]["type"] = "filesystem"
            with self.assertRaises(BackupConfigError):
                self.load()
        self.sources["sources"][0]["type"] = "postgres"
        with self.subTest("source duplicate"):
            self.sources["sources"][1]["id"] = "application-db"
            with self.assertRaises(BackupConfigError):
                self.load()
        self.sources["sources"][1]["id"] = "cache"
        with self.subTest("destination duplicate"):
            self.destinations["destinations"].append(copy.deepcopy(self.destinations["destinations"][0]))
            with self.assertRaises(BackupConfigError):
                self.load()

    def test_rejects_every_destination_bucket_as_source(self) -> None:
        destination = copy.deepcopy(self.destinations["destinations"][0])
        destination.update(id="second", bucket="application-files", required=False)
        self.destinations["destinations"].append(destination)
        with self.assertRaises(BackupConfigError):
            self.load()

    def test_rejects_empty_missing_directory_and_symlink_secrets(self) -> None:
        for mode in ("empty", "missing", "directory", "symlink"):
            with self.subTest(mode=mode):
                path = self.directory / mode
                if mode == "empty":
                    path.write_text(" \n", encoding="utf-8")
                elif mode == "directory":
                    path.mkdir()
                elif mode == "symlink":
                    path.symlink_to(self.secret)
                self.sources["sources"][0]["password_file"] = str(path)
                with self.assertRaises(BackupConfigError):
                    self.load()

    def test_rejects_non_tls_external_endpoints_and_credential_urls_without_leaking(self) -> None:
        for endpoint in ("http://public.example.net", "http://127.0.0.1:9000", "https://hidden-user:hidden-password@example.net", "https://example.net?token=hidden-token", "https://example.net/#hidden-token", "https://example.net:invalid"):
            with self.subTest(endpoint=endpoint):
                self.destinations["destinations"][0]["endpoint"] = endpoint
                with self.assertRaises(BackupConfigError) as caught:
                    self.load()
                self.assertNotIn(endpoint, str(caught.exception))
                self.assertNotIn("hidden-", str(caught.exception))

    def test_http_internal_origin_requires_explicit_flag(self) -> None:
        self.destinations["destinations"][0].pop("internal")
        with self.assertRaises(BackupConfigError):
            self.load()

    def test_rejects_prefix_traversal_and_requires_required_destination(self) -> None:
        for prefix in ("", ".", "../outside", "system/../outside", "/system", "system//v1", "system/./v1", "system\\v1", "system/%2e%2e"):
            with self.subTest(prefix=prefix):
                self.destinations["destinations"][0]["prefix"] = prefix
                with self.assertRaises(BackupConfigError):
                    self.load()
        self.destinations["destinations"][0]["prefix"] = "system/v1"
        self.destinations["destinations"][0]["required"] = False
        with self.assertRaises(BackupConfigError):
            self.load()

    def test_validates_timing_budget_port_and_boolean_ranges(self) -> None:
        for field, value in (("interval_seconds", 0), ("interval_seconds", True), ("interval_seconds", 604801), ("freshness_threshold_seconds", 60), ("pending_budget_bytes", 0)):
            with self.subTest(field=field, value=value):
                self.sources[field] = value
                with self.assertRaises(BackupConfigError):
                    self.load()
                self.sources.pop(field)
        for field, value in (("timeout_seconds", 0), ("port", 65536)):
            with self.subTest(field=field):
                self.sources["sources"][0][field] = value
                with self.assertRaises(BackupConfigError):
                    self.load()
                self.sources["sources"][0].pop(field)
        self.destinations["destinations"][0]["required"] = "true"
        with self.assertRaises(BackupConfigError):
            self.load()

    def test_recovery_allowlist_rejects_secret_material_and_checkout_state(self) -> None:
        for path in (".", "../outside", ".deploy-state/runtime.env", ".git/config", "secrets/application/plaintext.env", "certs/privkey.pem", "/etc/private-age-key", "infra/minio-mc/config.json", "config/.private/env"):
            with self.subTest(path=path):
                self.sources["sources"][3]["paths"] = [path]
                with self.assertRaises(BackupConfigError):
                    self.load()

    def test_errors_never_echo_secret_values_or_malformed_json(self) -> None:
        self.destinations["destinations"][0]["secret_key"] = self.secret.read_text()
        with self.assertRaises(BackupConfigError) as caught:
            self.load()
        self.assertNotIn(self.secret.read_text(), str(caught.exception))
        self.sources_path.write_text('{"sources": "private-value", invalid}', encoding="utf-8")
        with self.assertRaises(BackupConfigError) as caught:
            load_config(self.sources_path, self.destinations_path)
        self.assertNotIn("private-value", str(caught.exception))

    def test_duplicate_json_fields_and_missing_environment_fail_closed(self) -> None:
        self.load()
        self.sources_path.write_text('{"sources": [], "sources": []}', encoding="utf-8")
        with self.assertRaises(BackupConfigError):
            load_config(self.sources_path, self.destinations_path)
        os.environ.pop("TEST_DB_NAME")
        with self.assertRaises(BackupConfigError):
            self.load()


if __name__ == "__main__":
    unittest.main()
