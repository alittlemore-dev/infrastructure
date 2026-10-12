#!/usr/bin/env python3
"""Container liveness is independent of successful collection/delivery."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from infra_backup.json_io import decode, object_value
from infra_backup.status.projection import required_timestamp


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        data = object_value(decode((args.state_dir / "status.json").read_text()))
        value = datetime.fromisoformat(required_timestamp(data["worker_heartbeat_at"]).replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - value).total_seconds()
        return 0 if -60 <= age <= 120 else 1
    except (OSError, ValueError, TypeError, KeyError):
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
