"""Persisted and public contracts shared by collection, delivery and status."""
from __future__ import annotations

from typing import Literal, TypedDict

MAX_MANIFEST_BYTES = 64 * 1024 * 1024

ErrorCode = Literal["source_failed", "delivery_failed", "cleanup_failed", "delivery_evicted",
                    "interrupted", "projection_failed", "storage_full", "configuration_invalid"]
SourceType = Literal["postgres", "valkey", "s3", "config"]
RunState = Literal["initializing", "idle", "running", "failed"]
RunPhase = Literal["collecting", "delivering", "retrying", "pruning"]
CheckpointRole = Literal["latest", "weekly_current", "weekly_previous"]
HealthStatus = Literal["healthy", "initializing", "degraded", "stale", "failed", "unavailable"]


class ErrorRequired(TypedDict):
    code: ErrorCode


class SafeError(ErrorRequired, total=False):
    message: str
    source_id: str
    destination_id: str


class Artifact(TypedDict):
    path: str
    bytes: int
    sha256: str


class SourceRequired(TypedDict):
    id: str
    type: SourceType
    status: Literal["success", "failed"]
    complete: bool
    artifacts: list[Artifact]


class SourceResult(SourceRequired, total=False):
    started_at: str
    finished_at: str
    bytes: int
    error: SafeError


class ManifestRequired(TypedDict):
    schema_version: int
    run_id: str
    complete: bool
    started_at: str
    completed_at: str
    sources: list[SourceResult]
    artifacts: list[Artifact]


class RunManifest(ManifestRequired, total=False):
    delivery_completed_at: str


class RetainedSet(TypedDict):
    run_id: str
    completed_at: str
    roles: list[CheckpointRole]
    total_bytes: int
    complete: bool


class DestinationRequired(TypedDict):
    id: str
    required: bool
    retained_sets: list[RetainedSet]
    pending_count: int
    delivery_gap_count: int


class DestinationStatus(DestinationRequired, total=False):
    last_success_at: str | None
    last_error: SafeError | None
    cleanup_error: SafeError | None


class PendingRun(TypedDict):
    run_id: str
    started_at: str
    completed_at: str
    bytes: int
    destinations: list[str]


class Attempt(TypedDict):
    run_id: str
    started_at: str
    finished_at: str
    success: bool
    error: SafeError | None


class CurrentRun(TypedDict):
    id: str
    phase: RunPhase
    started_at: str


class StateRequired(TypedDict):
    schema_version: int
    freshness_threshold_seconds: int
    run_state: RunState
    current_run: CurrentRun | None
    last_error: SafeError | None
    pending: list[PendingRun]
    attempts: list[Attempt]
    sources: list[SourceResult]
    destinations: list[DestinationStatus]


class WorkerState(StateRequired, total=False):
    generated_at: str
    worker_heartbeat_at: str
    last_attempt_at: str
    last_success_at: str
    next_due_at: str
    next_retry_at: str | None
    projection_error: SafeError
    collection_error: SafeError | None
    last_restore_verified_at: str | None


class ProjectionSource(TypedDict):
    id: str
    complete: bool
    error: SafeError | None


class BackupProjection(TypedDict):
    schema_version: int
    generated_at: str
    worker_heartbeat_at: str | None
    last_attempt_at: str | None
    last_success_at: str | None
    next_due_at: str | None
    last_restore_verified_at: str | None
    freshness_threshold_seconds: int
    heartbeat_threshold_seconds: int
    run_state: RunState
    current_run: CurrentRun | None
    last_error: SafeError | None
    sources: list[ProjectionSource]
    destinations: list[DestinationStatus]


class BackupSystem(TypedDict):
    id: Literal["backups"]
    kind: Literal["backups"]
    status: HealthStatus
    observed_at: str | None
    backup: BackupProjection | None


class HealthcheckEnvelope(TypedDict):
    schema_version: int
    checked_at: str
    systems: list[BackupSystem]


class RetainedSetsEnvelope(TypedDict):
    schema_version: int
    checked_at: str
    status: HealthStatus
    destinations: list[DestinationStatus]


class StoredSet(TypedDict):
    run_id: str
    completed_at: str
    bytes: int


class RetentionDocument(TypedDict):
    schema_version: int
    latest: str | None
    weekly_current: str | None
    weekly_previous: str | None
    weekly_key: str | None
    sets: dict[str, StoredSet]
