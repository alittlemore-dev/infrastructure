#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
destination="${1:?destination path is required}"

exec python3 "$script_dir/quality_tools.py" install age-keygen "$destination"
