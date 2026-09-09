#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: list_compose_secret_variables.py MANIFEST", file=sys.stderr)
        return 2
    try:
        manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
        for document in manifest["documents"]:
            for secret in document["secrets"]:
                print(secret["composeVariable"])
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"list_compose_secret_variables.py: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
