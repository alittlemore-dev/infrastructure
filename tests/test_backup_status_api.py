from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


class BackupStatusApiTests(unittest.TestCase):
    def test_real_sdk_owner_session_boundary(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(["bash", str(root / "infra/scripts/backup_status_http_test.sh")], capture_output=True, text=True, timeout=360)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
