#!/usr/bin/env python3
from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CERT_SYNC_SCRIPT = ROOT / "infra/scripts/cert_sync.sh"
ALPINE_IMAGE = "alpine:3.22.2"


class CertificateSyncTest(unittest.TestCase):
    def test_activates_nginx_owned_release_without_fowner_capability(self) -> None:
        if shutil.which("docker") is None:
            self.skipTest("Docker is unavailable")
        docker_info = subprocess.run(
            ["docker", "info"],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        if docker_info.returncode != 0:
            self.skipTest("Docker daemon is unavailable")

        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            source_directory = temporary_path / "letsencrypt/live/alittlemore-apps"
            source_directory.mkdir(parents=True)
            (source_directory / "fullchain.pem").write_text(
                "test-certificate\n", encoding="utf-8"
            )
            (source_directory / "privkey.pem").write_text(
                "test-private-key\n", encoding="utf-8"
            )

            fake_openssl = temporary_path / "openssl"
            fake_openssl.write_text(
                """#!/bin/sh
case "$1" in
    rand)
        printf '0123456789abcdef01234567\\n'
        ;;
    x509)
        case " $* " in
            *" -pubkey "*) printf 'test-public-key\\n' ;;
        esac
        ;;
    pkey)
        case " $* " in
            *" -pubin "* | *" -pubout "*) printf 'test-public-key\\n' ;;
        esac
        ;;
    *)
        exit 1
        ;;
esac
""",
                encoding="utf-8",
            )
            fake_openssl.chmod(0o755)

            volume_name = f"alittlemore-cert-sync-test-{uuid.uuid4().hex}"
            subprocess.run(
                ["docker", "volume", "create", volume_name],
                check=True,
                capture_output=True,
                text=True,
                timeout=20,
            )
            try:
                result = subprocess.run(
                    [
                        "docker",
                        "run",
                        "--rm",
                        "--read-only",
                        "--user",
                        "0:0",
                        "--cap-drop",
                        "ALL",
                        "--cap-add",
                        "CHOWN",
                        "--cap-add",
                        "DAC_OVERRIDE",
                        "--network",
                        "none",
                        "--env",
                        "TLS_CERTIFICATE_NAME=alittlemore-apps",
                        "--env",
                        "APP_DOMAIN=alittlemore.dev",
                        "--env",
                        "MINIO_DOMAIN=s3.alittlemore.dev",
                        "--mount",
                        f"type=bind,src={temporary_path / 'letsencrypt'},dst=/etc/letsencrypt,readonly",
                        "--mount",
                        f"type=bind,src={CERT_SYNC_SCRIPT},dst=/usr/local/bin/alittlemore-cert-sync,readonly",
                        "--mount",
                        f"type=bind,src={fake_openssl},dst=/usr/local/bin/openssl,readonly",
                        "--mount",
                        f"type=volume,src={volume_name},dst=/certs",
                        ALPINE_IMAGE,
                        "/bin/sh",
                        "/usr/local/bin/alittlemore-cert-sync",
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                self.assertEqual(0, result.returncode, result.stderr)

                inspection = subprocess.run(
                    [
                        "docker",
                        "run",
                        "--rm",
                        "--read-only",
                        "--network",
                        "none",
                        "--mount",
                        f"type=volume,src={volume_name},dst=/certs,readonly",
                        ALPINE_IMAGE,
                        "sh",
                        "-eu",
                        "-c",
                        """
release="$(readlink -f /certs/current)"
stat -c '%u:%g %a' "$release"
stat -c '%u:%g %a' "$release/fullchain.pem"
stat -c '%u:%g %a' "$release/privkey.pem"
cat "$release/fullchain.pem"
cat "$release/privkey.pem"
""",
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                self.assertEqual(0, inspection.returncode, inspection.stderr)
                self.assertEqual(
                    [
                        "101:101 751",
                        "101:101 644",
                        "101:101 640",
                        "test-certificate",
                        "test-private-key",
                    ],
                    inspection.stdout.splitlines(),
                )
            finally:
                subprocess.run(
                    ["docker", "volume", "rm", "--force", volume_name],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=20,
                )


if __name__ == "__main__":
    unittest.main()
