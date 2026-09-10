#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "infra/scripts"


def write_executable(path: Path, source: str = "#!/bin/sh\nexit 0\n") -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


class DoctorTest(unittest.TestCase):
    def test_quality_doctor_aggregates_missing_commands(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            result = subprocess.run(
                ["/bin/bash", str(SCRIPTS / "doctor.sh"), "quality"],
                cwd=ROOT,
                env={"PATH": temporary_directory},
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )

        self.assertEqual(1, result.returncode)
        for command in ("bash", "docker", "make", "openssl", "python3", "ssh-keygen"):
            self.assertIn(f"MISSING {command}", result.stdout)

    def test_quality_doctor_accepts_available_commands_and_compose(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            binary_dir = Path(temporary_directory)
            for command in ("bash", "make", "openssl", "python3", "ssh-keygen"):
                write_executable(binary_dir / command)
            write_executable(
                binary_dir / "docker",
                "#!/bin/sh\n"
                "if [ \"$1 $2\" = 'compose version' ]; then printf '2.24.0\\n'; fi\n"
                "exit 0\n",
            )

            result = subprocess.run(
                ["/bin/bash", str(SCRIPTS / "doctor.sh"), "quality"],
                cwd=ROOT,
                env={"PATH": str(binary_dir)},
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("Quality prerequisites are available.", result.stdout)

    def test_runtime_doctor_adds_production_host_requirements(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            binary_dir = Path(temporary_directory)
            for command in ("bash", "make", "openssl", "python3", "ssh-keygen"):
                write_executable(binary_dir / command)
            write_executable(
                binary_dir / "docker",
                "#!/bin/sh\n"
                "if [ \"$1 $2\" = 'compose version' ]; then printf '2.24.0\\n'; fi\n"
                "exit 0\n",
            )

            result = subprocess.run(
                ["/bin/bash", str(SCRIPTS / "doctor.sh"), "runtime"],
                cwd=ROOT,
                env={"PATH": str(binary_dir)},
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )

        self.assertEqual(1, result.returncode)
        for command in ("curl", "flock", "mv", "readlink", "rsync", "sops", "stat", "timeout"):
            self.assertIn(f"MISSING {command}", result.stdout)


class StatusTest(unittest.TestCase):
    def run_status(self, repo_dir: Path) -> subprocess.CompletedProcess[str]:
        binary_dir = repo_dir / "bin"
        binary_dir.mkdir()
        write_executable(
            binary_dir / "docker",
            "#!/bin/sh\nprintf '%s\\n' 'NAME STATUS IMAGE' 'nginx Up nginx:test'\n",
        )
        return subprocess.run(
            [
                "/bin/bash",
                str(SCRIPTS / "status.sh"),
                "--repo-dir",
                str(repo_dir),
            ],
            cwd=ROOT,
            env={"PATH": str(binary_dir)},
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_status_is_read_only_when_no_deployment_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repo_dir = Path(temporary_directory)
            result = self.run_status(repo_dir)

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("Active deployment: none", result.stdout)
            self.assertIn("nginx Up nginx:test", result.stdout)
            self.assertFalse((repo_dir / ".deploy-state").exists())

    def test_status_reports_the_active_slot_and_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repo_dir = Path(temporary_directory)
            state_dir = repo_dir / ".deploy-state"
            state_dir.mkdir()
            marker = state_dir / "active-slot"
            marker.write_text("green release-123-4\n", encoding="utf-8")
            marker.chmod(0o600)

            result = self.run_status(repo_dir)

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("Active slot: green", result.stdout)
        self.assertIn("Active release: release-123-4", result.stdout)


class SopsVerificationTest(unittest.TestCase):
    def prepare_fixture(self, root: Path) -> tuple[Path, Path, Path]:
        repo_dir = root / "repo"
        secrets_dir = repo_dir / "secrets"
        secrets_dir.mkdir(parents=True)
        documents = []
        for name in ("one", "two"):
            relative_path = f"secrets/{name}.sops.yaml"
            (repo_dir / relative_path).write_text("ciphertext\n", encoding="utf-8")
            documents.append({"name": name, "path": relative_path, "secrets": []})
        manifest = repo_dir / "manifest.json"
        manifest.write_text(json.dumps({"documents": documents}), encoding="utf-8")
        age_key = root / "age-key.txt"
        age_key.write_text("AGE-SECRET-KEY-test\n", encoding="utf-8")
        age_key.chmod(0o600)
        log = root / "sops.log"
        fake_sops = root / "sops"
        write_executable(
            fake_sops,
            "#!/bin/sh\n"
            "if [ \"$1\" = '--version' ]; then printf 'sops 3.13.3\\n'; exit 0; fi\n"
            "printf '%s\\n' \"$*\" >>\"$FAKE_SOPS_LOG\"\n"
            "if [ \"$1\" = 'filestatus' ]; then\n"
            "  printf '{\"encrypted\":%s}\\n' \"${FAKE_SOPS_ENCRYPTED:-true}\"\n"
            "else\n"
            "  printf 'PLAINTEXT-MUST-NOT-APPEAR\\n'\n"
            "fi\n",
        )
        return repo_dir, manifest, age_key

    def run_verification(
        self,
        root: Path,
        *,
        encrypted: bool,
    ) -> tuple[subprocess.CompletedProcess[str], Path]:
        repo_dir, manifest, age_key = self.prepare_fixture(root)
        log = root / "sops.log"
        environment = os.environ.copy()
        environment.update(
            {
                "SOPS_INTEGRATION_BINARY": str(root / "sops"),
                "FAKE_SOPS_LOG": str(log),
                "FAKE_SOPS_ENCRYPTED": "true" if encrypted else "false",
            }
        )
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "verify_sops_documents.py"),
                "--manifest",
                str(manifest),
                "--repo-dir",
                str(repo_dir),
                "--cache-dir",
                str(root / "cache"),
                "--age-key-file",
                str(age_key),
            ],
            cwd=ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result, log

    def test_verify_checks_status_and_decrypts_every_manifest_document(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            result, log = self.run_verification(Path(temporary_directory), encrypted=True)
            invocations = log.read_text(encoding="utf-8").splitlines()

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(4, len(invocations))
        self.assertEqual(2, sum(line.startswith("filestatus ") for line in invocations))
        self.assertEqual(2, sum(line.startswith("decrypt ") for line in invocations))
        self.assertNotIn("PLAINTEXT-MUST-NOT-APPEAR", result.stdout + result.stderr)

    def test_verify_rejects_unencrypted_document_before_decryption(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            result, log = self.run_verification(Path(temporary_directory), encrypted=False)
            invocations = log.read_text(encoding="utf-8").splitlines()

        self.assertEqual(1, result.returncode)
        self.assertIn("is not encrypted", result.stderr)
        self.assertFalse(any(line.startswith("decrypt ") for line in invocations))


if __name__ == "__main__":
    unittest.main()
