#!/usr/bin/env python3
"""Explicit backup operations and secret-free status inspection."""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from infra_backup.contracts import ErrorCode, MAX_MANIFEST_BYTES
from infra_backup.json_io import decode, object_value, list_value

from infra_backup.config import _destination, _json, load_config
from infra_backup.state import LockBusy
from infra_backup.status.projection import ERROR_MESSAGES, read_healthcheck
from infra_backup.storage import _manifest, _open, _verify_response, download
from infra_backup.worker import BackupWorker


def verify(directory: Path) -> dict[str, str | int | bool]:
    with _open(directory, "manifest.json") as stream:
        raw = stream.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError("manifest exceeds limit")
    document = object_value(decode(raw))
    manifest = _manifest(document, committed="delivery_completed_at" in document)
    deadline = time.monotonic() + 3600
    for artifact in manifest["artifacts"]:
        with _open(directory, artifact["path"]) as stream:
            _verify_response({"Body": stream}, artifact, deadline)
    return {"run_id": manifest["run_id"], "complete": True,
            "total_bytes": sum(item["bytes"] for item in manifest["artifacts"])}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    for flag in ("config", "destinations", "state-dir", "work-dir", "projection-dir"):
        run.add_argument("--" + flag, type=Path, required=True)
    for command in ("status", "health"):
        read = commands.add_parser(command)
        read.add_argument("--state-dir", type=Path, required=True)
        read.add_argument("--projection-dir", type=Path)
        read.add_argument("--json", action="store_true")
    fetch = commands.add_parser("download")
    fetch.add_argument("--destination", required=True)
    fetch.add_argument("--run-id", required=True)
    fetch.add_argument("--output-dir", type=Path, required=True)
    fetch.add_argument("--destinations", type=Path, required=True)
    check = commands.add_parser("verify")
    check.add_argument("--directory", type=Path, required=True)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command in {"status", "health"}:
            system = read_healthcheck(args.projection_dir or args.state_dir, datetime.now(timezone.utc))["systems"][0]
            if args.command == "status" and args.json:
                print(json.dumps(system["backup"] or {"schema_version": 1, "status": system["status"]}, sort_keys=True))
            elif args.json:
                print(json.dumps({"status": system["status"], "observed_at": system["observed_at"]}, sort_keys=True))
            else:
                print("Backup status: " + system["status"])
            return 0 if args.command == "status" or system["status"] == "healthy" else 1
        if args.command == "run":
            metadata = os.environ.get("BACKUP_DEPLOYMENT_METADATA")
            worker = BackupWorker(load_config(args.config, args.destinations), args.state_dir,
                                  args.work_dir, args.projection_dir,
                                  deployment_metadata_path=Path(metadata) if metadata else None)
            success = worker.run_once()
            print("Backup completed." if success else "Backup failed; inspect backup status.")
            return 0 if success else 1
        if args.command == "download":
            inventory = _json(args.destinations)
            if set(inventory) != {"destinations"} or not isinstance(inventory["destinations"], list):
                raise ValueError("invalid destination inventory")
            destinations = [_destination(object_value(item)) for item in list_value(inventory["destinations"])]
            if len({item.id for item in destinations}) != len(destinations):
                raise ValueError("duplicate destinations")
            destination = next(item for item in destinations if item.id == args.destination)
            download(destination, args.run_id, args.output_dir)
            print("Backup downloaded and checksums verified.")
            return 0
        print(json.dumps(verify(args.directory), sort_keys=True))
        return 0
    except LockBusy:
        print("Backup busy; another run holds the lock.")
        return 2
    except Exception:
        code: ErrorCode = "configuration_invalid" if args.command == "run" else "delivery_failed"
        print(json.dumps({"error": {"code": code, "message": ERROR_MESSAGES[code]}}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
