from __future__ import annotations

import unittest
from pathlib import Path

from infra.scripts.render_runtime_config import parse_env_file


ROOT = Path(__file__).resolve().parent.parent


class AuthApiEnvironmentContractTest(unittest.TestCase):
    def test_auth_api_environments_configure_private_avatar_storage(self) -> None:
        for environment_name in ("development", "production"):
            with self.subTest(environment=environment_name):
                values = parse_env_file(ROOT / f"config/auth-api/{environment_name}.env")
                self.assertEqual("minio", values["MINIO_HOST"])
                self.assertEqual("9000", values["MINIO_PORT"])
                self.assertEqual("us-east-1", values["MINIO_REGION"])
                self.assertEqual("auth-avatars", values["MINIO_BUCKET"])
                self.assertEqual("false", values["MINIO_SECURE"])
                self.assertEqual("path", values["MINIO_ADDRESSING_STYLE"])
                self.assertEqual(
                    "86400",
                    values["TASKIQ_ACCOUNT_AVATAR_ORPHAN_PRUNE_INTERVAL_SECONDS"],
                )


if __name__ == "__main__":
    unittest.main()
