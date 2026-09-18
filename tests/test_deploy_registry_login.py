from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "infra/scripts/deploy_registry_login.sh"


def write_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


class DeployRegistryLoginTest(unittest.TestCase):
    def test_registry_token_reaches_remote_docker_only_through_stdin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            platform_environment = root / "production.env"
            platform_environment.write_text(
                ' IMAGE_REGISTRY = "registry.example.com/namespace"\n',
                encoding="utf-8",
            )
            binary_dir = root / "bin"
            binary_dir.mkdir()
            ssh_dir = root / ".ssh"
            ssh_dir.mkdir()
            (ssh_dir / "alittlemore-infra").touch()
            (ssh_dir / "known_hosts").touch()
            ssh_log = root / "ssh.log"
            token_log = root / "token.log"
            write_executable(
                binary_dir / "python3",
                "#!/bin/sh\n"
                "if [ \"${REGISTRY_TOKEN+x}\" = x ]; then\n"
                "  echo 'REGISTRY_TOKEN leaked to a pre-SSH helper' >&2\n"
                "  exit 91\n"
                "fi\n"
                f'exec "{sys.executable}" "$@"\n',
            )
            write_executable(
                binary_dir / "timeout",
                "#!/bin/sh\nshift\nexec \"$@\"\n",
            )
            write_executable(
                binary_dir / "ssh",
                "#!/bin/sh\n"
                "if [ \"${REGISTRY_TOKEN+x}\" = x ]; then\n"
                "  echo 'REGISTRY_TOKEN leaked through the process environment' >&2\n"
                "  exit 90\n"
                "fi\n"
                "printf '%s\\n' \"$*\" >\"$FAKE_SSH_LOG\"\n"
                "cat >\"$FAKE_TOKEN_LOG\"\n",
            )
            token = "registry-token-must-not-leak"
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{binary_dir}:{environment['PATH']}",
                    "HOME": str(root),
                    "ALITTLEMORE_PLATFORM_ENVIRONMENT": str(platform_environment),
                    "VALIDATED_REMOTE_HOST": "vps.example.com",
                    "VALIDATED_REMOTE_USER": "deploy",
                    "VALIDATED_REMOTE_PATH": "/srv/alittlemore",
                    "VALIDATED_DEPLOY_STAGE": "incoming-123-4",
                    "REGISTRY_USERNAME": "registry-user",
                    "REGISTRY_TOKEN": token,
                    "FAKE_SSH_LOG": str(ssh_log),
                    "FAKE_TOKEN_LOG": str(token_log),
                }
            )

            result = subprocess.run(
                ["bash", str(SCRIPT)],
                cwd=ROOT,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(token, token_log.read_text(encoding="utf-8"))
            invocation = ssh_log.read_text(encoding="utf-8")
            self.assertIn(
                "DOCKER_CONFIG=/srv/alittlemore/.deploy-state/registry-auth-123-4 "
                "docker login registry.example.com --username registry-user --password-stdin",
                invocation,
            )
            self.assertNotIn(token, invocation)
            self.assertNotIn(token, result.stdout)
            self.assertNotIn(token, result.stderr)

    def test_invalid_registry_or_connection_values_never_reach_ssh(self) -> None:
        invalid_cases = {
            "registry host": ("bad;host/namespace", "registry-user", "vps.example.com", "deploy"),
            "registry username": (
                "registry.example.com/namespace",
                "bad;username",
                "vps.example.com",
                "deploy",
            ),
            "remote host": (
                "registry.example.com/namespace",
                "registry-user",
                "bad;host",
                "deploy",
            ),
            "remote user": (
                "registry.example.com/namespace",
                "registry-user",
                "vps.example.com",
                "bad;user",
            ),
        }
        for case_name, values in invalid_cases.items():
            with self.subTest(case_name=case_name), tempfile.TemporaryDirectory() as temporary_directory:
                root = Path(temporary_directory)
                platform_environment = root / "production.env"
                platform_environment.write_text(
                    f"IMAGE_REGISTRY={values[0]}\n",
                    encoding="utf-8",
                )
                binary_dir = root / "bin"
                binary_dir.mkdir()
                ssh_dir = root / ".ssh"
                ssh_dir.mkdir()
                (ssh_dir / "alittlemore-infra").touch()
                (ssh_dir / "known_hosts").touch()
                ssh_marker = root / "ssh-called"
                write_executable(binary_dir / "timeout", "#!/bin/sh\nshift\nexec \"$@\"\n")
                write_executable(
                    binary_dir / "ssh",
                    "#!/bin/sh\ntouch \"$FAKE_SSH_MARKER\"\n",
                )
                environment = os.environ.copy()
                environment.update(
                    {
                        "PATH": f"{binary_dir}:{environment['PATH']}",
                        "HOME": str(root),
                        "ALITTLEMORE_PLATFORM_ENVIRONMENT": str(platform_environment),
                        "VALIDATED_REMOTE_HOST": values[2],
                        "VALIDATED_REMOTE_USER": values[3],
                        "VALIDATED_REMOTE_PATH": "/srv/alittlemore",
                        "VALIDATED_DEPLOY_STAGE": "incoming-123-4",
                        "REGISTRY_USERNAME": values[1],
                        "REGISTRY_TOKEN": "registry-token",
                        "FAKE_SSH_MARKER": str(ssh_marker),
                    }
                )

                result = subprocess.run(
                    ["bash", str(SCRIPT)],
                    cwd=ROOT,
                    env=environment,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )

                self.assertNotEqual(0, result.returncode)
                self.assertFalse(ssh_marker.exists())

    def test_duplicate_registry_setting_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            platform_environment = root / "production.env"
            platform_environment.write_text(
                "IMAGE_REGISTRY=\nIMAGE_REGISTRY=registry.example.com/namespace\n",
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment.update(
                {
                    "ALITTLEMORE_PLATFORM_ENVIRONMENT": str(platform_environment),
                    "REGISTRY_USERNAME": "registry-user",
                    "REGISTRY_TOKEN": "registry-token",
                    "VALIDATED_REMOTE_HOST": "vps.example.com",
                    "VALIDATED_REMOTE_USER": "deploy",
                    "VALIDATED_REMOTE_PATH": "/srv/alittlemore",
                    "VALIDATED_DEPLOY_STAGE": "incoming-123-4",
                }
            )

            result = subprocess.run(
                ["bash", str(SCRIPT)],
                cwd=ROOT,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )

            self.assertNotEqual(0, result.returncode)
            self.assertIn("declares IMAGE_REGISTRY more than once", result.stderr)


if __name__ == "__main__":
    unittest.main()
