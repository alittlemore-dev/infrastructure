from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parent.parent / "infra/scripts/verify_service_health.py"


class ServiceHealthTest(unittest.TestCase):
    def check_states(
        self,
        states: list[dict[str, str]],
        *,
        missing: bool = False,
        frontend_states: list[dict[str, str]] | None = None,
    ):
        with tempfile.TemporaryDirectory() as directory:
            docker = Path(directory) / "docker"
            docker.write_text(
                f"#!{sys.executable}\n"
                "import os, sys\n"
                "if sys.argv[1] == 'compose':\n"
                "    if os.environ['MISSING'] != 'yes': print(sys.argv[-1])\n"
                "else:\n"
                "    key = 'FRONTEND_STATES' if sys.argv[-1] == 'candidate-frontend' else 'STATES'\n"
                "    print(os.environ[key])\n",
                encoding="utf-8",
            )
            docker.chmod(0o755)
            environment = os.environ.copy()
            environment.update(
                PATH=directory + os.pathsep + environment.get("PATH", ""),
                MISSING="yes" if missing else "no",
                STATES="\n".join(json.dumps(state) for state in states),
                FRONTEND_STATES="\n".join(
                    json.dumps(state)
                    for state in (states if frontend_states is None else frontend_states)
                ),
            )
            return subprocess.run(
                [sys.executable, str(SCRIPT), "candidate-api", "candidate-frontend"],
                env=environment, capture_output=True, text=True, check=False, timeout=10,
            )

    def test_accepts_running_healthy_services(self):
        result = self.check_states([{"status": "running", "health": "healthy"}])
        self.assertEqual(0, result.returncode, result.stderr)

    def test_rejects_starting_or_unhealthy_services_after_compose_success(self):
        for health in ("starting", "unhealthy", ""):
            with self.subTest(health=health):
                result = self.check_states([{"status": "running", "health": health}])
                self.assertEqual(1, result.returncode)
                self.assertIn("candidate-api", result.stderr)
                self.assertIn("health=", result.stderr)

    def test_rejects_exited_or_restarting_containers(self):
        for status in ("exited", "restarting"):
            with self.subTest(status=status):
                result = self.check_states([{"status": status, "health": "healthy"}])
                self.assertEqual(1, result.returncode)
                self.assertIn(f"status={status}", result.stderr)

    def test_rejects_missing_service(self):
        result = self.check_states([], missing=True)
        self.assertEqual(1, result.returncode)
        self.assertIn("no container exists", result.stderr)

    def test_rejects_incomplete_inspection(self):
        result = self.check_states([])
        self.assertEqual(1, result.returncode)
        self.assertIn("incomplete container state", result.stderr)

    def test_requires_every_candidate_service_to_be_healthy(self):
        result = self.check_states(
            [{"status": "running", "health": "healthy"}],
            frontend_states=[{"status": "running", "health": "starting"}],
        )
        self.assertEqual(1, result.returncode)
        self.assertIn("candidate-frontend", result.stderr)


if __name__ == "__main__":
    unittest.main()
