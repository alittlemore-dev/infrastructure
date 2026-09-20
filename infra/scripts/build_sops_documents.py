#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path


AGE_RECIPIENT_PATTERN = re.compile(r"^age1[0-9a-z]{58}$")
SOURCE_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
VARIABLE_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build service-scoped SOPS documents from owner-only dotenv files."
    )
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--repo-dir", required=True, type=Path)
    parser.add_argument("--sops-binary", default="sops")
    parser.add_argument(
        "--source-env",
        action="append",
        required=True,
        metavar="DOCUMENT=PATH",
    )
    parser.add_argument("--age-recipient", action="append", required=True)
    return parser.parse_args()


def parse_source_arguments(raw_sources: list[str]) -> dict[str, Path]:
    sources: dict[str, Path] = {}
    for raw_source in raw_sources:
        if "=" not in raw_source:
            raise ValueError("Every --source-env must use DOCUMENT=/absolute/path syntax.")
        document_name, raw_path = raw_source.split("=", 1)
        if SOURCE_NAME_PATTERN.fullmatch(document_name) is None or not raw_path:
            raise ValueError("Every --source-env must use DOCUMENT=/absolute/path syntax.")
        if document_name in sources:
            raise ValueError(f"Secret source for {document_name} was supplied more than once.")
        source_path = Path(raw_path)
        if not source_path.is_absolute():
            raise ValueError(f"Secret source for {document_name} must be an absolute path.")
        sources[document_name] = source_path
    return sources


def load_dotenv_source(path: Path, document_name: str) -> dict[str, str]:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ValueError(f"Secret source for {document_name} is not readable: {path}") from exc
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"Secret source for {document_name} must be a regular file.")
    if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
        raise ValueError(f"Secret source for {document_name} must be owner-only.")

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"Secret source for {document_name} is not readable: {path}") from exc

    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(lines, 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(
                f"Secret source for {document_name} has an invalid line {line_number}."
            )
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if VARIABLE_NAME_PATTERN.fullmatch(name) is None:
            raise ValueError(
                f"Secret source for {document_name} has an invalid line {line_number}."
            )
        if name in values:
            raise ValueError(f"Secret source for {document_name} declares {name} more than once.")
        if value[:1] in {"'", '"'}:
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError(
                    f"Secret source for {document_name} has an invalid line {line_number}."
                )
            value = value[1:-1]
        elif value[-1:] in {"'", '"'}:
            raise ValueError(
                f"Secret source for {document_name} has an invalid line {line_number}."
            )
        values[name] = value
    return values


def resolve_repo_path(repo_dir: Path, relative_path: str) -> Path:
    resolved_repo = repo_dir.resolve()
    candidate = (resolved_repo / relative_path).resolve()
    try:
        candidate.relative_to(resolved_repo)
    except ValueError as exc:
        raise ValueError(f"Secret document escapes the repository: {relative_path}") from exc
    return candidate


def encrypt_document(
    sops_binary: str,
    recipients: list[str],
    values: dict[str, str],
    document_name: str,
) -> str:
    environment = os.environ.copy()
    try:
        result = subprocess.run(
            [
                sops_binary,
                "--config",
                "/dev/null",
                "encrypt",
                "--age",
                ",".join(recipients),
                "--input-type",
                "json",
                "--output-type",
                "yaml",
                "/dev/stdin",
            ],
            input=json.dumps(values),
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
    except OSError as exc:
        raise ValueError(f"Could not execute SOPS for {document_name}: {exc}") from exc
    if result.returncode != 0:
        raise ValueError(f"SOPS could not encrypt {document_name}.")
    if not result.stdout:
        raise ValueError(f"SOPS returned an empty document for {document_name}.")
    return result.stdout


def write_public_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError(f"Encrypted output must be a regular file: {path}")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    os.fchmod(descriptor, 0o644)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output_file:
        output_file.write(content)


def render_sops_config(recipients: list[str]) -> str:
    lines = [
        "creation_rules:",
        "  - path_regex: ^secrets/(platform|personal-workspace|competency-trainer|auth-api|i18n)/production\\.sops\\.yaml$",
        "    age:",
    ]
    lines.extend(f"      - {recipient}" for recipient in recipients)
    lines.append("")
    return "\n".join(lines)


def build_documents(args: argparse.Namespace) -> None:
    recipients = list(dict.fromkeys(args.age_recipient))
    if any(AGE_RECIPIENT_PATTERN.fullmatch(recipient) is None for recipient in recipients):
        raise ValueError("Every age recipient must be a valid age1 public recipient.")
    if len(recipients) < 2:
        raise ValueError("At least two distinct age recipients are required for server and recovery.")
    source_paths = parse_source_arguments(args.source_env)

    raw_manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if not isinstance(raw_manifest, dict) or set(raw_manifest) != {"documents"}:
        raise ValueError("Manifest must contain exactly the documents field.")
    documents = raw_manifest["documents"]
    if not isinstance(documents, list) or not documents:
        raise ValueError("Manifest documents must be a non-empty list.")

    encrypted_documents: list[tuple[Path, str]] = []
    document_names: set[str] = set()
    for document in documents:
        if not isinstance(document, dict) or set(document) != {"name", "path", "secrets"}:
            raise ValueError("Every secret document must contain name, path, and secrets.")
        document_name = document["name"]
        relative_path = document["path"]
        specs = document["secrets"]
        if not isinstance(document_name, str) or not document_name:
            raise ValueError("Secret document name must be a non-empty string.")
        if document_name in document_names:
            raise ValueError(f"Secret document {document_name} was declared more than once.")
        document_names.add(document_name)
        if document_name not in source_paths:
            raise ValueError(f"Missing --source-env for secret document {document_name}.")
        if not isinstance(relative_path, str) or not relative_path:
            raise ValueError(f"{document_name} path must be a non-empty string.")
        if not isinstance(specs, list) or not specs:
            raise ValueError(f"{document_name} secrets must be a non-empty list.")
        source_values = load_dotenv_source(source_paths[document_name], document_name)

        native_values: dict[str, str] = {}
        for spec in specs:
            required_fields = {
                "name",
                "target",
                "composeVariable",
                "allowEmpty",
            }
            optional_fields = {"encoding"}
            if (
                not isinstance(spec, dict)
                or not required_fields <= set(spec)
                or set(spec) - required_fields - optional_fields
            ):
                raise ValueError(f"{document_name} contains an invalid secret specification.")
            native_name = spec["name"]
            allow_empty = spec["allowEmpty"]
            if not isinstance(native_name, str):
                raise ValueError(f"{document_name} contains an invalid secret name.")
            if not isinstance(allow_empty, bool):
                raise ValueError(f"{document_name}.{native_name}.allowEmpty must be boolean.")
            if spec.get("encoding") not in (None, "pem"):
                raise ValueError(f"{document_name}.{native_name}.encoding is not supported.")
            if native_name not in source_values:
                raise ValueError(f"Missing secret in local source: {document_name}.{native_name}")
            value = source_values[native_name]
            if not value and not allow_empty:
                raise ValueError(f"Empty required secret in local source: {document_name}.{native_name}")
            native_values[native_name] = value

        encrypted_documents.append(
            (
                resolve_repo_path(args.repo_dir, relative_path),
                encrypt_document(args.sops_binary, recipients, native_values, document_name),
            )
        )

    unexpected_sources = sorted(set(source_paths) - document_names)
    if unexpected_sources:
        raise ValueError(f"Secret sources do not exist in the manifest: {', '.join(unexpected_sources)}")

    for path, content in encrypted_documents:
        write_public_file(path, content)
    write_public_file(args.repo_dir / ".sops.yaml", render_sops_config(recipients))


def main() -> int:
    args = parse_args()
    try:
        build_documents(args)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"build_sops_documents.py: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
