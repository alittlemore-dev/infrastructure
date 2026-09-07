#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path


def resolve_external_directory(repository: Path, candidate: Path) -> Path:
    if not candidate.is_absolute():
        raise ValueError("path must be absolute")

    repository = repository.resolve(strict=True)
    candidate = candidate.resolve(strict=False)
    if candidate == Path(candidate.anchor):
        raise ValueError("filesystem root is not an allowed output directory")
    if candidate == repository or repository in candidate.parents:
        raise ValueError("path must resolve outside the repository")
    return candidate


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Resolve an absolute PKI directory and reject repository-local targets.",
    )
    parser.add_argument("repository", type=Path)
    parser.add_argument("candidate", type=Path)
    args = parser.parse_args()

    try:
        print(resolve_external_directory(args.repository, args.candidate))
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
