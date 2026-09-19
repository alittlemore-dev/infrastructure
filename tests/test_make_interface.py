#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def run_make(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", "--no-print-directory", *arguments],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )


class MakeInterfaceTest(unittest.TestCase):
    def test_default_goal_is_read_only_help(self) -> None:
        result = run_make("--dry-run")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("Available targets:", result.stdout)
        self.assertNotIn("infra/scripts/run.sh", result.stdout)


if __name__ == "__main__":
    unittest.main()
