#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "infra/scripts"))

from render_deploy_env import quote_env_value, write_private_file  # noqa: E402


class RenderDeployEnvironmentTest(unittest.TestCase):
    def test_shell_metacharacters_are_loaded_as_literal_data(self) -> None:
        original = 'trailing\\ ${SHOULD_NOT_EXPAND} `printf injected` "quoted"'
        command = f"VALUE={quote_env_value(original)}\nprintf '%s' \"$VALUE\""

        result = subprocess.run(
            ["bash", "-c", command],
            check=True,
            capture_output=True,
            env={"SHOULD_NOT_EXPAND": "expanded"},
        )

        self.assertEqual(original, result.stdout.decode())

    def test_multiline_values_are_encoded_for_later_secret_file_decoding(self) -> None:
        original = "first line\nsecond line\r\n"
        command = f"VALUE={quote_env_value(original)}\nprintf '%s' \"$VALUE\""

        result = subprocess.run(
            ["bash", "-c", command],
            check=True,
            capture_output=True,
        )

        self.assertEqual(r"first line\nsecond line\r\n", result.stdout.decode())

    def test_runtime_environment_file_is_owner_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / ".env"
            output.write_text("old", encoding="utf-8")
            output.chmod(0o644)

            write_private_file(output, "SECRET=value\n")

            self.assertEqual(0o600, output.stat().st_mode & 0o777)
            self.assertEqual("SECRET=value\n", output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
