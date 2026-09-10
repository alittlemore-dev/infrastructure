#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


DEFAULT_GITHUB_API_BASE = "https://api.github.com"
DEFAULT_ALPINE_APORTS_BASE = "https://raw.githubusercontent.com/alpinelinux/aports"
VERSION_PATTERN = re.compile(r"^[0-9][0-9A-Za-z._+~-]*$")


class StatusError(RuntimeError):
    pass


@dataclass(frozen=True)
class DependencyStatus:
    name: str
    current: str
    latest: str

    @property
    def is_current(self) -> bool:
        return self.current == self.latest


def parse_string_assignment(source: str, name: str, source_name: str) -> str:
    try:
        module = ast.parse(source, filename=source_name)
    except SyntaxError as exc:
        raise StatusError(f"could not parse {source_name}: {exc.msg}") from exc

    for statement in module.body:
        value_node: ast.expr | None = None
        if isinstance(statement, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name
            for target in statement.targets
        ):
            value_node = statement.value
        elif (
            isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and statement.target.id == name
        ):
            value_node = statement.value
        if isinstance(value_node, ast.Constant) and isinstance(value_node.value, str):
            return value_node.value

    raise StatusError(f"{source_name} does not define string constant {name}")


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise StatusError(f"could not read {path}: {exc}") from exc


def fetch_text(url: str, *, github_token: str | None = None) -> str:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "alittlemore-infra-dependencies-status",
    }
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.read().decode("utf-8")
    except (OSError, UnicodeError, urllib.error.URLError) as exc:
        raise StatusError(f"could not fetch {url}: {exc}") from exc


def github_latest_version(
    repository: str,
    *,
    api_base: str,
    github_token: str | None,
) -> str:
    url = f"{api_base.rstrip('/')}/repos/{repository}/releases/latest"
    try:
        document = json.loads(fetch_text(url, github_token=github_token))
        tag_name = document["tag_name"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise StatusError(f"invalid latest-release response for {repository}") from exc
    if not isinstance(tag_name, str):
        raise StatusError(f"invalid latest-release tag for {repository}")
    version = tag_name.removeprefix("v")
    if not VERSION_PATTERN.fullmatch(version):
        raise StatusError(f"invalid latest-release version for {repository}: {tag_name!r}")
    return version


def parse_alpine_branch_and_openssl_pin(dockerfile: str) -> tuple[str, str]:
    alpine_match = re.search(
        r"^FROM\s+(?:[^\s/]+/)*alpine:(?P<series>[0-9]+\.[0-9]+)(?:\.[0-9]+)?(?:@\S+)?(?:\s|$)",
        dockerfile,
        flags=re.MULTILINE,
    )
    openssl_match = re.search(r"\bopenssl=(?P<version>[0-9][^\s\\]*)", dockerfile)
    if alpine_match is None:
        raise StatusError("cert-sync Dockerfile does not pin a supported Alpine image tag")
    if openssl_match is None:
        raise StatusError("cert-sync Dockerfile does not pin the openssl package")
    return f"{alpine_match.group('series')}-stable", openssl_match.group("version")


def parse_apkbuild_openssl_version(apkbuild: str) -> str:
    values: dict[str, str] = {}
    for name in ("pkgver", "pkgrel"):
        match = re.search(
            rf"^{name}=(?:\"(?P<double>[^\"]+)\"|'(?P<single>[^']+)'|(?P<plain>[^\s#]+))\s*$",
            apkbuild,
            flags=re.MULTILINE,
        )
        if match is None:
            raise StatusError(f"Alpine openssl APKBUILD does not define {name}")
        value = next(group for group in match.groups() if group is not None)
        if not VERSION_PATTERN.fullmatch(value):
            raise StatusError(f"Alpine openssl APKBUILD has invalid {name}: {value!r}")
        values[name] = value
    return f"{values['pkgver']}-r{values['pkgrel']}"


def status_for_github_release(
    *,
    name: str,
    current: str,
    repository: str,
    api_base: str,
    github_token: str | None,
) -> DependencyStatus:
    return DependencyStatus(
        name=name,
        current=current,
        latest=github_latest_version(
            repository,
            api_base=api_base,
            github_token=github_token,
        ),
    )


def collect_statuses(
    *,
    repo_dir: Path,
    github_api_base: str,
    alpine_aports_base: str,
    github_token: str | None,
) -> tuple[list[DependencyStatus], list[tuple[str, StatusError]]]:
    statuses: list[DependencyStatus] = []
    errors: list[tuple[str, StatusError]] = []
    quality_tools_source = read_text(repo_dir / "infra/scripts/quality_tools.py")

    for name, constant, repository in (
        ("sops", "SOPS_VERSION", "getsops/sops"),
        ("age", "AGE_VERSION", "FiloSottile/age"),
    ):
        try:
            current = parse_string_assignment(
                quality_tools_source,
                constant,
                "infra/scripts/quality_tools.py",
            )
            statuses.append(
                status_for_github_release(
                    name=name,
                    current=current,
                    repository=repository,
                    api_base=github_api_base,
                    github_token=github_token,
                )
            )
        except StatusError as exc:
            errors.append((name, exc))

    try:
        dockerfile = read_text(repo_dir / "infra/cert-sync/Dockerfile")
        alpine_branch, current_openssl = parse_alpine_branch_and_openssl_pin(dockerfile)
        apkbuild_url = (
            f"{alpine_aports_base.rstrip('/')}/{alpine_branch}/main/openssl/APKBUILD"
        )
        latest_openssl = parse_apkbuild_openssl_version(fetch_text(apkbuild_url))
        statuses.append(
            DependencyStatus(
                name="openssl",
                current=current_openssl,
                latest=latest_openssl,
            )
        )
    except StatusError as exc:
        errors.append(("openssl", exc))

    return statuses, errors


def parse_arguments(arguments: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check manually maintained dependency pins against upstream releases."
    )
    parser.add_argument("--repo-dir", type=Path, default=Path.cwd())
    parser.add_argument("--github-api-base", default=DEFAULT_GITHUB_API_BASE)
    parser.add_argument("--alpine-aports-base", default=DEFAULT_ALPINE_APORTS_BASE)
    parser.add_argument(
        "--check",
        action="store_true",
        help="return status 1 when at least one update is available",
    )
    return parser.parse_args(arguments)


def main(arguments: list[str] | None = None) -> int:
    options = parse_arguments(arguments)
    try:
        statuses, errors = collect_statuses(
            repo_dir=options.repo_dir.resolve(),
            github_api_base=options.github_api_base,
            alpine_aports_base=options.alpine_aports_base,
            github_token=os.environ.get("GITHUB_TOKEN"),
        )
    except StatusError as exc:
        print(f"ERROR   dependencies {exc}", file=sys.stderr)
        return 2

    for status in statuses:
        if status.is_current:
            print(f"OK      {status.name} {status.current}")
        else:
            print(f"UPDATE  {status.name} {status.current} -> {status.latest}")
    for name, error in errors:
        print(f"ERROR   {name} {error}", file=sys.stderr)

    if errors:
        return 2
    if options.check and any(not status.is_current for status in statuses):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
