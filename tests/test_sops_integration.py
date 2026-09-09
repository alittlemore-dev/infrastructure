#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
BUILD_SCRIPT = ROOT / "infra/scripts/build_sops_documents.py"
MATERIALIZE_SCRIPT = ROOT / "infra/scripts/materialize_sops_secrets.py"
MANIFEST = ROOT / "infra/deploy/runtime-secrets.manifest.json"
SOPS_BINARY = os.environ.get("SOPS_INTEGRATION_BINARY")
AGE_KEYGEN_BINARY = os.environ.get("AGE_KEYGEN_INTEGRATION_BINARY")


@unittest.skipUnless(
    SOPS_BINARY and AGE_KEYGEN_BINARY,
    "set SOPS_INTEGRATION_BINARY and AGE_KEYGEN_INTEGRATION_BINARY for a real SOPS round trip",
)
class SopsIntegrationTest(unittest.TestCase):
    def generate_identity(self, path: Path) -> str:
        result = subprocess.run(
            [AGE_KEYGEN_BINARY, "-o", str(path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        path.chmod(0o600)
        match = re.search(r"Public key: (age1[0-9a-z]{58})", result.stderr)
        self.assertIsNotNone(match, result.stderr)
        assert match is not None
        return match.group(1)

    def test_real_sops_round_trip_preserves_scoped_native_names(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            sandbox = Path(temporary_directory)
            root = sandbox / "repo"
            root.mkdir()
            production_key = sandbox / "production-age-key.txt"
            recovery_key = sandbox / "recovery-age-key.txt"
            recipients = [
                self.generate_identity(production_key),
                self.generate_identity(recovery_key),
            ]
            manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
            scoped_values = {
                document["name"]: {
                    secret["name"]: (
                        ""
                        if secret["allowEmpty"]
                        else f"test-value-for-{document['name']}-{secret['name']}"
                    )
                    for secret in document["secrets"]
                }
                for document in manifest["documents"]
            }
            source_arguments: list[str] = []
            for document_name, values in scoped_values.items():
                source_path = sandbox / f"{document_name}.env"
                source_path.write_text(
                    "".join(f"{name}='{value}'\n" for name, value in values.items()),
                    encoding="utf-8",
                )
                source_path.chmod(0o600)
                source_arguments.extend(["--source-env", f"{document_name}={source_path}"])

            build = subprocess.run(
                [
                    "python3",
                    str(BUILD_SCRIPT),
                    "--manifest",
                    str(MANIFEST),
                    "--repo-dir",
                    str(root),
                    "--sops-binary",
                    SOPS_BINARY,
                    *source_arguments,
                    "--age-recipient",
                    recipients[0],
                    "--age-recipient",
                    recipients[1],
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(0, build.returncode, build.stderr)

            encrypted_text = "\n".join(
                (root / document["path"]).read_text(encoding="utf-8")
                for document in manifest["documents"]
            )
            for values in scoped_values.values():
                for value in values.values():
                    if value:
                        self.assertNotIn(value, encrypted_text)

            runtime_dir = root / "runtime-secrets"
            materialize = subprocess.run(
                [
                    "python3",
                    str(MATERIALIZE_SCRIPT),
                    "--manifest",
                    str(MANIFEST),
                    "--repo-dir",
                    str(root),
                    "--output-dir",
                    str(runtime_dir),
                    "--compose-env-output",
                    str(root / "compose-secrets.env"),
                    "--sops-binary",
                    SOPS_BINARY,
                    "--age-key-file",
                    str(recovery_key),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(0, materialize.returncode, materialize.stderr)
            for document in manifest["documents"]:
                for secret in document["secrets"]:
                    self.assertEqual(
                        scoped_values[document["name"]][secret["name"]],
                        (runtime_dir / secret["target"]).read_text(encoding="utf-8"),
                    )


if __name__ == "__main__":
    unittest.main()
