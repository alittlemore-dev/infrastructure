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

    def test_help_lists_canonical_workflows(self) -> None:
        result = run_make("help")

        self.assertEqual(0, result.returncode, result.stderr)
        for target in (
            "quality",
            "doctor",
            "doctor-runtime",
            "deploy",
            "status",
            "secrets-verify",
            "security-images",
            "dependencies-status",
            "dev",
            "dev-trust",
        ):
            self.assertIn(target, result.stdout)

    def test_local_development_has_only_two_public_entrypoints(self) -> None:
        dev = run_make("--dry-run", "dev")
        trust = run_make("--dry-run", "dev-trust")

        self.assertEqual(0, dev.returncode, dev.stderr)
        self.assertEqual(0, trust.returncode, trust.stderr)
        self.assertEqual("bash infra/scripts/dev.sh\n", dev.stdout)
        self.assertEqual("bash infra/scripts/dev_tls.sh trust\n", trust.stdout)

    def test_quality_reaches_the_test_runner_once(self) -> None:
        result = run_make("--dry-run", "quality")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(1, result.stdout.count("infra/scripts/run_tests.py"), result.stdout)

    def test_dependency_status_is_an_explicit_read_only_workflow(self) -> None:
        result = run_make("--dry-run", "dependencies-status")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("infra/scripts/dependencies_status.py", result.stdout)
        self.assertNotIn("infra/scripts/run.sh", result.stdout)

    def test_compatibility_aliases_delegate_to_canonical_targets(self) -> None:
        for compatibility_target, canonical_target in (
            ("run", "deploy"),
            ("check", "validate"),
            ("lint-dockerfiles", "lint"),
            ("security-trivy-config", "security-config"),
            ("security-trivy-images", "security-images"),
        ):
            with self.subTest(target=compatibility_target):
                compatibility = run_make("--dry-run", compatibility_target)
                canonical = run_make("--dry-run", canonical_target)
                self.assertEqual(0, compatibility.returncode, compatibility.stderr)
                self.assertEqual(0, canonical.returncode, canonical.stderr)
                self.assertEqual(canonical.stdout, compatibility.stdout)


if __name__ == "__main__":
    unittest.main()
