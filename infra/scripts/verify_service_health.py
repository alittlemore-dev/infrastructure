#!/usr/bin/env python3
"""Require actual healthy containers even when Compose's wait returns success."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys


STATE_FORMAT = (
    '{"status":"{{.State.Status}}",'
    '"health":"{{if .State.Health}}{{.State.Health.Status}}{{end}}"}'
)


def verify_service(service: str) -> None:
    result = subprocess.run(
        ["docker", "compose", "ps", "--all", "--quiet", service],
        check=True, capture_output=True, text=True,
    )
    containers = result.stdout.split()
    if not containers:
        raise ValueError(f"{service}: no container exists")
    result = subprocess.run(
        ["docker", "inspect", "--format", STATE_FORMAT, *containers],
        check=True, capture_output=True, text=True,
    )
    states = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    if len(states) != len(containers):
        raise ValueError(f"{service}: incomplete container state")
    for state in states:
        status = state.get("status")
        health = state.get("health")
        if status != "running" or health != "healthy":
            raise ValueError(f"{service}: status={status}, health={health or 'missing'}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("services", nargs="+")
    args = parser.parse_args()
    try:
        for service in args.services:
            verify_service(service)
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"Runtime readiness verification failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
