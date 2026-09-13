#!/usr/bin/env python3
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path


class OpenSSLResolutionError(RuntimeError):
    pass


def supports_ed25519(path: Path) -> bool:
    try:
        result = subprocess.run(
            [str(path), "genpkey", "-algorithm", "ED25519", "-out", os.devnull],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def homebrew_openssl_candidates(environment: Mapping[str, str]) -> tuple[Path, ...]:
    candidates = [
        Path("/opt/homebrew/opt/openssl@3/bin/openssl"),
        Path("/opt/homebrew/bin/openssl"),
        Path("/usr/local/opt/openssl@3/bin/openssl"),
        Path("/usr/local/bin/openssl"),
    ]
    brew_commands: list[Path] = []
    path_brew = shutil.which("brew", path=environment.get("PATH"))
    if path_brew is not None:
        brew_commands.append(Path(path_brew))
    brew_commands.extend((Path("/opt/homebrew/bin/brew"), Path("/usr/local/bin/brew")))
    for brew in dict.fromkeys(brew_commands):
        if not brew.is_file() or not os.access(brew, os.X_OK):
            continue
        try:
            result = subprocess.run(
                [str(brew), "--prefix", "openssl@3"],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode == 0 and result.stdout.strip():
            candidates.append(Path(result.stdout.strip()) / "bin/openssl")
    return tuple(dict.fromkeys(candidates))


def resolve_openssl(
    environment: Mapping[str, str] | None = None,
    *,
    homebrew_candidates: Iterable[Path] | None = None,
) -> Path:
    effective_environment = os.environ if environment is None else environment
    explicit = effective_environment.get("OPENSSL_BINARY")
    if explicit:
        explicit_path = Path(explicit).expanduser().absolute()
        if supports_ed25519(explicit_path):
            return explicit_path.resolve()
        raise OpenSSLResolutionError(
            f"OPENSSL_BINARY must point to OpenSSL with Ed25519 support: {explicit_path}"
        )

    path_candidate = shutil.which("openssl", path=effective_environment.get("PATH"))
    if path_candidate is not None and supports_ed25519(Path(path_candidate)):
        return Path(path_candidate).absolute().resolve()

    candidates = (
        homebrew_openssl_candidates(effective_environment)
        if homebrew_candidates is None
        else tuple(homebrew_candidates)
    )
    for candidate in dict.fromkeys(candidates):
        if supports_ed25519(candidate):
            return candidate.expanduser().absolute().resolve()
    raise OpenSSLResolutionError(
        "OpenSSL with Ed25519 support could not be found. Install OpenSSL 3 "
        "(on macOS: brew install openssl@3)."
    )


def main() -> int:
    try:
        print(resolve_openssl())
    except OpenSSLResolutionError as exc:
        print(f"openssl_tools.py: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
