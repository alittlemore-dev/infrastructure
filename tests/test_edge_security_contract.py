#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SOPS_BOOTSTRAP_SCRIPT = ROOT / "infra/scripts/bootstrap_sops_secrets.sh"
SSH_CONFIGURE_SCRIPT = ROOT / "infra/scripts/deploy_configure_ssh.sh"


class EdgeSecurityContractTest(unittest.TestCase):
    def test_sops_bootstrap_reports_a_missing_option_value_as_usage_error(self) -> None:
        result = subprocess.run(
            ["bash", str(SOPS_BOOTSTRAP_SCRIPT), "--platform-env"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )

        self.assertEqual(2, result.returncode)
        self.assertIn("Usage:", result.stderr)
        self.assertNotIn("shift count", result.stderr)

    def test_ssh_host_pin_ignores_keyscan_comments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            matching_key = temporary_path / "matching"
            unrelated_key = temporary_path / "unrelated"
            for key_path in (matching_key, unrelated_key):
                subprocess.run(
                    ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key_path)],
                    check=True,
                    capture_output=True,
                    text=True,
                )

            def known_hosts_line(key_path: Path) -> str:
                key_type, key_blob, *_ = key_path.with_suffix(".pub").read_text(
                    encoding="utf-8"
                ).split()
                return f"example.com {key_type} {key_blob}"

            matching_line = known_hosts_line(matching_key)
            keyscan_fixture = temporary_path / "keyscan-output"
            keyscan_fixture.write_text(
                "\n".join(
                    (
                        "# example.com:22 SSH-2.0-test-server",
                        known_hosts_line(unrelated_key),
                        "# example.com:22 SSH-2.0-test-server",
                        "example.com not-a-key invalid-data",
                        matching_line,
                    )
                )
                + "\n",
                encoding="utf-8",
            )

            fake_bin = temporary_path / "bin"
            fake_bin.mkdir()
            fake_keyscan = fake_bin / "ssh-keyscan"
            fake_keyscan.write_text(
                '#!/usr/bin/env bash\nexec /bin/cat "$SSH_KEYSCAN_FIXTURE"\n',
                encoding="utf-8",
            )
            fake_keyscan.chmod(0o755)
            fake_timeout = fake_bin / "timeout"
            fake_timeout.write_text(
                '#!/usr/bin/env bash\nshift\nexec "$@"\n',
                encoding="utf-8",
            )
            fake_timeout.chmod(0o755)

            fingerprint = subprocess.run(
                ["ssh-keygen", "-lf", str(matching_key.with_suffix(".pub")), "-E", "sha256"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.split()[1]
            home = temporary_path / "home"
            github_environment = temporary_path / "github-env"
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{fake_bin}:{environment['PATH']}",
                    "HOME": str(home),
                    "SSH_KEYSCAN_FIXTURE": str(keyscan_fixture),
                    "REMOTE_HOST": "example.com",
                    "REMOTE_USER": "deploy",
                    "REMOTE_PATH": "/srv/alittlemore-dev",
                    "SSH_HOST_KEY_FINGERPRINT": fingerprint,
                    "SSH_PRIVATE_KEY": "test-private-key",
                    "GITHUB_RUN_ID": "123",
                    "GITHUB_RUN_ATTEMPT": "1",
                    "GITHUB_ENV": str(github_environment),
                }
            )

            result = subprocess.run(
                ["bash", str(SSH_CONFIGURE_SCRIPT)],
                cwd=ROOT,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(
                [matching_line],
                (home / ".ssh/known_hosts").read_text(encoding="utf-8").splitlines(),
            )


if __name__ == "__main__":
    unittest.main()
