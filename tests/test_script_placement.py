#!/usr/bin/env python3
from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class ScriptPlacementTest(unittest.TestCase):
    def test_shell_scripts_live_in_infra_scripts(self) -> None:
        misplaced = sorted(
            str(path.relative_to(ROOT))
            for path in ROOT.rglob("*.sh")
            if path.parent != ROOT / "infra/scripts"
        )

        self.assertEqual([], misplaced)

    def test_workflows_only_invoke_named_scripts(self) -> None:
        violations = []
        workflows = sorted(
            path
            for path in (ROOT / ".github/workflows").iterdir()
            if path.suffix in {".yml", ".yaml"}
        )
        for workflow in workflows:
            if re.search(r"(?m)^\s+run:\s*[|>]", workflow.read_text(encoding="utf-8")):
                violations.append(str(workflow.relative_to(ROOT)))

        self.assertEqual([], violations)

    def test_dockerfiles_do_not_embed_multi_command_run_scripts(self) -> None:
        violations = []
        for dockerfile in sorted((ROOT / "infra").rglob("Dockerfile")):
            source = dockerfile.read_text(encoding="utf-8")
            logical_lines = re.sub(r"\\\n\s*", " ", source).splitlines()
            if any(
                line.startswith("RUN ") and ("&&" in line or ";" in line)
                for line in logical_lines
            ):
                violations.append(str(dockerfile.relative_to(ROOT)))

        self.assertEqual([], violations)


if __name__ == "__main__":
    unittest.main()
