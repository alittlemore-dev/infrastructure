#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from render_runtime_config import parse_env_file


def main() -> int:
    parser = argparse.ArgumentParser(description="Read one value from a validated dotenv file.")
    parser.add_argument("path", type=Path)
    parser.add_argument("name")
    args = parser.parse_args()

    try:
        values = parse_env_file(args.path)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    value = values.get(args.name, "")
    if not value:
        parser.error(f"{args.path} must declare a non-empty {args.name} value.")
    print(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
