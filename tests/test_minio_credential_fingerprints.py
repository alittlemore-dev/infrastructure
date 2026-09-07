#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "infra/scripts/minio_credential_fingerprints.py"
SECRET_NAMES = (
    "MINIO_ROOT_SECRET_KEY",
    "PERSONAL_WORKSPACE_MINIO_SECRET_KEY",
    "COMPETENCY_MINIO_SECRET_KEY",
    "DATABASUS_MINIO_SECRET_KEY",
)


class MinioCredentialFingerprintsTest(unittest.TestCase):
    def environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        environment.update(
            {
                "MINIO_ROOT_SECRET_KEY": "root-secret-value",
                "PERSONAL_WORKSPACE_MINIO_SECRET_KEY": "personal-secret-value",
                "COMPETENCY_MINIO_SECRET_KEY": "competency-secret-value",
                "DATABASUS_MINIO_SECRET_KEY": "databasus-secret-value",
            }
        )
        return environment

    def render(self, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(SCRIPT)],
            cwd=ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )

    def test_output_is_stable_and_does_not_expose_secret_values(self) -> None:
        environment = self.environment()
        first = self.render(environment)
        second = self.render(environment)

        self.assertEqual(0, first.returncode, first.stderr)
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(
            list(SECRET_NAMES),
            [line.split(" ", maxsplit=1)[0] for line in first.stdout.splitlines()],
        )
        for secret_value in (environment[name] for name in SECRET_NAMES):
            self.assertNotIn(secret_value, first.stdout)

    def test_changing_one_secret_changes_the_fingerprint_document(self) -> None:
        original_environment = self.environment()
        changed_environment = self.environment()
        changed_environment["COMPETENCY_MINIO_SECRET_KEY"] = "rotated-competency-secret"

        self.assertNotEqual(
            self.render(original_environment).stdout,
            self.render(changed_environment).stdout,
        )

    def test_missing_secret_is_rejected(self) -> None:
        environment = self.environment()
        del environment["DATABASUS_MINIO_SECRET_KEY"]

        result = self.render(environment)

        self.assertNotEqual(0, result.returncode)
        self.assertIn("DATABASUS_MINIO_SECRET_KEY", result.stderr)


if __name__ == "__main__":
    unittest.main()
