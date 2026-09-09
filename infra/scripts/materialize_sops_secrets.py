#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from render_runtime_config import quote_env_value, write_private_file


VARIABLE_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")
TARGET_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9_]*$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Decrypt service-scoped SOPS documents into Compose secret files."
    )
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--repo-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--compose-env-output", required=True, type=Path)
    parser.add_argument("--sops-binary", default="sops")
    parser.add_argument("--age-key-file", required=True, type=Path)
    return parser.parse_args()


def require_string(value: Any, description: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{description} must be a non-empty string.")
    return value


def resolve_repo_path(repo_dir: Path, relative_path: str) -> Path:
    resolved_repo = repo_dir.resolve()
    candidate = (resolved_repo / relative_path).resolve()
    try:
        candidate.relative_to(resolved_repo)
    except ValueError as exc:
        raise ValueError(f"Secret document escapes the repository: {relative_path}") from exc
    return candidate


def load_manifest(path: Path) -> list[dict[str, Any]]:
    raw_manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw_manifest, dict) or set(raw_manifest) != {"documents"}:
        raise ValueError("Manifest must contain exactly the documents field.")
    documents = raw_manifest["documents"]
    if not isinstance(documents, list) or not documents:
        raise ValueError("Manifest documents must be a non-empty list.")
    return documents


def decrypt_document(
    sops_binary: str,
    age_key_file: Path,
    document_path: Path,
    document_name: str,
) -> dict[str, str]:
    environment = os.environ.copy()
    environment["SOPS_AGE_KEY_FILE"] = str(age_key_file)
    try:
        result = subprocess.run(
            [sops_binary, "decrypt", "--output-type", "json", str(document_path)],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
    except OSError as exc:
        raise ValueError(f"Could not execute SOPS for {document_name}: {exc}") from exc
    if result.returncode != 0:
        raise ValueError(f"SOPS could not decrypt {document_name}.")
    try:
        raw_values = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"SOPS returned invalid JSON for {document_name}.") from exc
    if not isinstance(raw_values, dict) or not all(
        isinstance(name, str) and isinstance(value, str) for name, value in raw_values.items()
    ):
        raise ValueError(f"SOPS output for {document_name} must be a string map.")
    return raw_values


def write_secret_file(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError(f"Secret target must be a regular file: {path}")
    if path.exists():
        path.unlink()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
    os.fchmod(descriptor, 0o444)
    with os.fdopen(descriptor, "w", encoding="utf-8") as secret_file:
        secret_file.write(value)


def normalize_secret_value(value: str, encoding: str | None) -> str:
    if encoding is None:
        return value
    if encoding != "pem":
        raise ValueError(f"Unsupported secret encoding: {encoding}")
    return value.replace("\\r\\n", "\n").replace("\\n", "\n")


def path_is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def require_external_age_key(age_key_file: Path, repo_dir: Path) -> Path:
    if not age_key_file.is_absolute():
        raise ValueError("SOPS age key must use an absolute path outside deployment releases.")
    if not age_key_file.is_file() or age_key_file.is_symlink():
        raise ValueError("SOPS age key must be a regular, non-symlinked file.")
    resolved_key = age_key_file.resolve(strict=True)
    protected_roots = [repo_dir.resolve()]
    runtime_root_marker = repo_dir / ".alittlemore-runtime-root"
    if runtime_root_marker.exists() or runtime_root_marker.is_symlink():
        if runtime_root_marker.is_symlink() or not runtime_root_marker.is_file():
            raise ValueError("Deployment runtime-root marker must be a regular file.")
        runtime_root = Path(runtime_root_marker.read_text(encoding="utf-8").strip())
        if not runtime_root.is_absolute():
            raise ValueError("Deployment runtime-root marker must contain an absolute path.")
        protected_roots.append(runtime_root.resolve())
    if any(path_is_within(resolved_key, root) for root in protected_roots):
        raise ValueError(
            "SOPS age key must stay outside the repository and stable deployment root."
        )
    if resolved_key.stat().st_uid != os.getuid():
        raise ValueError("SOPS age key must be owned by the deploy user.")
    if resolved_key.stat().st_mode & 0o077:
        raise ValueError("SOPS age key must not be accessible by group or other users.")
    return resolved_key


def materialize(args: argparse.Namespace) -> None:
    age_key_file = require_external_age_key(args.age_key_file, args.repo_dir)

    documents = load_manifest(args.manifest)
    args.output_dir.parent.mkdir(parents=True, exist_ok=True)
    if args.output_dir.exists() and (args.output_dir.is_symlink() or not args.output_dir.is_dir()):
        raise ValueError("Compose secrets output must be a real directory.")
    staging_dir = Path(
        tempfile.mkdtemp(prefix=f".{args.output_dir.name}.staging.", dir=args.output_dir.parent)
    )
    staging_dir.chmod(0o700)

    try:
        compose_paths: dict[str, str] = {}
        seen_document_names: set[str] = set()
        seen_targets: set[str] = set()
        for raw_document in documents:
            if not isinstance(raw_document, dict) or set(raw_document) != {
                "name",
                "path",
                "secrets",
            }:
                raise ValueError("Each document must contain exactly name, path, and secrets.")
            document_name = require_string(raw_document["name"], "Document name")
            if document_name in seen_document_names:
                raise ValueError(f"Duplicate secret document: {document_name}")
            seen_document_names.add(document_name)
            document_path = resolve_repo_path(
                args.repo_dir, require_string(raw_document["path"], f"{document_name} path")
            )
            raw_specs = raw_document["secrets"]
            if not isinstance(raw_specs, list) or not raw_specs:
                raise ValueError(f"{document_name} secrets must be a non-empty list.")

            values = decrypt_document(
                args.sops_binary,
                age_key_file,
                document_path,
                document_name,
            )
            expected_names: set[str] = set()
            for raw_spec in raw_specs:
                allowed_fields = {
                    "name",
                    "target",
                    "composeVariable",
                    "allowEmpty",
                }
                optional_fields = {"encoding"}
                if (
                    not isinstance(raw_spec, dict)
                    or not allowed_fields <= set(raw_spec)
                    or set(raw_spec) - allowed_fields - optional_fields
                ):
                    raise ValueError(
                        f"{document_name} secret specs must contain name, target, "
                        "composeVariable, allowEmpty, and optional encoding."
                    )
                name = require_string(raw_spec["name"], f"{document_name} secret name")
                target = require_string(raw_spec["target"], f"{document_name}.{name} target")
                compose_variable = require_string(
                    raw_spec["composeVariable"], f"{document_name}.{name} composeVariable"
                )
                allow_empty = raw_spec["allowEmpty"]
                encoding = raw_spec.get("encoding")
                if VARIABLE_NAME_PATTERN.fullmatch(name) is None:
                    raise ValueError(f"Invalid secret name: {name}")
                if TARGET_PATTERN.fullmatch(target) is None or target in seen_targets:
                    raise ValueError(f"Invalid or duplicate secret target: {target}")
                if VARIABLE_NAME_PATTERN.fullmatch(compose_variable) is None:
                    raise ValueError(f"Invalid Compose secret variable: {compose_variable}")
                if not isinstance(allow_empty, bool):
                    raise ValueError(f"{document_name}.{name}.allowEmpty must be boolean.")
                if encoding not in (None, "pem"):
                    raise ValueError(f"{document_name}.{name}.encoding is not supported.")
                if name in expected_names:
                    raise ValueError(f"{document_name} declares {name} more than once.")
                if compose_variable in compose_paths:
                    raise ValueError(f"Duplicate Compose secret variable: {compose_variable}")
                expected_names.add(name)
                seen_targets.add(target)

                if name not in values:
                    raise ValueError(f"{document_name} is missing secret: {name}")
                value = normalize_secret_value(values[name], encoding)
                if not value and not allow_empty:
                    raise ValueError(f"{document_name}.{name} must not be empty.")
                write_secret_file(staging_dir / target, value)
                compose_paths[compose_variable] = str(args.output_dir / target)

            unexpected_names = sorted(set(values) - expected_names)
            if unexpected_names:
                raise ValueError(
                    f"{document_name} has unexpected secrets: {', '.join(unexpected_names)}"
                )

        previous_dir: Path | None = None
        if args.output_dir.exists():
            previous_dir = Path(
                tempfile.mkdtemp(
                    prefix=f".{args.output_dir.name}.previous.", dir=args.output_dir.parent
                )
            )
            previous_dir.rmdir()
            args.output_dir.rename(previous_dir)
        try:
            staging_dir.rename(args.output_dir)
        except OSError:
            if previous_dir is not None and not args.output_dir.exists():
                previous_dir.rename(args.output_dir)
            raise
        if previous_dir is not None:
            shutil.rmtree(previous_dir)

        lines = [
            "# Generated by infra/scripts/materialize_sops_secrets.py.",
            "# Contains paths only; secret values are stored in the referenced files.",
        ]
        lines.extend(f"{name}={quote_env_value(value)}" for name, value in compose_paths.items())
        lines.append("")
        write_private_file(args.compose_env_output, "\n".join(lines))
    finally:
        if staging_dir.exists():
            shutil.rmtree(staging_dir)


def main() -> int:
    args = parse_args()
    try:
        materialize(args)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"materialize_sops_secrets.py: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
