#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
BOOTSTRAP = ROOT / "infra/scripts/minio_bootstrap.sh"


FAKE_MC = r"""#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

state_path = Path(os.environ["FAKE_MC_STATE"])
state = json.loads(state_path.read_text()) if state_path.exists() else {
    "buckets": [], "policies": {}, "users": {}, "attachments": {}
}
arguments = sys.argv[1:]

if arguments[:2] == ["alias", "set"]:
    pass
elif arguments[:2] == ["mb", "--ignore-existing"]:
    bucket = arguments[2]
    if bucket not in state["buckets"]:
        state["buckets"].append(bucket)
elif arguments[:3] == ["admin", "policy", "create"]:
    state["policies"][arguments[4]] = arguments[5]
elif arguments[:3] == ["admin", "user", "add"]:
    state["users"][arguments[4]] = arguments[5]
elif arguments[:3] == ["admin", "policy", "attach"]:
    state["attachments"][arguments[6]] = arguments[4]
else:
    print(f"unexpected mc invocation: {arguments}", file=sys.stderr)
    raise SystemExit(2)

state_path.write_text(json.dumps(state, sort_keys=True))
"""


class MinioBootstrapTest(unittest.TestCase):
    def test_personal_workspace_can_access_its_private_resume_bucket(self) -> None:
        policy_path = ROOT / "infra/minio/policies/personal-workspace.json"
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        allowed = {
            resource
            for statement in policy["Statement"]
            if statement["Effect"] == "Allow" and "s3:*" in statement["Action"]
            for resource in statement["Resource"]
        }

        self.assertIn("arn:aws:s3:::resume-private", allowed)
        self.assertIn("arn:aws:s3:::resume-private/*", allowed)

    def test_bootstrap_can_repeat_without_duplicate_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            secrets_dir = temporary_path / "secrets"
            binary_dir = temporary_path / "bin"
            state_file = temporary_path / "state.json"
            secrets_dir.mkdir()
            binary_dir.mkdir()

            secret_values = {
                "minio_root_access_key": "alittlemore-infra-admin",
                "minio_root_secret_key": "root-secret",
                "personal_workspace_minio_access_key": "personal-workspace",
                "personal_workspace_minio_secret_key": "personal-secret",
                "competency_minio_access_key": "competency-trainer",
                "competency_minio_secret_key": "competency-secret",
                "databasus_minio_access_key": "databasus",
                "databasus_minio_secret_key": "databasus-secret",
                "auth_api_minio_access_key": "auth-api",
                "auth_api_minio_secret_key": "auth-api-secret",
            }
            for name, value in secret_values.items():
                (secrets_dir / name).write_text(value, encoding="utf-8")

            fake_mc = binary_dir / "mc"
            fake_mc.write_text(textwrap.dedent(FAKE_MC), encoding="utf-8")
            fake_mc.chmod(0o700)
            environment = os.environ.copy()
            environment.update(
                {
                    "FAKE_MC_STATE": str(state_file),
                    "MINIO_BOOTSTRAP_SECRETS_DIR": str(secrets_dir),
                    "PATH": f"{binary_dir}:{environment['PATH']}",
                }
            )

            for _ in range(2):
                result = subprocess.run(
                    ["sh", str(BOOTSTRAP)],
                    cwd=ROOT,
                    env=environment,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                self.assertEqual(0, result.returncode, result.stderr)

            state = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(
                [
                    "alittlemore/auth-avatars",
                    "alittlemore/database-backups",
                    "alittlemore/knowledge-private",
                    "alittlemore/media",
                    "alittlemore/resume-private",
                ],
                sorted(state["buckets"]),
            )
            self.assertEqual(
                {
                    "personal-workspace": "/policies/personal-workspace.json",
                    "competency-trainer": "/policies/competency-trainer.json",
                    "databasus": "/policies/databasus.json",
                    "auth-api": "/policies/auth-api.json",
                },
                state["policies"],
            )
            self.assertEqual(
                {
                    "personal-workspace": "personal-secret",
                    "competency-trainer": "competency-secret",
                    "databasus": "databasus-secret",
                    "auth-api": "auth-api-secret",
                },
                state["users"],
            )
            self.assertEqual(
                {
                    "personal-workspace": "personal-workspace",
                    "competency-trainer": "competency-trainer",
                    "databasus": "databasus",
                    "auth-api": "auth-api",
                },
                state["attachments"],
            )


if __name__ == "__main__":
    unittest.main()
