#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

from quality_tools import ToolError, resolve_tool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify that every manifest SOPS document is encrypted and decryptable."
    )
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--repo-dir", required=True, type=Path)
    parser.add_argument("--cache-dir", required=True, type=Path)
    parser.add_argument("--age-key-file", required=True, type=Path)
    return parser.parse_args()


def validate_age_key(path: Path, repo_dir: Path) -> Path:
    if not path.is_absolute():
        raise ValueError("SOPS age key must be an absolute path.")
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ValueError(f"SOPS age key is not readable: {path}") from exc
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("SOPS age key must be a regular non-symlinked file.")
    if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
        raise ValueError("SOPS age key must be owner-only and owned by the current user.")
    try:
        path.resolve().relative_to(repo_dir.resolve())
    except ValueError:
        return path
    raise ValueError("SOPS age key must live outside the repository.")


def load_document_paths(manifest: Path, repo_dir: Path) -> list[tuple[str, Path]]:
    raw: Any = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("documents"), list):
        raise ValueError("Secret manifest must contain a documents list.")
    resolved_repo = repo_dir.resolve()
    documents: list[tuple[str, Path]] = []
    for document in raw["documents"]:
        if not isinstance(document, dict):
            raise ValueError("Every secret manifest document must be an object.")
        name = document.get("name")
        relative_path = document.get("path")
        if not isinstance(name, str) or not name or not isinstance(relative_path, str):
            raise ValueError("Every secret manifest document needs non-empty name and path fields.")
        path = (resolved_repo / relative_path).resolve()
        try:
            path.relative_to(resolved_repo)
        except ValueError as exc:
            raise ValueError(f"Secret document escapes the repository: {relative_path}") from exc
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Secret document must be a regular file: {relative_path}")
        documents.append((relative_path, path))
    if not documents:
        raise ValueError("Secret manifest must declare at least one document.")
    return documents


def verify_document(sops_binary: Path, age_key: Path, relative_path: str, path: Path) -> None:
    environment = os.environ.copy()
    environment["SOPS_AGE_KEY_FILE"] = str(age_key)
    status_result = subprocess.run(
        [str(sops_binary), "filestatus", str(path)],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if status_result.returncode != 0:
        raise ValueError(f"Could not read encrypted status for {relative_path}.")
    try:
        encrypted = json.loads(status_result.stdout).get("encrypted") is True
    except (AttributeError, json.JSONDecodeError) as exc:
        raise ValueError(f"SOPS returned invalid status for {relative_path}.") from exc
    if not encrypted:
        raise ValueError(f"Secret document is not encrypted: {relative_path}")
    decrypt_result = subprocess.run(
        [str(sops_binary), "decrypt", str(path)],
        env=environment,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=30,
    )
    if decrypt_result.returncode != 0:
        raise ValueError(f"Secret document could not be decrypted: {relative_path}")


def main() -> int:
    args = parse_args()
    try:
        repo_dir = args.repo_dir.resolve()
        age_key = validate_age_key(args.age_key_file, repo_dir)
        documents = load_document_paths(args.manifest, repo_dir)
        sops_binary = resolve_tool("sops", args.cache_dir, environment=os.environ)
        for relative_path, path in documents:
            verify_document(sops_binary, age_key, relative_path, path)
            print(f"Verified {relative_path}")
    except (OSError, ToolError, ValueError, json.JSONDecodeError) as exc:
        print(f"verify_sops_documents.py: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
