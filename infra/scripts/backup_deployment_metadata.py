#!/usr/bin/env python3
"""Record deployment provenance and actual application image digests, without Docker secrets."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Mapping


def command(arguments: list[str]) -> str:
    result = subprocess.run(arguments, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise ValueError("Deployment metadata could not be prepared.")
    return result.stdout.strip()


def save(path: Path, value: Mapping[str, object]) -> None:
    if path.parent.is_symlink() or path.is_symlink():
        raise ValueError("Metadata path must not be a symlink.")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    path.parent.chmod(0o755)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as output:
            json.dump(value, output)
            output.flush()
            os.fchmod(output.fileno(), 0o644)
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--project", default="alittlemore-infra")
    parser.add_argument("--release-only", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    try:
        release_file = root / "infra/deploy/release.json"
        if release_file.is_file():
            release_data: object = json.loads(release_file.read_text())
            if not isinstance(release_data, dict):
                raise ValueError("Invalid release metadata.")
            release = release_data.get("infra_release")
            if not isinstance(release, str):
                raise ValueError("Invalid release revision.")
        else:
            release = command(["git", "-C", str(root), "rev-parse", "HEAD"])
        if not re.fullmatch(r"[a-f0-9]{40}", release):
            raise ValueError("Invalid infrastructure revision.")
        images: dict[str, str] = {}
        if not args.release_only:
            ids = command(["docker", "ps", "--filter", f"label=com.docker.compose.project={args.project}", "--format", "{{.ID}}"])
            for container in ids.splitlines():
                detail = command(["docker", "inspect", "--format", '{{index .Config.Labels "com.docker.compose.service"}}|{{.Config.Image}}|{{.Image}}', container])
                service, repository, digest = detail.split("|")
                if not ("backend-" in service or "taskiq-" in service or service.startswith("frontend-")):
                    continue
                if not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
                    raise ValueError("Invalid image digest.")
                repo_digests: object = json.loads(command(["docker", "image", "inspect", "--format", "{{json .RepoDigests}}", digest]))
                if not isinstance(repo_digests, list) or not all(isinstance(item, str) for item in repo_digests):
                    raise ValueError("Invalid image provenance.")
                first = repo_digests[0] if repo_digests else "local-built@" + digest
                if not isinstance(first, str):
                    raise ValueError("Invalid image provenance.")
                images[service] = first
            if not images:
                raise ValueError("No initialized application containers were found.")
        save(args.output_dir / ("release.json" if args.release_only else "deployment.json"), {"infra_release": release, "application_images": images})
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
        print("Backup deployment metadata unavailable; check release and application readiness.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
