#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import io
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping


Download = Callable[[str], bytes]


class ToolError(RuntimeError):
    pass


@dataclass(frozen=True)
class Artifact:
    url: str
    sha256: str
    archive_member: str | None = None


@dataclass(frozen=True)
class ToolSpec:
    name: str
    command: str
    version: str
    environment_variable: str
    artifacts: Mapping[tuple[str, str], Artifact]
    version_arguments: tuple[str, ...] = ("--version",)


SOPS_VERSION = "3.13.3"
AGE_VERSION = "1.3.2"

TOOLS: dict[str, ToolSpec] = {
    "sops": ToolSpec(
        name="sops",
        command="sops",
        version=SOPS_VERSION,
        environment_variable="SOPS_INTEGRATION_BINARY",
        version_arguments=("--version", "--disable-version-check"),
        artifacts={
            ("linux", "amd64"): Artifact(
                url=f"https://github.com/getsops/sops/releases/download/v{SOPS_VERSION}/sops-v{SOPS_VERSION}.linux.amd64",
                sha256="e5bec3346a873ae91d871550f3e698c1aad962aff462a080e40f25fde17fef6b",
            ),
            ("linux", "arm64"): Artifact(
                url=f"https://github.com/getsops/sops/releases/download/v{SOPS_VERSION}/sops-v{SOPS_VERSION}.linux.arm64",
                sha256="53b0abacd38ef1b12a66d6c100956691b9cefce018d91f81e73ddf7438b94d77",
            ),
            ("darwin", "amd64"): Artifact(
                url=f"https://github.com/getsops/sops/releases/download/v{SOPS_VERSION}/sops-v{SOPS_VERSION}.darwin.amd64",
                sha256="42162d5cef10b74fcf80a045a70e658d7ce6e63d6ea1be6f347e44015714468d",
            ),
            ("darwin", "arm64"): Artifact(
                url=f"https://github.com/getsops/sops/releases/download/v{SOPS_VERSION}/sops-v{SOPS_VERSION}.darwin.arm64",
                sha256="b97c0d434aab577dc40310e8d22ff9e45eef4c80638ab978daae9b4681c59286",
            ),
        },
    ),
    "age-keygen": ToolSpec(
        name="age-keygen",
        command="age-keygen",
        version=AGE_VERSION,
        environment_variable="AGE_KEYGEN_INTEGRATION_BINARY",
        artifacts={
            ("linux", "amd64"): Artifact(
                url=f"https://github.com/FiloSottile/age/releases/download/v{AGE_VERSION}/age-v{AGE_VERSION}-linux-amd64.tar.gz",
                sha256="cbe24006683f8eb669266162894b9a522a1af52f2665fbc63a4bb032ed26ac10",
                archive_member="age/age-keygen",
            ),
            ("linux", "arm64"): Artifact(
                url=f"https://github.com/FiloSottile/age/releases/download/v{AGE_VERSION}/age-v{AGE_VERSION}-linux-arm64.tar.gz",
                sha256="6b8dc4333c53a5a57c9e5834e3a48f92605d7154014cd07269ff3327db5d37f4",
                archive_member="age/age-keygen",
            ),
            ("darwin", "amd64"): Artifact(
                url=f"https://github.com/FiloSottile/age/releases/download/v{AGE_VERSION}/age-v{AGE_VERSION}-darwin-amd64.tar.gz",
                sha256="1d1e4bc66e1427edad7739ae7616157de0e79db8b6d2a1497d7d9925fb06a539",
                archive_member="age/age-keygen",
            ),
            ("darwin", "arm64"): Artifact(
                url=f"https://github.com/FiloSottile/age/releases/download/v{AGE_VERSION}/age-v{AGE_VERSION}-darwin-arm64.tar.gz",
                sha256="e2020b073c44f692685a24d6abc378817eb81ffaaf49fd0531ef8565f767f2f5",
                archive_member="age/age-keygen",
            ),
        },
    ),
}


def download_bytes(url: str) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=120) as response:
            return response.read()
    except OSError as exc:
        raise ToolError(f"could not download {url}: {exc}") from exc


def normalize_platform(system: str, machine: str) -> tuple[str, str]:
    normalized_system = system.lower()
    normalized_machine = machine.lower()
    systems = {"linux": "linux", "darwin": "darwin"}
    machines = {
        "amd64": "amd64",
        "x86_64": "amd64",
        "arm64": "arm64",
        "aarch64": "arm64",
    }
    try:
        return systems[normalized_system], machines[normalized_machine]
    except KeyError as exc:
        raise ToolError(f"unsupported platform: {system}/{machine}") from exc


def artifact_for(spec: ToolSpec, system: str, machine: str) -> Artifact:
    platform_key = normalize_platform(system, machine)
    try:
        return spec.artifacts[platform_key]
    except KeyError as exc:
        raise ToolError(
            f"unsupported platform for {spec.name}: {platform_key[0]}/{platform_key[1]}"
        ) from exc


def extract_payload(artifact: Artifact, downloaded: bytes) -> bytes:
    if artifact.archive_member is None:
        return downloaded
    try:
        with tarfile.open(fileobj=io.BytesIO(downloaded), mode="r:gz") as archive:
            member = archive.getmember(artifact.archive_member)
            if not member.isfile():
                raise ToolError(
                    f"release archive member is not a regular file: {artifact.archive_member}"
                )
            extracted = archive.extractfile(member)
            if extracted is None:
                raise ToolError(
                    f"release archive member could not be read: {artifact.archive_member}"
                )
            return extracted.read()
    except (KeyError, tarfile.TarError) as exc:
        raise ToolError(
            f"release archive is missing {artifact.archive_member}"
        ) from exc


def install_artifact(
    artifact: Artifact,
    destination: Path,
    *,
    downloader: Download = download_bytes,
) -> None:
    downloaded = downloader(artifact.url)
    actual_checksum = hashlib.sha256(downloaded).hexdigest()
    if actual_checksum != artifact.sha256:
        raise ToolError(
            f"checksum mismatch for {artifact.url}: expected {artifact.sha256}, got {actual_checksum}"
        )
    executable = extract_payload(artifact, downloaded)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(executable)
            output.flush()
            os.fsync(output.fileno())
        temporary.chmod(0o755)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def has_expected_version(path: Path, spec: ToolSpec) -> bool:
    try:
        result = subprocess.run(
            [str(path), *spec.version_arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    output = f"{result.stdout}\n{result.stderr}"
    version_pattern = rf"(?<![0-9]){re.escape(spec.version)}(?![0-9])"
    return result.returncode == 0 and re.search(version_pattern, output) is not None


def install_tool(
    name: str,
    destination: Path,
    *,
    system: str | None = None,
    machine: str | None = None,
    downloader: Download = download_bytes,
) -> Path:
    try:
        spec = TOOLS[name]
    except KeyError as exc:
        raise ToolError(f"unknown quality tool: {name}") from exc
    artifact = artifact_for(
        spec,
        system if system is not None else platform.system(),
        machine if machine is not None else platform.machine(),
    )
    install_artifact(artifact, destination, downloader=downloader)
    if not has_expected_version(destination, spec):
        destination.unlink(missing_ok=True)
        raise ToolError(
            f"installed {spec.name} does not report expected version {spec.version}"
        )
    return destination


def resolve_tool(
    name: str,
    cache_dir: Path,
    *,
    environment: Mapping[str, str] | None = None,
    system: str | None = None,
    machine: str | None = None,
    downloader: Download = download_bytes,
) -> Path:
    try:
        spec = TOOLS[name]
    except KeyError as exc:
        raise ToolError(f"unknown quality tool: {name}") from exc
    effective_environment = os.environ if environment is None else environment
    explicit = effective_environment.get(spec.environment_variable)
    if explicit:
        explicit_path = Path(explicit).expanduser().absolute()
        if explicit_path.exists():
            if not has_expected_version(explicit_path, spec):
                raise ToolError(
                    f"{spec.environment_variable} does not point to {spec.name} {spec.version}: "
                    f"{explicit_path}"
                )
            return explicit_path
        return install_tool(
            name,
            explicit_path,
            system=system,
            machine=machine,
            downloader=downloader,
        )

    path_candidate = shutil.which(spec.command, path=effective_environment.get("PATH"))
    if path_candidate is not None:
        path_binary = Path(path_candidate).absolute()
        if has_expected_version(path_binary, spec):
            return path_binary

    normalized_system, normalized_machine = normalize_platform(
        system if system is not None else platform.system(),
        machine if machine is not None else platform.machine(),
    )
    cached = (
        cache_dir.expanduser().absolute()
        / f"{spec.name}-{spec.version}-{normalized_system}-{normalized_machine}"
        / spec.command
    )
    if cached.exists() and has_expected_version(cached, spec):
        return cached
    return install_tool(
        name,
        cached,
        system=normalized_system,
        machine=normalized_machine,
        downloader=downloader,
    )


def ensure_tools(
    cache_dir: Path,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Path]:
    return {
        name: resolve_tool(name, cache_dir, environment=environment)
        for name in ("sops", "age-keygen")
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Resolve checksum-pinned quality tools.")
    subparsers = parser.add_subparsers(dest="action", required=True)
    ensure = subparsers.add_parser("ensure", help="Resolve or install every quality tool.")
    ensure.add_argument("--cache-dir", required=True, type=Path)
    install = subparsers.add_parser("install", help="Install one tool at an exact destination.")
    install.add_argument("tool", choices=sorted(TOOLS))
    install.add_argument("destination", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.action == "ensure":
            resolved = ensure_tools(args.cache_dir)
            for name, path in resolved.items():
                print(f"{name} {TOOLS[name].version}: {path}")
        else:
            installed = install_tool(args.tool, args.destination.expanduser().absolute())
            print(installed)
    except (OSError, ToolError) as exc:
        print(f"quality_tools.py: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
