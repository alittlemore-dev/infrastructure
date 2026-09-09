#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "infra/scripts/build_sops_documents.py"


def write_private_env(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)


def manifest_document(name: str, secret_name: str = "DB_PASSWORD") -> dict[str, object]:
    return {
        "name": name,
        "path": f"secrets/{name}/production.sops.yaml",
        "secrets": [
            {
                "name": secret_name,
                "target": f"{name}/{secret_name.lower()}",
                "composeVariable": f"COMPOSE_{name.upper().replace('-', '_')}_{secret_name}_FILE",
                "allowEmpty": False,
            }
        ],
    }


class BuildSopsDocumentsTest(unittest.TestCase):
    def run_builder(
        self,
        *,
        root: Path,
        manifest: Path,
        fake_sops: Path,
        source_args: list[str],
        recipients: int = 2,
    ) -> subprocess.CompletedProcess[str]:
        command = [
            "python3",
            str(SCRIPT),
            "--manifest",
            str(manifest),
            "--repo-dir",
            str(root),
            "--sops-binary",
            str(fake_sops),
            *source_args,
        ]
        for recipient_character in ("q", "p")[:recipients]:
            command.extend(["--age-recipient", "age1" + recipient_character * 58])
        return subprocess.run(
            command,
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )

    def test_requires_independent_server_and_recovery_recipients(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps({"documents": [manifest_document("platform")]}),
                encoding="utf-8",
            )
            source = root / "platform.env"
            write_private_env(source, "DB_PASSWORD=platform-secret\n")

            result = self.run_builder(
                root=root,
                manifest=manifest,
                fake_sops=root / "unused",
                source_args=["--source-env", f"platform={source}"],
                recipients=1,
            )

            self.assertNotEqual(0, result.returncode)
            self.assertIn("two distinct age recipients", result.stderr)

    def test_service_scoped_dotenv_files_reuse_native_names(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "documents": [
                            manifest_document("personal-workspace"),
                            manifest_document("competency-trainer"),
                        ]
                    }
                ),
                encoding="utf-8",
            )
            fake_sops = root / "fake-sops"
            fake_sops.write_text(
                "#!/usr/bin/env python3\n"
                "import json, sys\n"
                "print(json.dumps(json.load(sys.stdin), sort_keys=True))\n",
                encoding="utf-8",
            )
            fake_sops.chmod(0o755)
            personal_source = root / "personal.env"
            competency_source = root / "competency.env"
            write_private_env(personal_source, "DB_PASSWORD='personal-secret'\n")
            write_private_env(
                competency_source,
                "APP_DEBUG=false\nDB_PASSWORD=competency-secret\n",
            )

            result = self.run_builder(
                root=root,
                manifest=manifest,
                fake_sops=fake_sops,
                source_args=[
                    "--source-env",
                    f"personal-workspace={personal_source}",
                    "--source-env",
                    f"competency-trainer={competency_source}",
                ],
            )

            self.assertEqual(0, result.returncode, result.stderr)
            personal_encrypted = (
                root / "secrets/personal-workspace/production.sops.yaml"
            ).read_text(encoding="utf-8")
            competency_encrypted = (
                root / "secrets/competency-trainer/production.sops.yaml"
            ).read_text(encoding="utf-8")
            self.assertEqual('{"DB_PASSWORD": "personal-secret"}\n', personal_encrypted)
            self.assertEqual('{"DB_PASSWORD": "competency-secret"}\n', competency_encrypted)
            self.assertNotIn("APP_DEBUG", competency_encrypted)
            self.assertNotIn("personal-secret", result.stdout + result.stderr)
            self.assertNotIn("competency-secret", result.stdout + result.stderr)
            sops_config = (root / ".sops.yaml").read_text(encoding="utf-8")
            self.assertIn("age1" + "q" * 58, sops_config)
            self.assertIn("age1" + "p" * 58, sops_config)

    def test_missing_required_local_secret_is_rejected_without_exposure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps({"documents": [manifest_document("platform", "REQUIRED")]}),
                encoding="utf-8",
            )
            source = root / "platform.env"
            write_private_env(source, "OTHER=do-not-print\n")

            result = self.run_builder(
                root=root,
                manifest=manifest,
                fake_sops=root / "unused",
                source_args=["--source-env", f"platform={source}"],
            )

            self.assertNotEqual(0, result.returncode)
            self.assertIn("platform.REQUIRED", result.stderr)
            self.assertNotIn("do-not-print", result.stdout + result.stderr)

    def test_rejects_group_or_world_readable_secret_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps({"documents": [manifest_document("platform")]}),
                encoding="utf-8",
            )
            source = root / "platform.env"
            source.write_text("DB_PASSWORD=do-not-print\n", encoding="utf-8")
            source.chmod(0o644)

            result = self.run_builder(
                root=root,
                manifest=manifest,
                fake_sops=root / "unused",
                source_args=["--source-env", f"platform={source}"],
            )

            self.assertNotEqual(0, result.returncode)
            self.assertIn("owner-only", result.stderr)
            self.assertNotIn("do-not-print", result.stdout + result.stderr)

    def test_rejects_duplicate_and_malformed_dotenv_entries(self) -> None:
        for source_content, expected_error in (
            ("DB_PASSWORD=first\nDB_PASSWORD=do-not-print\n", "more than once"),
            ("not-an-assignment\nDB_PASSWORD=do-not-print\n", "line 1"),
        ):
            with self.subTest(source_content=source_content):
                with tempfile.TemporaryDirectory() as temporary_directory:
                    root = Path(temporary_directory)
                    manifest = root / "manifest.json"
                    manifest.write_text(
                        json.dumps({"documents": [manifest_document("platform")]}),
                        encoding="utf-8",
                    )
                    source = root / "platform.env"
                    write_private_env(source, source_content)

                    result = self.run_builder(
                        root=root,
                        manifest=manifest,
                        fake_sops=root / "unused",
                        source_args=["--source-env", f"platform={source}"],
                    )

                    self.assertNotEqual(0, result.returncode)
                    self.assertIn(expected_error, result.stderr)
                    self.assertNotIn("do-not-print", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
