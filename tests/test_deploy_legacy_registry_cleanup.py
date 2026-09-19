from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "infra/scripts/deploy_activate_payload.sh"


def write_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


class DeployLegacyRegistryCleanupTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.root = Path(temporary_directory.name).resolve()
        self.deploy_root = self.root / "deploy"
        self.state_path = self.deploy_root / ".deploy-state"
        (self.state_path / "releases").mkdir(parents=True)
        (self.state_path / "incoming-123-4").mkdir()
        (self.deploy_root / "certificates").mkdir()
        sentinel = self.deploy_root / ".alittlemore-infra-deploy-root"
        sentinel.write_text("alittlemore-infra\n", encoding="utf-8")

        binary_dir = self.root / "bin"
        binary_dir.mkdir()
        write_executable(
            binary_dir / "stat",
            "#!/bin/sh\n"
            "format=$2\n"
            "path=$3\n"
            "if [ \"$format\" = '%U' ]; then id -un; exit; fi\n"
            "case \"$path\" in\n"
            "  */.alittlemore-infra-deploy-root|*/runtime.lock) printf '600\\n' ;;\n"
            "  */certificates) printf '751\\n' ;;\n"
            "  */registry-auth-*) printf '%s\\n' \"${FAKE_REGISTRY_MODE:-700}\" ;;\n"
            "  *) printf '700\\n' ;;\n"
            "esac\n",
        )
        write_executable(
            binary_dir / "readlink",
            "#!/bin/sh\n"
            "[ \"$1\" = '-f' ] && { realpath \"$2\"; exit; }\n"
            "exec /usr/bin/readlink \"$@\"\n",
        )
        write_executable(binary_dir / "flock", "#!/bin/sh\nexit 0\n")
        self.environment = os.environ.copy()
        self.environment["PATH"] = f"{binary_dir}:{self.environment['PATH']}"

    def run_activation(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "bash",
                str(SCRIPT),
                "--remote",
                str(self.deploy_root),
                "incoming-123-4",
                "false",
            ],
            cwd=ROOT,
            env=self.environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_valid_legacy_registry_auth_is_removed_during_activation(self) -> None:
        registry_auth_path = self.state_path / "registry-auth-122-3"
        registry_auth_path.mkdir()
        (registry_auth_path / "config.json").write_text("legacy credential", encoding="utf-8")

        result = self.run_activation()

        self.assertNotEqual(0, result.returncode)
        self.assertFalse(registry_auth_path.exists())

    def test_legacy_registry_auth_symlink_is_rejected_without_removing_target(self) -> None:
        external_target = self.root / "external-registry-auth"
        external_target.mkdir()
        registry_auth_path = self.state_path / "registry-auth-122-3"
        registry_auth_path.symlink_to(external_target, target_is_directory=True)

        result = self.run_activation()

        self.assertNotEqual(0, result.returncode)
        self.assertTrue(registry_auth_path.is_symlink())
        self.assertTrue(external_target.is_dir())

    def test_legacy_registry_auth_with_unsafe_permissions_is_not_removed(self) -> None:
        registry_auth_path = self.state_path / "registry-auth-122-3"
        registry_auth_path.mkdir()
        self.environment["FAKE_REGISTRY_MODE"] = "755"

        result = self.run_activation()

        self.assertNotEqual(0, result.returncode)
        self.assertTrue(registry_auth_path.is_dir())


if __name__ == "__main__":
    unittest.main()
