from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "infra/scripts/deploy_cleanup_payload.sh"


def write_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


class DeployRegistryCleanupTest(unittest.TestCase):
    def test_registry_auth_is_removed_even_when_runtime_lock_is_busy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            deploy_root = root / "deploy"
            state_path = deploy_root / ".deploy-state"
            registry_auth_path = state_path / "registry-auth-123-4"
            registry_auth_path.mkdir(parents=True)
            registry_auth_path.chmod(0o700)
            (registry_auth_path / "config.json").write_text("secret", encoding="utf-8")
            sentinel = deploy_root / ".alittlemore-infra-deploy-root"
            sentinel.write_text("alittlemore-infra\n", encoding="utf-8")
            sentinel.chmod(0o600)
            deploy_root.chmod(0o700)
            state_path.chmod(0o700)

            binary_dir = root / "bin"
            binary_dir.mkdir()
            # GNU stat/readlink are part of the production contract; emulate only the probes used here.
            write_executable(
                binary_dir / "stat",
                "#!/bin/sh\n"
                "format=$2\n"
                "path=$3\n"
                "if [ \"$format\" = '%U' ]; then id -un; exit; fi\n"
                "case \"$path\" in\n"
                "  */.alittlemore-infra-deploy-root|*/runtime.lock) printf '600\\n' ;;\n"
                "  *) printf '700\\n' ;;\n"
                "esac\n",
            )
            write_executable(
                binary_dir / "readlink",
                "#!/bin/sh\n[ \"$1\" = '-f' ] && { realpath \"$2\"; exit; }\nexec /usr/bin/readlink \"$@\"\n",
            )
            write_executable(binary_dir / "flock", "#!/bin/sh\nexit 1\n")
            environment = os.environ.copy()
            environment["PATH"] = f"{binary_dir}:{environment['PATH']}"

            result = subprocess.run(
                ["bash", str(SCRIPT), "--remote", str(deploy_root), "incoming-123-4"],
                cwd=ROOT,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertFalse(registry_auth_path.exists())
            self.assertIn("Runtime lock is busy", result.stderr)


if __name__ == "__main__":
    unittest.main()
