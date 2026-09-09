#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "infra/scripts/compose_secrets.sh"


class ComposeSecretGenerationTest(unittest.TestCase):
    def run_cleanup(
        self,
        candidate: Path,
        pointer: Path,
        temporary_pointer: Path,
        expected_target: str,
        temporary_files: list[Path],
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment.update(
            {
                "TEST_SCRIPT": str(SCRIPT),
                "TEST_CANDIDATE": str(candidate),
                "TEST_POINTER": str(pointer),
                "TEST_TEMP_POINTER": str(temporary_pointer),
                "TEST_EXPECTED_TARGET": expected_target,
                "TEST_TEMP_FILES": " ".join(str(path) for path in temporary_files),
            }
        )
        return subprocess.run(
            [
                "bash",
                "-c",
                '. "$TEST_SCRIPT"; cleanup_compose_secret_transaction '
                '"$TEST_CANDIDATE" "$TEST_POINTER" "$TEST_TEMP_POINTER" '
                '"$TEST_EXPECTED_TARGET" $TEST_TEMP_FILES',
            ],
            cwd=ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )

    def test_interrupt_after_atomic_pointer_switch_keeps_published_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            state = Path(temporary_directory)
            generations = state / ".compose-secret-generations"
            candidate = generations / "blue-ABC123"
            candidate.mkdir(parents=True)
            (candidate / "secret").write_text("secret", encoding="utf-8")
            expected_target = ".compose-secret-generations/blue-ABC123"
            pointer = state / "compose-secrets-blue"
            pointer.symlink_to(expected_target)
            temporary_pointer = state / ".compose-secrets-blue.123"
            temporary_pointer.symlink_to(expected_target)
            temporary_files = [state / name for name in ("env", "current", "legacy")]
            for path in temporary_files:
                path.write_text("temporary", encoding="utf-8")

            result = self.run_cleanup(
                candidate,
                pointer,
                temporary_pointer,
                expected_target,
                temporary_files,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertTrue(candidate.is_dir())
            self.assertTrue(pointer.is_symlink())
            self.assertFalse(temporary_pointer.exists())
            self.assertFalse(any(path.exists() for path in temporary_files))

    def test_interrupt_before_pointer_switch_removes_only_transaction_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            state = Path(temporary_directory)
            generations = state / ".compose-secret-generations"
            candidate = generations / "green-ABC123"
            candidate.mkdir(parents=True)
            expected_target = ".compose-secret-generations/green-ABC123"
            pointer = state / "compose-secrets-green"
            unrelated_target = ".compose-secret-generations/green-OLD123"
            (generations / "green-OLD123").mkdir()
            pointer.symlink_to(unrelated_target)
            temporary_pointer = state / ".compose-secrets-green.123"
            temporary_pointer.symlink_to(expected_target)
            temporary_files = [state / name for name in ("env", "current", "legacy")]
            for path in temporary_files:
                path.write_text("temporary", encoding="utf-8")

            result = self.run_cleanup(
                candidate,
                pointer,
                temporary_pointer,
                expected_target,
                temporary_files,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertFalse(candidate.exists())
            self.assertEqual(unrelated_target, os.readlink(pointer))
            self.assertFalse(temporary_pointer.exists())
            self.assertFalse(any(path.exists() for path in temporary_files))


if __name__ == "__main__":
    unittest.main()
