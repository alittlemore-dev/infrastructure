#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "infra/scripts"))

from render_runtime_config import render_runtime_config, write_private_file  # noqa: E402


class RenderRuntimeConfigTest(unittest.TestCase):
    def write_manifest(self, root: Path) -> Path:
        manifest = root / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "configs": [
                        {
                            "name": "platform",
                            "path": "config/platform/production.env",
                            "variables": ["IMAGE_REGISTRY"],
                            "runtimeAliases": {"IMAGE_REGISTRY": "IMAGE_REGISTRY"},
                        },
                        {
                            "name": "personal-workspace",
                            "path": "config/personal-workspace/production.env",
                            "variables": ["APP_DEBUG", "APP_DOMAIN", "DB_NAME"],
                            "runtimeAliases": {
                                "APP_DOMAIN": "PERSONAL_WORKSPACE_DOMAIN",
                                "DB_NAME": "PERSONAL_WORKSPACE_DB_NAME",
                            },
                        },
                        {
                            "name": "competency-trainer",
                            "path": "config/competency-trainer/production.env",
                            "variables": ["APP_DEBUG", "APP_DOMAIN", "DB_NAME"],
                            "runtimeAliases": {
                                "APP_DOMAIN": "COMPETENCY_DOMAIN",
                                "DB_NAME": "COMPETENCY_DB_NAME",
                            },
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        return manifest

    def test_duplicate_native_names_are_isolated_by_config_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "config/platform").mkdir(parents=True)
            (root / "config/personal-workspace").mkdir(parents=True)
            (root / "config/competency-trainer").mkdir(parents=True)
            (root / "config/platform/production.env").write_text(
                "IMAGE_REGISTRY=ghcr.io/alittlemore-dev\n", encoding="utf-8"
            )
            (root / "config/personal-workspace/production.env").write_text(
                "APP_DEBUG=false\nAPP_DOMAIN=personal.example.com\nDB_NAME=personal\n",
                encoding="utf-8",
            )
            (root / "config/competency-trainer/production.env").write_text(
                "APP_DEBUG=true\nAPP_DOMAIN=competency.example.com\nDB_NAME=competency\n",
                encoding="utf-8",
            )

            rendered = render_runtime_config(self.write_manifest(root), root)

            self.assertIn('IMAGE_REGISTRY="ghcr.io/alittlemore-dev"', rendered)
            self.assertIn('PERSONAL_WORKSPACE_DOMAIN="personal.example.com"', rendered)
            self.assertIn('PERSONAL_WORKSPACE_DB_NAME="personal"', rendered)
            self.assertIn('COMPETENCY_DOMAIN="competency.example.com"', rendered)
            self.assertIn('COMPETENCY_DB_NAME="competency"', rendered)
            self.assertNotIn("PERSONAL_WORKSPACE_APP_DEBUG", rendered)
            self.assertNotIn("COMPETENCY_APP_DEBUG", rendered)

    def test_unexpected_secret_in_open_config_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "config/platform").mkdir(parents=True)
            (root / "config/personal-workspace").mkdir(parents=True)
            (root / "config/competency-trainer").mkdir(parents=True)
            (root / "config/platform/production.env").write_text(
                "IMAGE_REGISTRY=ghcr.io/alittlemore-dev\n", encoding="utf-8"
            )
            (root / "config/personal-workspace/production.env").write_text(
                "APP_DEBUG=false\nAPP_DOMAIN=personal.example.com\nDB_NAME=personal\n"
                "DB_PASSWORD=must-not-be-open\n",
                encoding="utf-8",
            )
            (root / "config/competency-trainer/production.env").write_text(
                "APP_DEBUG=true\nAPP_DOMAIN=competency.example.com\nDB_NAME=competency\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "unexpected variables.*DB_PASSWORD"):
                render_runtime_config(self.write_manifest(root), root)

    def test_empty_open_config_value_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "config/platform").mkdir(parents=True)
            (root / "config/personal-workspace").mkdir(parents=True)
            (root / "config/competency-trainer").mkdir(parents=True)
            (root / "config/platform/production.env").write_text(
                "IMAGE_REGISTRY=ghcr.io/alittlemore-dev\n", encoding="utf-8"
            )
            (root / "config/personal-workspace/production.env").write_text(
                "APP_DEBUG=\nAPP_DOMAIN=personal.example.com\nDB_NAME=personal\n",
                encoding="utf-8",
            )
            (root / "config/competency-trainer/production.env").write_text(
                "APP_DEBUG=true\nAPP_DOMAIN=competency.example.com\nDB_NAME=competency\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "empty variables.*APP_DEBUG"):
                render_runtime_config(self.write_manifest(root), root)

    def test_runtime_environment_file_is_owner_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "runtime.env"
            output.write_text("old", encoding="utf-8")
            output.chmod(0o644)

            write_private_file(output, "IMAGE_REGISTRY=value\n")

            self.assertEqual(0o600, output.stat().st_mode & 0o777)
            self.assertEqual("IMAGE_REGISTRY=value\n", output.read_text(encoding="utf-8"))

    def test_runtime_environment_file_does_not_follow_a_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "target"
            target.write_text("must-stay-unchanged\n", encoding="utf-8")
            output = root / "runtime.env"
            output.symlink_to(target)

            with self.assertRaises(OSError):
                write_private_file(output, "IMAGE_REGISTRY=value\n")

            self.assertEqual("must-stay-unchanged\n", target.read_text(encoding="utf-8"))

    def test_shell_metacharacters_are_loaded_as_literal_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "config/platform").mkdir(parents=True)
            (root / "config/personal-workspace").mkdir(parents=True)
            (root / "config/competency-trainer").mkdir(parents=True)
            original = 'trailing\\ ${SHOULD_NOT_EXPAND} `printf injected` "quoted"'
            (root / "config/platform/production.env").write_text(
                f"IMAGE_REGISTRY={original}\n", encoding="utf-8"
            )
            for application in ("personal-workspace", "competency-trainer"):
                (root / f"config/{application}/production.env").write_text(
                    "APP_DEBUG=false\nAPP_DOMAIN=example.com\nDB_NAME=database\n",
                    encoding="utf-8",
                )
            rendered = render_runtime_config(self.write_manifest(root), root)
            command = f"{rendered}\nprintf '%s' \"$IMAGE_REGISTRY\""

            result = subprocess.run(
                ["bash", "-c", command],
                check=True,
                capture_output=True,
                env={"SHOULD_NOT_EXPAND": "expanded"},
            )

            self.assertEqual(original, result.stdout.decode())


if __name__ == "__main__":
    unittest.main()
