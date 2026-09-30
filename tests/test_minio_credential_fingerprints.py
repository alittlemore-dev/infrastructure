#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "infra/scripts/minio_credential_fingerprints.py"
SECRET_FILE_VARIABLES = (
    ("MINIO_ROOT_ACCESS_KEY", "COMPOSE_MINIO_ROOT_ACCESS_KEY_FILE"),
    ("MINIO_ROOT_SECRET_KEY", "COMPOSE_MINIO_ROOT_SECRET_KEY_FILE"),
    ("PERSONAL_WORKSPACE_MINIO_ACCESS_KEY", "COMPOSE_PERSONAL_WORKSPACE_MINIO_ACCESS_KEY_FILE"),
    ("PERSONAL_WORKSPACE_MINIO_SECRET_KEY", "COMPOSE_PERSONAL_WORKSPACE_MINIO_SECRET_KEY_FILE"),
    ("COMPETENCY_MINIO_ACCESS_KEY", "COMPOSE_COMPETENCY_MINIO_ACCESS_KEY_FILE"),
    ("COMPETENCY_MINIO_SECRET_KEY", "COMPOSE_COMPETENCY_MINIO_SECRET_KEY_FILE"),
    ("DATABASUS_MINIO_ACCESS_KEY", "COMPOSE_DATABASUS_MINIO_ACCESS_KEY_FILE"),
    ("DATABASUS_MINIO_SECRET_KEY", "COMPOSE_DATABASUS_MINIO_SECRET_KEY_FILE"),
    ("AUTH_API_MINIO_ACCESS_KEY", "COMPOSE_AUTH_API_MINIO_ACCESS_KEY_FILE"),
    ("AUTH_API_MINIO_SECRET_KEY", "COMPOSE_AUTH_API_MINIO_SECRET_KEY_FILE"),
)


class MinioCredentialFingerprintsTest(unittest.TestCase):
    def secret_files(self, root: Path) -> tuple[dict[str, str], dict[str, str]]:
        values = {
            "MINIO_ROOT_ACCESS_KEY": "root-access-key",
            "MINIO_ROOT_SECRET_KEY": "root-secret-value",
            "PERSONAL_WORKSPACE_MINIO_ACCESS_KEY": "personal-access-key",
            "PERSONAL_WORKSPACE_MINIO_SECRET_KEY": "personal-secret-value",
            "COMPETENCY_MINIO_ACCESS_KEY": "competency-access-key",
            "COMPETENCY_MINIO_SECRET_KEY": "competency-secret-value",
            "DATABASUS_MINIO_ACCESS_KEY": "databasus-access-key",
            "DATABASUS_MINIO_SECRET_KEY": "databasus-secret-value",
            "AUTH_API_MINIO_ACCESS_KEY": "auth-api-access-key",
            "AUTH_API_MINIO_SECRET_KEY": "auth-api-secret-value",
        }
        environment = os.environ.copy()
        for secret_name, file_variable in SECRET_FILE_VARIABLES:
            path = root / secret_name.lower()
            path.write_text(values[secret_name], encoding="utf-8")
            environment[file_variable] = str(path)
        return environment, values

    def render(
        self, environment: dict[str, str], *arguments: str
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(SCRIPT), *arguments],
            cwd=ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )

    def test_output_is_stable_and_does_not_expose_secret_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            environment, values = self.secret_files(Path(temporary_directory))
            first = self.render(environment)
            second = self.render(environment)

            self.assertEqual(0, first.returncode, first.stderr)
            self.assertEqual(first.stdout, second.stdout)
            self.assertEqual(
                [name for name, _ in SECRET_FILE_VARIABLES],
                [line.split(" ", maxsplit=1)[0] for line in first.stdout.splitlines()],
            )
            for secret_value in values.values():
                self.assertNotIn(secret_value, first.stdout)

    def test_changing_one_secret_changes_the_fingerprint_document(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            environment, _ = self.secret_files(Path(temporary_directory))
            original = self.render(environment).stdout
            Path(environment["COMPOSE_COMPETENCY_MINIO_SECRET_KEY_FILE"]).write_text(
                "rotated-competency-secret", encoding="utf-8"
            )

            self.assertNotEqual(original, self.render(environment).stdout)

    def test_changing_one_access_key_changes_the_fingerprint_document(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            environment, _ = self.secret_files(Path(temporary_directory))
            original = self.render(environment).stdout
            Path(environment["COMPOSE_PERSONAL_WORKSPACE_MINIO_ACCESS_KEY_FILE"]).write_text(
                "rotated-personal-access-key", encoding="utf-8"
            )

            self.assertNotEqual(original, self.render(environment).stdout)

    def test_legacy_document_contains_only_secret_key_fingerprints(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            environment, _ = self.secret_files(Path(temporary_directory))

            result = self.render(environment, "--legacy-secret-keys-only")

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(
                [
                    "MINIO_ROOT_SECRET_KEY",
                    "PERSONAL_WORKSPACE_MINIO_SECRET_KEY",
                    "COMPETENCY_MINIO_SECRET_KEY",
                    "DATABASUS_MINIO_SECRET_KEY",
                ],
                [line.split(" ", maxsplit=1)[0] for line in result.stdout.splitlines()],
            )

    def test_legacy_marker_is_accepted_for_one_time_upgrade(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            environment, _ = self.secret_files(root)
            marker = root / "minio-credentials.sha256"
            marker.write_text(
                self.render(environment, "--legacy-secret-keys-only").stdout,
                encoding="utf-8",
            )
            marker.chmod(0o600)
            candidate = root / "candidate.sha256"
            legacy_candidate = root / "legacy-candidate.sha256"
            active_slot = root / "active-slot"
            environment["TEST_REPO_DIR"] = str(ROOT)
            environment["TEST_CANDIDATE"] = str(candidate)
            environment["TEST_LEGACY_CANDIDATE"] = str(legacy_candidate)
            environment["TEST_MARKER"] = str(marker)
            environment["TEST_ACTIVE_SLOT"] = str(active_slot)

            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    '. "$TEST_REPO_DIR/infra/scripts/common.sh"; '
                    'repo_dir="$TEST_REPO_DIR"; '
                    '. "$TEST_REPO_DIR/infra/scripts/compose_secrets.sh"; '
                    'verify_minio_credential_fingerprints "$TEST_CANDIDATE" '
                    '"$TEST_LEGACY_CANDIDATE" "$TEST_MARKER" "$TEST_ACTIVE_SLOT"',
                ],
                cwd=ROOT,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("legacy\n", result.stdout)
            self.assertEqual(self.render(environment).stdout, candidate.read_text())

    def verify_marker(
        self, root: Path, environment: dict[str, str], document: str
    ) -> subprocess.CompletedProcess[str]:
        marker = root / "minio-credentials.sha256"
        marker.write_text(document, encoding="utf-8")
        marker.chmod(0o600)
        active_slot = root / "active-slot"
        active_slot.write_text("blue\n", encoding="utf-8")
        environment.update(
            {
                "TEST_REPO_DIR": str(ROOT),
                "TEST_CANDIDATE": str(root / "candidate.sha256"),
                "TEST_LEGACY_CANDIDATE": str(root / "legacy-candidate.sha256"),
                "TEST_MARKER": str(marker),
                "TEST_ACTIVE_SLOT": str(active_slot),
            }
        )
        return subprocess.run(
            [
                "bash",
                "-c",
                '. "$TEST_REPO_DIR/infra/scripts/common.sh"; '
                'repo_dir="$TEST_REPO_DIR"; '
                '. "$TEST_REPO_DIR/infra/scripts/compose_secrets.sh"; '
                'verify_minio_credential_fingerprints "$TEST_CANDIDATE" '
                '"$TEST_LEGACY_CANDIDATE" "$TEST_MARKER" "$TEST_ACTIVE_SLOT"',
            ],
            cwd=ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )

    def marker_before_auth_api(self, environment: dict[str, str]) -> str:
        return "".join(
            f"{line}\n"
            for line in self.render(environment).stdout.splitlines()
            if not line.startswith("AUTH_API_")
        )

    def test_eight_key_marker_accepts_new_auth_api_identity_without_rotation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            environment, values = self.secret_files(root)
            document = self.marker_before_auth_api(environment)

            result = self.verify_marker(root, environment, document)

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("legacy\n", result.stdout)
            self.assertEqual(self.render(environment).stdout, (root / "candidate.sha256").read_text())
            self.assertEqual(document, (root / "minio-credentials.sha256").read_text())
            for value in values.values():
                self.assertNotIn(value, result.stdout + result.stderr)

    def test_eight_key_marker_rejects_rotation_of_every_existing_key(self) -> None:
        for name, variable in SECRET_FILE_VARIABLES:
            if name.startswith("AUTH_API_"):
                continue
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary_directory:
                root = Path(temporary_directory)
                environment, _ = self.secret_files(root)
                document = self.marker_before_auth_api(environment)
                Path(environment[variable]).write_text("rotated-existing-key", encoding="utf-8")

                result = self.verify_marker(root, environment, document)

                self.assertNotEqual(0, result.returncode)
                self.assertIn("credential rotation", result.stderr)
                self.assertEqual(document, (root / "minio-credentials.sha256").read_text())

    def test_current_marker_rejects_rotation_of_auth_api_keys(self) -> None:
        for name, variable in SECRET_FILE_VARIABLES:
            if not name.startswith("AUTH_API_"):
                continue
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary_directory:
                root = Path(temporary_directory)
                environment, _ = self.secret_files(root)
                document = self.render(environment).stdout
                Path(environment[variable]).write_text("rotated-auth-api-key", encoding="utf-8")

                result = self.verify_marker(root, environment, document)

                self.assertNotEqual(0, result.returncode)
                self.assertIn("credential rotation", result.stderr)

    def test_incomplete_or_unrecognized_marker_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            environment, _ = self.secret_files(root)
            document = self.marker_before_auth_api(environment)
            for invalid in (document.splitlines()[0] + "\n", document + "UNKNOWN value\n"):
                with self.subTest(document=invalid):
                    result = self.verify_marker(root, environment, invalid)
                    self.assertNotEqual(0, result.returncode)

    def validate_credentials(
        self, environment: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        environment["TEST_REPO_DIR"] = str(ROOT)
        return subprocess.run(
            [
                "bash",
                "-c",
                '. "$TEST_REPO_DIR/infra/scripts/common.sh"; '
                '. "$TEST_REPO_DIR/infra/scripts/compose_secrets.sh"; '
                'validate_minio_credentials',
            ],
            cwd=ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )

    def test_auth_api_identity_cannot_reuse_an_existing_access_or_secret_key(self) -> None:
        for name, variable in SECRET_FILE_VARIABLES:
            if name.startswith("AUTH_API_"):
                continue
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary_directory:
                environment, values = self.secret_files(Path(temporary_directory))
                auth_variable = (
                    "COMPOSE_AUTH_API_MINIO_ACCESS_KEY_FILE"
                    if name.endswith("_ACCESS_KEY")
                    else "COMPOSE_AUTH_API_MINIO_SECRET_KEY_FILE"
                )
                Path(environment[auth_variable]).write_text(values[name], encoding="utf-8")

                result = self.validate_credentials(environment)

                self.assertNotEqual(0, result.returncode)
                for value in values.values():
                    self.assertNotIn(value, result.stdout + result.stderr)

    def test_auth_api_secret_key_must_have_at_least_eight_characters(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            environment, _ = self.secret_files(Path(temporary_directory))
            Path(environment["COMPOSE_AUTH_API_MINIO_SECRET_KEY_FILE"]).write_text(
                "short", encoding="utf-8"
            )

            result = self.validate_credentials(environment)

            self.assertNotEqual(0, result.returncode)
            self.assertIn("at least eight characters", result.stderr)

    def test_distinct_credentials_for_all_five_identities_are_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            environment, _ = self.secret_files(Path(temporary_directory))

            result = self.validate_credentials(environment)

            self.assertEqual(0, result.returncode, result.stderr)

    def test_missing_secret_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            environment, _ = self.secret_files(Path(temporary_directory))
            del environment["COMPOSE_DATABASUS_MINIO_SECRET_KEY_FILE"]

            result = self.render(environment)

            self.assertNotEqual(0, result.returncode)
            self.assertIn("COMPOSE_DATABASUS_MINIO_SECRET_KEY_FILE", result.stderr)


if __name__ == "__main__":
    unittest.main()
