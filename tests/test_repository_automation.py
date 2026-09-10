#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "infra/scripts"


def write_executable(path: Path, source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


def copy_script(source_name: str, repo_dir: Path) -> Path:
    destination = repo_dir / "infra/scripts" / source_name
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SCRIPTS / source_name, destination)
    return destination


class RepositoryAutomationTest(unittest.TestCase):
    def test_validation_discovers_json_and_does_not_run_tests(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repo_dir = Path(temporary_directory)
            script = copy_script("check.sh", repo_dir)
            (repo_dir / "infra/scripts/example.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            for relative_path in (
                "infra/minio/policies/personal-workspace.json",
                "infra/minio/policies/competency-trainer.json",
                "infra/minio/policies/databasus.json",
                "infra/deploy/runtime-secrets.manifest.json",
                "infra/new-component/contract.json",
            ):
                path = repo_dir / relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}\n", encoding="utf-8")
            log = repo_dir / "commands.log"
            binary_dir = repo_dir / "bin"
            write_executable(
                binary_dir / "python3",
                "#!/bin/sh\nprintf '%s\\n' \"$*\" >>\"$AUTOMATION_LOG\"\n",
            )
            write_executable(
                binary_dir / "docker",
                "#!/bin/sh\nprintf 'docker %s\\n' \"$*\" >>\"$AUTOMATION_LOG\"\n",
            )
            environment = os.environ.copy()
            environment["PATH"] = f"{binary_dir}:{environment['PATH']}"
            environment["AUTOMATION_LOG"] = str(log)

            result = subprocess.run(
                ["bash", str(script)],
                cwd=repo_dir,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            commands = log.read_text(encoding="utf-8")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("infra/new-component/contract.json", commands)
        self.assertIn("infra/scripts/list_compose_build_images.py", commands)
        self.assertNotIn("-m unittest", commands)

    def test_lint_discovers_every_dockerfile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repo_dir = Path(temporary_directory)
            script = copy_script("docker_lint.sh", repo_dir)
            for relative_path in (
                "infra/cert-sync/Dockerfile",
                "infra/minio/Dockerfile",
                "infra/nginx/Dockerfile",
                "infra/new-component/Dockerfile",
            ):
                path = repo_dir / relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("FROM scratch\n", encoding="utf-8")
            (repo_dir / "infra/scripts/example.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            log = repo_dir / "docker.log"
            binary_dir = repo_dir / "bin"
            write_executable(
                binary_dir / "docker",
                "#!/bin/sh\nprintf '%s\\n' \"$*\" >>\"$AUTOMATION_LOG\"\n",
            )
            environment = os.environ.copy()
            environment["PATH"] = f"{binary_dir}:{environment['PATH']}"
            environment["AUTOMATION_LOG"] = str(log)

            result = subprocess.run(
                ["bash", str(script)],
                cwd=repo_dir,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            invocations = log.read_text(encoding="utf-8")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("infra/new-component/Dockerfile", invocations)
        self.assertRegex(invocations, r"hadolint/hadolint:v2\.14\.0@sha256:[0-9a-f]{64}")
        self.assertRegex(invocations, r"koalaman/shellcheck:v0\.11\.0@sha256:[0-9a-f]{64}")

    def test_image_scan_uses_unique_effective_compose_images(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repo_dir = Path(temporary_directory)
            script = copy_script("trivy_scan.sh", repo_dir)
            shutil.copy2(
                SCRIPTS / "list_compose_build_images.py",
                repo_dir / "infra/scripts/list_compose_build_images.py",
            )
            (repo_dir / "infra/scripts/common.sh").write_text(
                "load_environment() { export IMAGE_REGISTRY=registry.example; }\n",
                encoding="utf-8",
            )
            (repo_dir / "infra/scripts/compose_secrets.sh").write_text(
                "prepare_compose_secret_files() { :; }\n",
                encoding="utf-8",
            )
            log = repo_dir / "docker.log"
            binary_dir = repo_dir / "bin"
            write_executable(
                binary_dir / "docker",
                "#!/bin/sh\n"
                "printf '%s\\n' \"$*\" >>\"$AUTOMATION_LOG\"\n"
                "if [ \"$1 $2 $3\" = 'compose config --images' ]; then\n"
                "  printf '%s\\n' 'custom/local-wrapper:test' 'registry.example/app:latest' "
                "'postgres:18.4-alpine' 'registry.example/app:latest'\n"
                "elif [ \"$1 $2 $3 $4\" = 'compose config --format json' ]; then\n"
                "  printf '%s\\n' "
                "'{\"services\":{\"wrapper\":{\"build\":{\"context\":\".\"},\"image\":\"custom/local-wrapper:test\"},\"app\":{\"image\":\"registry.example/app:latest\"}}}'\n"
                "fi\n",
            )
            environment = os.environ.copy()
            environment["PATH"] = f"{binary_dir}:{environment['PATH']}"
            environment["AUTOMATION_LOG"] = str(log)

            result = subprocess.run(
                ["bash", str(script), "images", "trivy:test"],
                cwd=repo_dir,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            invocations = log.read_text(encoding="utf-8").splitlines()

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("compose build", invocations)
        self.assertNotIn("compose build minio nginx cert-sync", invocations)
        self.assertIn("compose config --images", invocations)
        self.assertEqual(1, invocations.count("pull registry.example/app:latest"))
        self.assertEqual(1, invocations.count("pull postgres:18.4-alpine"))
        self.assertNotIn("pull custom/local-wrapper:test", invocations)
        for image in (
            "custom/local-wrapper:test",
            "registry.example/app:latest",
            "postgres:18.4-alpine",
        ):
            self.assertEqual(
                1,
                sum(command.startswith("run ") and command.endswith(image) for command in invocations),
                image,
            )

    def test_compose_secret_variables_are_loaded_from_the_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repo_dir = Path(temporary_directory)
            script = copy_script("compose_secrets.sh", repo_dir)
            shutil.copy2(
                SCRIPTS / "list_compose_secret_variables.py",
                repo_dir / "infra/scripts/list_compose_secret_variables.py",
            )
            manifest = repo_dir / "infra/deploy/runtime-secrets.manifest.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(
                json.dumps(
                    {
                        "documents": [
                            {
                                "secrets": [
                                    {"composeVariable": "ALPHA_FILE"},
                                    {"composeVariable": "BETA_FILE"},
                                ]
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    'repo_dir="$1"; . "$2"; load_compose_secret_file_variables; '
                    'printf "%s\\n" "${COMPOSE_SECRET_FILE_VARIABLES[@]}"',
                    "automation-test",
                    str(repo_dir),
                    str(script),
                ],
                cwd=repo_dir,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(["ALPHA_FILE", "BETA_FILE"], result.stdout.splitlines())


if __name__ == "__main__":
    unittest.main()
