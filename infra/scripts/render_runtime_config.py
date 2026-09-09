#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any


VARIABLE_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate service-scoped config files and render Compose runtime aliases."
    )
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--repo-dir", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    if not args.validate_only and args.output is None:
        parser.error("--output is required unless --validate-only is used")
    if args.validate_only and args.output is not None:
        parser.error("--output cannot be combined with --validate-only")
    return args


def quote_env_value(value: str) -> str:
    escaped_value = (
        value.replace("\\", "\\\\")
        .replace("\r", "\\r")
        .replace("\n", "\\n")
        .replace("\t", "\\t")
        .replace('"', '\\"')
        .replace("$", "\\$")
        .replace("`", "\\`")
    )
    return f'"{escaped_value}"'


def write_private_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise OSError(f"Refusing to replace symlinked private file: {path}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output_file:
        output_file.write(content)


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"{path}:{line_number} must use NAME=value syntax.")
        name, value = line.split("=", maxsplit=1)
        name = name.strip()
        value = value.strip()
        if VARIABLE_NAME_PATTERN.fullmatch(name) is None:
            raise ValueError(f"{path}:{line_number} has an invalid variable name: {name}")
        if name in values:
            raise ValueError(f"{path} declares {name} more than once.")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[name] = value
    return values


def require_string(value: Any, description: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{description} must be a non-empty string.")
    return value


def resolve_repo_path(repo_dir: Path, relative_path: str) -> Path:
    candidate = (repo_dir / relative_path).resolve()
    resolved_repo = repo_dir.resolve()
    try:
        candidate.relative_to(resolved_repo)
    except ValueError as exc:
        raise ValueError(f"Config path escapes the repository: {relative_path}") from exc
    return candidate


def render_runtime_config(manifest_path: Path, repo_dir: Path) -> str:
    raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw_manifest, dict) or set(raw_manifest) != {"configs"}:
        raise ValueError("Manifest must contain exactly the configs field.")
    raw_configs = raw_manifest["configs"]
    if not isinstance(raw_configs, list) or not raw_configs:
        raise ValueError("Manifest configs must be a non-empty list.")

    runtime_values: dict[str, str] = {}
    seen_config_names: set[str] = set()
    for raw_config in raw_configs:
        if not isinstance(raw_config, dict) or set(raw_config) != {
            "name",
            "path",
            "variables",
            "runtimeAliases",
        }:
            raise ValueError(
                "Each config must contain exactly name, path, variables, and runtimeAliases."
            )
        config_name = require_string(raw_config["name"], "Config name")
        if config_name in seen_config_names:
            raise ValueError(f"Duplicate config name: {config_name}")
        seen_config_names.add(config_name)
        relative_path = require_string(raw_config["path"], f"{config_name} path")

        raw_variables = raw_config["variables"]
        if not isinstance(raw_variables, list) or not all(
            isinstance(name, str) and VARIABLE_NAME_PATTERN.fullmatch(name)
            for name in raw_variables
        ):
            raise ValueError(f"{config_name} variables must contain valid variable names.")
        if len(raw_variables) != len(set(raw_variables)):
            raise ValueError(f"{config_name} variables must be unique.")

        raw_aliases = raw_config["runtimeAliases"]
        if not isinstance(raw_aliases, dict) or not all(
            isinstance(source, str)
            and source in raw_variables
            and isinstance(target, str)
            and VARIABLE_NAME_PATTERN.fullmatch(target)
            for source, target in raw_aliases.items()
        ):
            raise ValueError(f"{config_name} runtimeAliases are invalid.")

        config_path = resolve_repo_path(repo_dir, relative_path)
        config_values = parse_env_file(config_path)
        expected_names = set(raw_variables)
        actual_names = set(config_values)
        missing_names = sorted(expected_names - actual_names)
        unexpected_names = sorted(actual_names - expected_names)
        if missing_names:
            raise ValueError(f"{config_name} is missing variables: {', '.join(missing_names)}")
        if unexpected_names:
            raise ValueError(
                f"{config_name} has unexpected variables: {', '.join(unexpected_names)}"
            )
        empty_names = sorted(name for name, value in config_values.items() if value == "")
        if empty_names:
            raise ValueError(f"{config_name} has empty variables: {', '.join(empty_names)}")
        for source_name, runtime_name in raw_aliases.items():
            if runtime_name in runtime_values:
                raise ValueError(f"Duplicate runtime variable: {runtime_name}")
            runtime_values[runtime_name] = config_values[source_name]

    lines = [
        "# Generated by infra/scripts/render_runtime_config.py.",
        "# Contains only public Compose aliases; do not edit.",
    ]
    lines.extend(f"{name}={quote_env_value(value)}" for name, value in runtime_values.items())
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    try:
        rendered = render_runtime_config(args.manifest, args.repo_dir)
        if not args.validate_only:
            assert args.output is not None
            write_private_file(args.output, rendered)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"render_runtime_config.py: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
