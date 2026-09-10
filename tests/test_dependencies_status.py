#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "infra/scripts/dependencies_status.py"


def write_fixture(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def run_status(
    *,
    sops_latest: str = "v3.13.3",
    age_latest: str = "v1.3.2",
    openssl_apkbuild: str = "pkgver=3.5.7\npkgrel=0\n",
    check: bool = False,
) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
    with tempfile.TemporaryDirectory() as temporary_directory:
        fixture_dir = Path(temporary_directory)
        repo_dir = fixture_dir / "repo"
        github_dir = fixture_dir / "github"
        alpine_dir = fixture_dir / "alpine"
        write_fixture(
            repo_dir / "infra/scripts/quality_tools.py",
            'SOPS_VERSION = "3.13.3"\nAGE_VERSION = "1.3.2"\n',
        )
        write_fixture(
            repo_dir / "infra/cert-sync/Dockerfile",
            "FROM alpine:3.22.2\nRUN apk add --no-cache openssl=3.5.7-r0\n",
        )
        write_fixture(
            github_dir / "repos/getsops/sops/releases/latest",
            json.dumps({"tag_name": sops_latest}),
        )
        write_fixture(
            github_dir / "repos/FiloSottile/age/releases/latest",
            json.dumps({"tag_name": age_latest}),
        )
        write_fixture(
            alpine_dir / "3.22-stable/main/openssl/APKBUILD",
            openssl_apkbuild,
        )
        tracked_files = {
            str(path.relative_to(repo_dir)): path.read_text(encoding="utf-8")
            for path in repo_dir.rglob("*")
            if path.is_file()
        }

        arguments = [
            "python3",
            str(SCRIPT),
            "--repo-dir",
            str(repo_dir),
            "--github-api-base",
            github_dir.as_uri(),
            "--alpine-aports-base",
            alpine_dir.as_uri(),
        ]
        if check:
            arguments.append("--check")
        result = subprocess.run(
            arguments,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        resulting_files = {
            str(path.relative_to(repo_dir)): path.read_text(encoding="utf-8")
            for path in repo_dir.rglob("*")
            if path.is_file()
        }

    return result, {"before": repr(tracked_files), "after": repr(resulting_files)}


class DependenciesStatusTest(unittest.TestCase):
    def test_reports_current_manual_pins_without_modifying_them(self) -> None:
        result, files = run_status()

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("OK      sops 3.13.3", result.stdout)
        self.assertIn("OK      age 1.3.2", result.stdout)
        self.assertIn("OK      openssl 3.5.7-r0", result.stdout)
        self.assertEqual(files["before"], files["after"])

    def test_status_mode_reports_an_update_without_failing(self) -> None:
        result, _ = run_status(age_latest="v1.4.0")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("UPDATE  age 1.3.2 -> 1.4.0", result.stdout)

    def test_check_mode_returns_one_when_an_update_is_available(self) -> None:
        result, _ = run_status(age_latest="v1.4.0", check=True)

        self.assertEqual(1, result.returncode, result.stderr)
        self.assertIn("UPDATE  age 1.3.2 -> 1.4.0", result.stdout)

    def test_returns_two_when_an_upstream_version_cannot_be_parsed(self) -> None:
        result, _ = run_status(openssl_apkbuild="pkgver=3.5.7\n")

        self.assertEqual(2, result.returncode)
        self.assertIn("ERROR   openssl", result.stderr)


if __name__ == "__main__":
    unittest.main()
