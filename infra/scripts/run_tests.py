#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from quality_tools import TOOLS, ToolError, ensure_tools


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_DIR = SCRIPT_DIR.parent.parent
REQUIRED_HOST_COMMANDS = ("bash", "make", "openssl", "ssh-keygen")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the complete infrastructure test suite.")
    parser.add_argument("--cache-dir", required=True, type=Path)
    return parser.parse_args()


def missing_host_commands(environment: dict[str, str]) -> list[str]:
    search_path = environment.get("PATH")
    return [
        command
        for command in REQUIRED_HOST_COMMANDS
        if shutil.which(command, path=search_path) is None
    ]


def main() -> int:
    args = parse_args()
    environment = os.environ.copy()
    missing = missing_host_commands(environment)
    if missing:
        print(f"run_tests.py: missing required commands: {', '.join(missing)}", file=sys.stderr)
        return 1
    try:
        resolved = ensure_tools(args.cache_dir, environment)
    except (OSError, ToolError) as exc:
        print(f"run_tests.py: {exc}", file=sys.stderr)
        return 1
    for name, path in resolved.items():
        environment[TOOLS[name].environment_variable] = str(path)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-p",
            "test_*.py",
            "-v",
        ],
        cwd=REPO_DIR,
        env=environment,
        check=False,
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
