"""Bounded, fail-closed reader of the public projection contract."""
from __future__ import annotations

import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, cast

from infra_backup.contracts import (
    BackupProjection, BackupSystem, CheckpointRole, CurrentRun, DestinationStatus,
    ErrorCode, HealthcheckEnvelope, HealthStatus, ProjectionSource, RetainedSet,
    RetainedSetsEnvelope, RunPhase, RunState, SafeError,
)
from infra_backup.json_io import decode, list_value, object_value

MAX_PROJECTION_BYTES = 1024 * 1024
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$")
ROLES: set[CheckpointRole] = {"latest", "weekly_current", "weekly_previous"}
ERROR_MESSAGES: dict[ErrorCode, str] = {
    "source_failed": "A source could not be collected.",
    "delivery_failed": "A destination could not receive the backup.",
    "cleanup_failed": "Backup cleanup needs attention.",
    "delivery_evicted": "A pending delivery exceeded the storage budget.",
    "interrupted": "The previous backup was interrupted.",
    "projection_failed": "Backup status could not be published.",
    "storage_full": "Backup storage is full.",
    "configuration_invalid": "Backup configuration needs attention.",
}


def timestamp(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError("invalid timestamp")
    instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if instant.tzinfo is None:
        raise ValueError("timezone is required")
    return instant.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def required_timestamp(value: object) -> str:
    result = timestamp(value)
    if result is None:
        raise ValueError("timestamp is required")
    return result


def identifier(value: object) -> str:
    if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
        raise ValueError("invalid identifier")
    return value


def integer(value: object, minimum: int = 0, maximum: int = 2**63 - 1) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("invalid number")
    return value


def safe_error(value: object) -> SafeError | None:
    if value is None:
        return None
    raw = object_value(value)
    code = raw.get("code")
    if not isinstance(code, str) or code not in ERROR_MESSAGES:
        raise ValueError("invalid error")
    error_code = cast(ErrorCode, code)
    result: SafeError = {"code": error_code, "message": ERROR_MESSAGES[error_code]}
    if raw.get("source_id") is not None:
        result["source_id"] = identifier(raw["source_id"])
    if raw.get("destination_id") is not None:
        result["destination_id"] = identifier(raw["destination_id"])
    return result


def current_run(value: object) -> CurrentRun | None:
    if value is None:
        return None
    raw = object_value(value)
    phase = raw.get("phase")
    if phase not in {"collecting", "delivering", "retrying", "pruning"}:
        raise ValueError("invalid phase")
    return {"id": identifier(raw["id"]), "phase": cast(RunPhase, phase),
            "started_at": required_timestamp(raw.get("started_at"))}


def destination_status(value: object) -> DestinationStatus:
    raw = object_value(value)
    required = raw.get("required")
    if type(required) is not bool:
        raise ValueError("required destination flag is required")
    sets = list_value(raw.get("retained_sets", []))
    if len(sets) > 3:
        raise ValueError("invalid retained sets")
    retained: list[RetainedSet] = []
    run_ids: set[str] = set()
    claimed: set[CheckpointRole] = set()
    for value in sets:
        entry = object_value(value)
        run_id = identifier(entry["run_id"])
        roles_raw = list_value(entry["roles"])
        if not roles_raw or any(not isinstance(role, str) or role not in ROLES for role in roles_raw):
            raise ValueError("invalid checkpoint roles")
        roles = cast(list[CheckpointRole], roles_raw)
        if run_id in run_ids or len(set(roles)) != len(roles) or claimed.intersection(roles) or entry.get("complete") is not True:
            raise ValueError("invalid retained set")
        run_ids.add(run_id)
        claimed.update(roles)
        retained.append({"run_id": run_id, "roles": roles,
            "completed_at": required_timestamp(entry["completed_at"]),
            "total_bytes": integer(entry["total_bytes"]), "complete": True})
    return {"id": identifier(raw["id"]), "required": required, "retained_sets": retained,
        "last_success_at": timestamp(raw.get("last_success_at")),
        "pending_count": integer(raw.get("pending_count", 0)),
        "delivery_gap_count": integer(raw.get("delivery_gap_count", 0)),
        "last_error": safe_error(raw.get("last_error")), "cleanup_error": safe_error(raw.get("cleanup_error"))}


def sanitize_projection(data: Mapping[str, object]) -> BackupProjection:
    if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise ValueError("unsupported schema")
    run_state = data.get("run_state")
    if run_state not in {"idle", "running", "failed", "initializing"}:
        raise ValueError("invalid run state")
    raw_destinations = list_value(data.get("destinations"))
    if len(raw_destinations) > 32:
        raise ValueError("invalid destinations")
    destinations = [destination_status(value) for value in raw_destinations]
    if len({item["id"] for item in destinations}) != len(destinations):
        raise ValueError("duplicate destination")
    raw_sources = list_value(data.get("sources", []))
    if len(raw_sources) > 64:
        raise ValueError("invalid sources")
    sources: list[ProjectionSource] = []
    for value in raw_sources:
        raw = object_value(value)
        complete = raw.get("complete")
        if type(complete) is not bool:
            raise ValueError("invalid source completion")
        sources.append({"id": identifier(raw["id"]), "complete": complete, "error": safe_error(raw.get("error"))})
    return {"schema_version": 1, "generated_at": required_timestamp(data.get("generated_at")),
        "worker_heartbeat_at": timestamp(data.get("worker_heartbeat_at")),
        "last_attempt_at": timestamp(data.get("last_attempt_at")), "last_success_at": timestamp(data.get("last_success_at")),
        "next_due_at": timestamp(data.get("next_due_at")), "last_restore_verified_at": timestamp(data.get("last_restore_verified_at")),
        "freshness_threshold_seconds": integer(data.get("freshness_threshold_seconds"), 60, 2678400),
        "heartbeat_threshold_seconds": integer(data.get("heartbeat_threshold_seconds", 120), 10, 3600),
        "run_state": cast(RunState, run_state), "current_run": current_run(data.get("current_run")),
        "last_error": safe_error(data.get("last_error")), "sources": sources, "destinations": destinations}


def health_status(data: BackupProjection, now: datetime) -> HealthStatus:
    def age(value: str | None) -> float | None:
        return (now - datetime.fromisoformat(value.replace("Z", "+00:00"))).total_seconds() if value else None
    heartbeat_age = age(data["worker_heartbeat_at"])
    success_age = age(data["last_success_at"])
    if heartbeat_age is not None and (heartbeat_age < -60 or heartbeat_age > data["heartbeat_threshold_seconds"]):
        return "stale"
    if data["run_state"] == "failed" or data["last_error"] is not None:
        return "failed" if success_age is None else "degraded"
    if success_age is None:
        return "initializing"
    if heartbeat_age is None or success_age < -60 or success_age > data["freshness_threshold_seconds"]:
        return "stale"
    if not data["destinations"] or not any(item["required"] for item in data["destinations"]):
        return "unavailable"
    for item in data["destinations"]:
        if item["required"]:
            destination_age = age(item.get("last_success_at"))
            if destination_age is None or not item["retained_sets"]:
                return "unavailable"
            if destination_age < -60 or destination_age > data["freshness_threshold_seconds"]:
                return "stale"
    if any(item.get("last_error") or item.get("cleanup_error") or item["pending_count"] or item["delivery_gap_count"] for item in data["destinations"]):
        return "degraded"
    return "healthy"


def _read(projection_dir: Path) -> tuple[BackupProjection | None, HealthStatus | None]:
    try:
        directory = os.open(projection_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            descriptor = os.open("status.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        finally:
            os.close(directory)
        with os.fdopen(descriptor, "rb") as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise ValueError("projection must be regular")
            raw = source.read(MAX_PROJECTION_BYTES + 1)
        if len(raw) > MAX_PROJECTION_BYTES:
            raise ValueError("projection exceeds limit")
        return sanitize_projection(object_value(decode(raw))), None
    except FileNotFoundError:
        return None, "initializing"
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        return None, "unavailable"


def read_healthcheck(projection_dir: Path, now: datetime) -> HealthcheckEnvelope:
    if now.tzinfo is None:
        raise ValueError("timezone-aware clock is required")
    data, failure = _read(projection_dir)
    system: BackupSystem = {"id": "backups", "kind": "backups",
        "status": health_status(data, now) if data else failure or "unavailable",
        "observed_at": data["worker_heartbeat_at"] if data else None, "backup": data}
    return {"schema_version": 1, "checked_at": required_timestamp(now.isoformat()), "systems": [system]}


def read_retained_sets(projection_dir: Path, now: datetime) -> RetainedSetsEnvelope:
    overview = read_healthcheck(projection_dir, now)
    system = overview["systems"][0]
    return {"schema_version": 1, "checked_at": overview["checked_at"], "status": system["status"],
        "destinations": system["backup"]["destinations"] if system["backup"] else []}
