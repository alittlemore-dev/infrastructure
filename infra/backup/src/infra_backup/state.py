"""Durable private state and a lock shared by scheduled and manual runs."""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Mapping, cast

from infra_backup.contracts import Artifact, Attempt, PendingRun, SourceResult, SourceType, WorkerState
from infra_backup.json_io import JsonObject, decode, list_value, object_value, string_value
from infra_backup.status.projection import identifier, integer, required_timestamp, safe_error, sanitize_projection

HISTORY_LIMIT = 32


def private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or not path.is_dir() or path.stat().st_uid != os.geteuid():
        raise ValueError("state directory must be owned by the backup process")
    path.chmod(0o700)


def write_json_atomic(path: Path, payload: Mapping[str, object]) -> None:
    private_directory(path.parent)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as output:
            os.fchmod(output.fileno(), 0o600)
            json.dump(payload, output, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path: Path, default: Mapping[str, object] | None = None) -> JsonObject:
    try:
        if path.is_symlink():
            raise ValueError("state must be a regular file")
        with path.open() as source:
            value = decode(source.read())
        if not isinstance(value, dict):
            raise ValueError("invalid state")
        return object_value(value)
    except FileNotFoundError:
        if default is None:
            raise
        return dict(default)


class LockBusy(Exception):
    pass


@contextmanager
def run_lock(state_dir: Path) -> Iterator[None]:
    private_directory(state_dir)
    descriptor = os.open(state_dir / "run.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LockBusy("a backup is already running") from exc
        yield
    finally:
        os.close(descriptor)


def append_attempt(state: WorkerState, attempt: Attempt) -> None:
    state["attempts"] = [*state.get("attempts", []), attempt][-HISTORY_LIMIT:]


def decode_worker_state(raw: JsonObject) -> WorkerState:
    """Validate persisted private state before it can drive deliveries or deletion."""
    projection = sanitize_projection(raw)
    sources: list[SourceResult] = []
    for value in list_value(raw["sources"]):
        item = object_value(value)
        kind, status = item.get("type"), item.get("status")
        if kind not in {"postgres", "valkey", "s3", "config"} or status not in {"success", "failed"}:
            raise ValueError("invalid persisted source")
        artifacts: list[Artifact] = []
        for value in list_value(item["artifacts"]):
            artifact = object_value(value)
            path = string_value(artifact["path"])
            relative = Path(path)
            digest = string_value(artifact["sha256"])
            if relative.is_absolute() or str(relative) != path or ".." in relative.parts or "\\" in path or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError("invalid persisted artifact")
            artifacts.append({"path": path, "sha256": digest, "bytes": integer(artifact["bytes"])})
        result: SourceResult = {"id": identifier(item["id"]), "type": cast(SourceType, kind),
            "status": "success" if status == "success" else "failed", "complete": item["complete"] is True,
            "artifacts": artifacts}
        for name in ("started_at", "finished_at"):
            if name in item:
                value = required_timestamp(item[name])
                if name == "started_at":
                    result["started_at"] = value
                else:
                    result["finished_at"] = value
        if "bytes" in item:
            result["bytes"] = integer(item["bytes"])
        failure = safe_error(item.get("error"))
        if failure:
            result["error"] = failure
        sources.append(result)
    pending: list[PendingRun] = []
    for value in list_value(raw["pending"]):
        item = object_value(value)
        pending.append({"run_id": identifier(item["run_id"]), "started_at": required_timestamp(item["started_at"]),
            "completed_at": required_timestamp(item["completed_at"]), "bytes": integer(item["bytes"]),
            "destinations": [identifier(dest) for dest in list_value(item["destinations"])]})
    if len({item["run_id"] for item in pending}) != len(pending):
        raise ValueError("duplicate pending run")
    attempts: list[Attempt] = []
    for value in list_value(raw["attempts"])[-HISTORY_LIMIT:]:
        item = object_value(value)
        if type(item["success"]) is not bool:
            raise ValueError("invalid attempt success")
        attempts.append({"run_id": identifier(item["run_id"]), "started_at": required_timestamp(item["started_at"]),
            "finished_at": required_timestamp(item["finished_at"]), "success": item["success"] is True,
            "error": safe_error(item.get("error"))})
    state: WorkerState = {"schema_version": 1, "generated_at": projection["generated_at"],
        "freshness_threshold_seconds": projection["freshness_threshold_seconds"], "run_state": projection["run_state"],
        "current_run": projection["current_run"], "last_error": projection["last_error"],
        "sources": sources, "destinations": projection["destinations"], "pending": pending, "attempts": attempts}
    for name in ("worker_heartbeat_at", "last_attempt_at", "last_success_at", "next_due_at"):
        value = raw.get(name)
        if value is not None:
            normalized = required_timestamp(value)
            if name == "worker_heartbeat_at":
                state["worker_heartbeat_at"] = normalized
            elif name == "last_attempt_at":
                state["last_attempt_at"] = normalized
            elif name == "last_success_at":
                state["last_success_at"] = normalized
            else:
                state["next_due_at"] = normalized
    retry = raw.get("next_retry_at")
    state["next_retry_at"] = required_timestamp(retry) if retry else None
    if raw.get("projection_error") is not None:
        failure = safe_error(raw["projection_error"])
        if failure:
            state["projection_error"] = failure
    state["collection_error"] = safe_error(raw.get("collection_error"))
    state["last_restore_verified_at"] = projection["last_restore_verified_at"]
    return state
