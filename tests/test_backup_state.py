import json
import os
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "infra/backup/src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "infra/scripts"))
from infra_backup.state import LockBusy, append_attempt, read_json, run_lock, write_json_atomic


class StateTests(unittest.TestCase):
    def test_interrupted_replace_preserves_state_and_permissions(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "state.json"
            write_json_atomic(path, {"value": 1})
            with patch("infra_backup.state.os.replace", side_effect=OSError("interrupted")):
                with self.assertRaises(OSError):
                    write_json_atomic(path, {"value": 2})
            self.assertEqual(read_json(path), {"value": 1})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(Path(root).iterdir()), [path])

    def test_lock_contention_and_release(self):
        with tempfile.TemporaryDirectory() as root:
            with run_lock(Path(root)):
                with self.assertRaises(LockBusy):
                    with run_lock(Path(root)):
                        self.fail("lock must exclude another process")
            with run_lock(Path(root)):
                pass

    def test_history_is_bounded(self):
        state = {}
        for index in range(100):
            append_attempt(state, {"index": index})
        self.assertEqual(len(state["attempts"]), 32)
        self.assertEqual(state["attempts"][-1]["index"], 99)

    def test_existing_permissions_are_restricted(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            directory.chmod(0o755)
            lock = directory / "run.lock"
            lock.touch()
            lock.chmod(0o666)
            with run_lock(directory):
                self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
                self.assertEqual(lock.stat().st_mode & 0o777, 0o600)

    def test_process_termination_releases_lock_without_success(self):
        with tempfile.TemporaryDirectory() as root:
            code = "from pathlib import Path; from infra_backup.state import run_lock; import time;\nwith run_lock(Path(__import__('sys').argv[1])):\n print('locked',flush=True)\n time.sleep(60)"
            process = subprocess.Popen([sys.executable, "-c", code, root], stdout=subprocess.PIPE, text=True, env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "infra/backup/src")})
            try:
                self.assertEqual(process.stdout.readline().strip(), "locked")
                with self.assertRaises(LockBusy):
                    with run_lock(Path(root)):
                        pass
                process.terminate()
                process.wait(timeout=5)
                with run_lock(Path(root)):
                    self.assertFalse((Path(root) / "status.json").exists())
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
                process.stdout.close()
