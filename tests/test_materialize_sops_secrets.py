#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import shutil
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "infra/scripts/materialize_sops_secrets.py"


class MaterializeSopsSecretsTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("openssl"), "openssl is required")
    def test_pem_encoding_converts_literal_newline_escapes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            sandbox = Path(temporary_directory)
            root = sandbox / "repo"
            document = root / "secrets/competency-trainer/production.sops.yaml"
            document.parent.mkdir(parents=True)
            document.write_text("encrypted", encoding="utf-8")
            key_file = sandbox / "age-key.txt"
            key_file.write_text("AGE-SECRET-KEY-test\n", encoding="utf-8")
            key_file.chmod(0o600)
            private_key = sandbox / "private.pem"
            subprocess.run(
                ["openssl", "genrsa", "-out", str(private_key), "1024"],
                check=True,
                capture_output=True,
                timeout=5,
            )
            escaped_key = private_key.read_text(encoding="utf-8").replace("\n", "\\n")
            fake_sops = root / "fake-sops"
            fake_sops.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os\n"
                "print(json.dumps({'AUTH_PRIVATE_KEY': os.environ['TEST_ESCAPED_PEM']}))\n",
                encoding="utf-8",
            )
            fake_sops.chmod(0o755)
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "documents": [
                            {
                                "name": "competency-trainer",
                                "path": "secrets/competency-trainer/production.sops.yaml",
                                "secrets": [
                                    {
                                        "name": "AUTH_PRIVATE_KEY",
                                        "target": "competency-trainer/auth_private_key",
                                        "composeVariable": "COMPOSE_COMPETENCY_AUTH_PRIVATE_KEY_FILE",
                                        "allowEmpty": False,
                                        "encoding": "pem",
                                    }
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            output_dir = root / "runtime-secrets"
            environment = os.environ.copy()
            environment["TEST_ESCAPED_PEM"] = escaped_key

            result = subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    "--manifest",
                    str(manifest),
                    "--repo-dir",
                    str(root),
                    "--output-dir",
                    str(output_dir),
                    "--compose-env-output",
                    str(root / "compose-secrets.env"),
                    "--sops-binary",
                    str(fake_sops),
                    "--age-key-file",
                    str(key_file),
                ],
                cwd=ROOT,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            materialized_key = output_dir / "competency-trainer/auth_private_key"
            self.assertNotIn("\\n", materialized_key.read_text(encoding="utf-8"))
            validation = subprocess.run(
                ["openssl", "pkey", "-in", str(materialized_key), "-noout"],
                check=False,
                capture_output=True,
                timeout=5,
            )
            self.assertEqual(0, validation.returncode, validation.stderr.decode())

    def test_age_key_must_use_an_absolute_external_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            key_file = root / "age-key.txt"
            key_file.write_text("AGE-SECRET-KEY-test\n", encoding="utf-8")
            key_file.chmod(0o600)
            manifest = root / "manifest.json"
            manifest.write_text('{"documents": []}\n', encoding="utf-8")

            result = subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    "--manifest",
                    str(manifest),
                    "--repo-dir",
                    str(root),
                    "--output-dir",
                    str(root / "runtime-secrets"),
                    "--compose-env-output",
                    str(root / "compose-secrets.env"),
                    "--age-key-file",
                    "age-key.txt",
                ],
                cwd=root,
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )

            self.assertNotEqual(0, result.returncode)
            self.assertIn("absolute", result.stderr)

    def test_age_key_cannot_be_stored_inside_the_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            key_file = root / "age-key.txt"
            key_file.write_text("AGE-SECRET-KEY-test\n", encoding="utf-8")
            key_file.chmod(0o600)
            manifest = root / "manifest.json"
            manifest.write_text('{"documents": []}\n', encoding="utf-8")

            result = subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    "--manifest",
                    str(manifest),
                    "--repo-dir",
                    str(root),
                    "--output-dir",
                    str(root / "runtime-secrets"),
                    "--compose-env-output",
                    str(root / "compose-secrets.env"),
                    "--age-key-file",
                    str(key_file),
                ],
                cwd=root,
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )

            self.assertNotEqual(0, result.returncode)
            self.assertIn("outside", result.stderr)

    def test_age_key_cannot_be_stored_inside_the_stable_deployment_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            stable_root = Path(temporary_directory) / "deploy-root"
            root = stable_root / ".deploy-state/releases/release-1-1"
            root.mkdir(parents=True)
            (root / ".alittlemore-runtime-root").write_text(
                f"{stable_root}\n", encoding="utf-8"
            )
            key_file = stable_root / "age-key.txt"
            key_file.write_text("AGE-SECRET-KEY-test\n", encoding="utf-8")
            key_file.chmod(0o600)
            manifest = root / "manifest.json"
            manifest.write_text('{"documents": []}\n', encoding="utf-8")

            result = subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    "--manifest",
                    str(manifest),
                    "--repo-dir",
                    str(root),
                    "--output-dir",
                    str(stable_root / ".deploy-state/compose-secrets"),
                    "--compose-env-output",
                    str(stable_root / ".deploy-state/compose-secrets.env"),
                    "--age-key-file",
                    str(key_file),
                ],
                cwd=root,
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )

            self.assertNotEqual(0, result.returncode)
            self.assertIn("outside", result.stderr)

    def test_duplicate_native_secret_names_are_materialized_per_service(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            sandbox = Path(temporary_directory)
            root = sandbox / "repo"
            (root / "secrets/personal-workspace").mkdir(parents=True)
            (root / "secrets/competency-trainer").mkdir(parents=True)
            (root / "secrets/personal-workspace/production.sops.yaml").write_text(
                "encrypted personal", encoding="utf-8"
            )
            (root / "secrets/competency-trainer/production.sops.yaml").write_text(
                "encrypted competency", encoding="utf-8"
            )
            key_file = sandbox / "age-key.txt"
            key_file.write_text("AGE-SECRET-KEY-test\n", encoding="utf-8")
            key_file.chmod(0o600)
            fake_sops = root / "fake-sops"
            fake_sops.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, pathlib, sys\n"
                "assert os.environ['SOPS_AGE_KEY_FILE'].endswith('age-key.txt')\n"
                "scope = pathlib.Path(sys.argv[-1]).parent.name\n"
                "values = {\n"
                "  'personal-workspace': {'DB_PASSWORD': 'personal-secret'},\n"
                "  'competency-trainer': {'DB_PASSWORD': 'competency-secret'},\n"
                "}\n"
                "print(json.dumps(values[scope]))\n",
                encoding="utf-8",
            )
            fake_sops.chmod(0o755)
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "documents": [
                            {
                                "name": "personal-workspace",
                                "path": "secrets/personal-workspace/production.sops.yaml",
                                "secrets": [
                                    {
                                        "name": "DB_PASSWORD",
                                        "target": "personal-workspace/db_password",
                                        "composeVariable": "COMPOSE_PERSONAL_WORKSPACE_DB_PASSWORD_FILE",
                                        "allowEmpty": False,
                                    }
                                ],
                            },
                            {
                                "name": "competency-trainer",
                                "path": "secrets/competency-trainer/production.sops.yaml",
                                "secrets": [
                                    {
                                        "name": "DB_PASSWORD",
                                        "target": "competency-trainer/db_password",
                                        "composeVariable": "COMPOSE_COMPETENCY_DB_PASSWORD_FILE",
                                        "allowEmpty": False,
                                    }
                                ],
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            output_dir = root / "runtime-secrets"
            compose_env = root / "compose-secrets.env"

            result = subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    "--manifest",
                    str(manifest),
                    "--repo-dir",
                    str(root),
                    "--output-dir",
                    str(output_dir),
                    "--compose-env-output",
                    str(compose_env),
                    "--sops-binary",
                    str(fake_sops),
                    "--age-key-file",
                    str(key_file),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            personal = output_dir / "personal-workspace/db_password"
            competency = output_dir / "competency-trainer/db_password"
            self.assertEqual("personal-secret", personal.read_text(encoding="utf-8"))
            self.assertEqual("competency-secret", competency.read_text(encoding="utf-8"))
            self.assertEqual(0o444, personal.stat().st_mode & 0o777)
            self.assertEqual(0o444, competency.stat().st_mode & 0o777)
            self.assertEqual(0o600, compose_env.stat().st_mode & 0o777)
            compose_environment = compose_env.read_text(encoding="utf-8")
            self.assertIn(str(personal), compose_environment)
            self.assertIn(str(competency), compose_environment)
            self.assertNotIn("personal-secret", result.stdout + result.stderr + compose_environment)
            self.assertNotIn("competency-secret", result.stdout + result.stderr + compose_environment)

    def test_missing_secret_is_rejected_without_exposing_other_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            sandbox = Path(temporary_directory)
            root = sandbox / "repo"
            (root / "secrets/platform").mkdir(parents=True)
            (root / "secrets/platform/production.sops.yaml").write_text(
                "encrypted", encoding="utf-8"
            )
            key_file = sandbox / "age-key.txt"
            key_file.write_text("AGE-SECRET-KEY-test\n", encoding="utf-8")
            key_file.chmod(0o600)
            fake_sops = root / "fake-sops"
            fake_sops.write_text(
                "#!/usr/bin/env python3\n"
                "import json\n"
                "print(json.dumps({'PRESENT': 'do-not-print'}))\n",
                encoding="utf-8",
            )
            fake_sops.chmod(0o755)
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "documents": [
                            {
                                "name": "platform",
                                "path": "secrets/platform/production.sops.yaml",
                                "secrets": [
                                    {
                                        "name": "MISSING",
                                        "target": "platform/missing",
                                        "composeVariable": "COMPOSE_MISSING_FILE",
                                        "allowEmpty": False,
                                    }
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    "--manifest",
                    str(manifest),
                    "--repo-dir",
                    str(root),
                    "--output-dir",
                    str(root / "runtime-secrets"),
                    "--compose-env-output",
                    str(root / "compose-secrets.env"),
                    "--sops-binary",
                    str(fake_sops),
                    "--age-key-file",
                    str(key_file),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )

            self.assertNotEqual(0, result.returncode)
            self.assertIn("MISSING", result.stderr)
            self.assertNotIn("do-not-print", result.stdout + result.stderr)

    def test_failed_decryption_does_not_replace_existing_runtime_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            sandbox = Path(temporary_directory)
            root = sandbox / "repo"
            for scope in ("first", "second"):
                (root / f"secrets/{scope}").mkdir(parents=True)
                (root / f"secrets/{scope}/production.sops.yaml").write_text(
                    "encrypted", encoding="utf-8"
                )
            key_file = sandbox / "age-key.txt"
            key_file.write_text("AGE-SECRET-KEY-test\n", encoding="utf-8")
            key_file.chmod(0o600)
            fake_sops = root / "fake-sops"
            fake_sops.write_text(
                "#!/usr/bin/env python3\n"
                "import json, pathlib, sys\n"
                "if pathlib.Path(sys.argv[-1]).parent.name == 'second':\n"
                "    raise SystemExit(1)\n"
                "print(json.dumps({'TOKEN': 'new-secret'}))\n",
                encoding="utf-8",
            )
            fake_sops.chmod(0o755)
            specs = [
                {
                    "name": "TOKEN",
                    "target": f"{scope}/token",
                    "composeVariable": f"COMPOSE_{scope.upper()}_TOKEN_FILE",
                    "allowEmpty": False,
                }
                for scope in ("first", "second")
            ]
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "documents": [
                            {
                                "name": scope,
                                "path": f"secrets/{scope}/production.sops.yaml",
                                "secrets": [spec],
                            }
                            for scope, spec in zip(("first", "second"), specs)
                        ]
                    }
                ),
                encoding="utf-8",
            )
            output_dir = root / "runtime-secrets"
            (output_dir / "first").mkdir(parents=True)
            existing = output_dir / "first/token"
            existing.write_text("old-secret", encoding="utf-8")
            compose_env = root / "compose-secrets.env"
            compose_env.write_text("old-paths\n", encoding="utf-8")

            result = subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    "--manifest",
                    str(manifest),
                    "--repo-dir",
                    str(root),
                    "--output-dir",
                    str(output_dir),
                    "--compose-env-output",
                    str(compose_env),
                    "--sops-binary",
                    str(fake_sops),
                    "--age-key-file",
                    str(key_file),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )

            self.assertNotEqual(0, result.returncode)
            self.assertEqual("old-secret", existing.read_text(encoding="utf-8"))
            self.assertEqual("old-paths\n", compose_env.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
