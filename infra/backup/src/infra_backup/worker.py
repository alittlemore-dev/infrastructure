#!/usr/bin/env python3
"""Durable scheduling and independent delivery of complete recovery sets."""
from __future__ import annotations

import argparse
import fcntl
import os
import shutil
import signal
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterator, Mapping, NoReturn, Protocol, Sequence

from infra_backup.config import BackupConfig, DestinationConfig, load_config
from infra_backup.sources import CollectionResult, collect_sources
from infra_backup.state import LockBusy, append_attempt, private_directory, read_json, decode_worker_state, run_lock, write_json_atomic
from infra_backup.status.projection import identifier, sanitize_projection
from infra_backup.storage import DeliveryResult, deliver, read_retention, retained_sets
from infra_backup.retention import RetentionRefs
from infra_backup.contracts import CheckpointRole, ErrorCode, RetainedSet, RunPhase, SafeError, StoredSet, WorkerState


class Collector(Protocol):
    def __call__(self, config: BackupConfig, run_directory: Path, /, *,
                 deployment_metadata_path: Path | None = None) -> CollectionResult: ...



def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def stamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("timezone-aware clock is required")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def error(code: ErrorCode, *, source_id: str | None = None, destination_id: str | None = None) -> SafeError:
    result: SafeError = {"code": code}
    if source_id is not None:
        result["source_id"] = source_id
    if destination_id is not None:
        result["destination_id"] = destination_id
    return result


class WorkerStopped(BaseException):
    """Interrupt blocking work promptly without a source swallowing the signal."""


ROLES: tuple[CheckpointRole, ...] = ("latest", "weekly_current", "weekly_previous")


class BackupWorker:
    def __init__(self, config: BackupConfig, state_dir: Path, work_dir: Path,
                 projection_dir: Path | None = None, *, clock: Callable[[], datetime] = utc_now,
                 collector: Collector = collect_sources,
                 delivery: Callable[[Path, DestinationConfig], DeliveryResult] = deliver,
                 retention: Callable[[DestinationConfig], RetentionRefs] = read_retention,
                 sets: Callable[[DestinationConfig], list[StoredSet]] = retained_sets,
                 publisher: Callable[[Path, Mapping[str, object]], None] = write_json_atomic,
                 deployment_metadata_path: Path | None = None) -> None:
        self.config = config
        self.state_dir, self.work_dir = state_dir, work_dir
        self.projection_dir = projection_dir
        self.clock, self.collector, self.delivery = clock, collector, delivery
        self.retention, self.sets, self.publisher = retention, sets, publisher
        self.metadata_path = deployment_metadata_path
        self._mutex = threading.RLock()
        self.stop_event = threading.Event()
        private_directory(state_dir)
        private_directory(work_dir)

    def _initial(self) -> WorkerState:
        return {"schema_version": 1, "freshness_threshold_seconds": self.config.freshness_threshold_seconds,
                "run_state": "initializing", "current_run": None, "last_error": None,
                "pending": [], "attempts": [], "sources": [], "destinations": [
                    {"id": dest.id, "required": dest.required, "retained_sets": [],
                     "pending_count": 0, "delivery_gap_count": 0} for dest in self.config.destinations]}

    @contextmanager
    def _state(self) -> Iterator[WorkerState]:
        # This lock is held only for local atomic state writes, never network work.
        with self._mutex:
            descriptor = os.open(self.state_dir / "update.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                os.fchmod(descriptor, 0o600)
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                state = self._load_state()
                existing = {item["id"]: item for item in state["destinations"]}
                state["destinations"] = [
                    {**existing.get(dest.id, {"id": dest.id, "required": dest.required, "retained_sets": [], "pending_count": 0, "delivery_gap_count": 0}),
                     "required": dest.required} for dest in self.config.destinations]
                yield state
                state["generated_at"] = stamp(self.clock())
                state["freshness_threshold_seconds"] = self.config.freshness_threshold_seconds
                for dest in state["destinations"]:
                    dest["pending_count"] = sum(dest["id"] in item["destinations"] for item in state["pending"])
                write_json_atomic(self.state_dir / "state.json", state)
                self._publish(state)
            finally:
                os.close(descriptor)

    def _publish(self, state: WorkerState) -> None:
        projection = sanitize_projection(state)
        if self.projection_dir is not None:
            try:
                self.publisher(self.projection_dir / "status.json", projection)
                state.pop("projection_error", None)
            except Exception:
                state["projection_error"] = error("projection_failed")
            if state.get("projection_error") and projection["last_error"] is None:
                projection["last_error"] = error("projection_failed")
                projection = sanitize_projection(projection)
            write_json_atomic(self.state_dir / "state.json", state)
        write_json_atomic(self.state_dir / "status.json", projection)

    def heartbeat(self) -> None:
        with self._state() as state:
            state["worker_heartbeat_at"] = stamp(self.clock())

    def _load_state(self) -> WorkerState:
        try:
            return decode_worker_state(read_json(self.state_dir / "state.json"))
        except FileNotFoundError:
            return self._initial()

    def snapshot(self) -> WorkerState:
        with self._mutex:
            return self._load_state()

    def _recover(self) -> None:
        with self._state() as state:
            current = state.get("current_run")
            if current:
                interrupted = error("interrupted")
                state.update({"current_run": None, "run_state": "failed", "last_error": interrupted})
                if current["phase"] == "collecting":
                    state["collection_error"] = interrupted
                append_attempt(state, {"run_id": current["id"], "started_at": current["started_at"],
                                       "finished_at": stamp(self.clock()), "success": False, "error": interrupted})
                if not any(item["run_id"] == current["id"] for item in state["pending"]):
                    self._remove_run(current["id"])

    def _remove_run(self, run_id: str) -> None:
        identifier(run_id)
        path = self.work_dir / run_id
        if path.is_symlink():
            path.unlink()
        elif path.exists():
            shutil.rmtree(path)

    def _phase(self, run_id: str, phase: RunPhase, started_at: str) -> None:
        with self._state() as state:
            state.update({"run_state": "running", "current_run": {"id": run_id, "phase": phase, "started_at": started_at}})

    def _deliver_pending(self, only_run: str | None = None) -> None:
        destinations = {dest.id: dest for dest in self.config.destinations}
        for pending in self.snapshot()["pending"]:
            if only_run is not None and pending["run_id"] != only_run:
                continue
            if self.stop_event.is_set():
                return
            self._phase(pending["run_id"], "retrying", pending["started_at"])
            for dest_id in list(pending["destinations"]):
                if self.stop_event.is_set():
                    return
                dest = destinations.get(dest_id)
                if dest is None:
                    continue
                try:
                    result = self.delivery(self.work_dir / pending["run_id"], dest)
                except Exception:
                    result = None
                retained: list[RetainedSet] | None = None
                if result is not None and result.success and result.completed_at is not None:
                    try:
                        refs = result.refs or self.retention(dest)
                        observed: list[RetainedSet] = []
                        for item in self.sets(dest):
                            roles = [role for role in ROLES if getattr(refs, role) == item["run_id"]]
                            if roles:
                                observed.append({"run_id": item["run_id"], "roles": roles,
                                                 "completed_at": item["completed_at"], "total_bytes": item["bytes"], "complete": True})
                        retained = observed
                    except Exception:
                        pass
                with self._state() as state:
                    current = next((item for item in state["pending"] if item["run_id"] == pending["run_id"]), None)
                    if current is None:
                        raise ValueError("pending delivery disappeared")
                    status = next(item for item in state["destinations"] if item["id"] == dest_id)
                    if result is not None and result.success and result.completed_at is not None:
                        current["destinations"].remove(dest_id)
                        previous_success = status.get("last_success_at")
                        completed_at = max((previous_success, result.completed_at), key=instant) if previous_success else result.completed_at
                        status.update({"last_success_at": completed_at, "last_error": None,
                                       "cleanup_error": error("cleanup_failed", destination_id=dest_id) if result.cleanup_error or retained is None else None})
                        if retained is not None:
                            status["retained_sets"] = retained
                        elif result.refs:
                            # Keep verified delivery visible even when rereading status fails.
                            known = {item["run_id"]: item for item in status["retained_sets"]}
                            if result.run_id is not None:
                                known[result.run_id] = {"run_id": result.run_id, "completed_at": result.completed_at,
                                                       "total_bytes": result.bytes, "complete": True, "roles": []}
                            status["retained_sets"] = [{**item, "roles": roles} for run_id, item in known.items()
                                if (roles := [role for role in ROLES if getattr(result.refs, role) == run_id])]
                    else:
                        status["last_error"] = error("delivery_failed", destination_id=dest_id)
                    required = {dest.id for dest in self.config.destinations if dest.required}
                    if not required.intersection(current["destinations"]):
                        previous = state.get("last_success_at")
                        completed = max((item.get("last_success_at") or pending["completed_at"] for item in state["destinations"] if item["required"]), default=pending["completed_at"])
                        if previous is None or instant(completed) > instant(previous):
                            state["last_success_at"] = completed
                if not self.snapshot()["pending"]:
                    break
            with self._state() as state:
                current = next(item for item in state["pending"] if item["run_id"] == pending["run_id"])
                if not current["destinations"]:
                    state["pending"].remove(current)
                    self._remove_run(pending["run_id"])
        with self._state() as state:
            state["next_retry_at"] = stamp(self.clock() + timedelta(seconds=min(300, self.config.interval_seconds))) if state["pending"] else None

    def _budget(self) -> None:
        with self._state() as state:
            total = sum(item["bytes"] for item in state["pending"])
            while total > self.config.pending_budget_bytes and state["pending"]:
                evicted = state["pending"].pop(0)
                total -= evicted["bytes"]
                for dest in state["destinations"]:
                    if dest["id"] in evicted["destinations"]:
                        dest["delivery_gap_count"] = dest.get("delivery_gap_count", 0) + 1
                        dest["last_error"] = error("delivery_evicted", destination_id=dest["id"])
                self._remove_run(evicted["run_id"])

    def _finish(self, run_id: str | None = None, started: str | None = None, failure: SafeError | None = None) -> bool:
        with self._state() as state:
            pending = next((item for item in state["pending"] if item["run_id"] == run_id), None)
            if run_id is None and state.get("collection_error") is not None:
                failure = state["collection_error"]
            required = {dest.id for dest in self.config.destinations if dest.required}
            waiting = pending["destinations"] if pending is not None else [dest for item in state["pending"] for dest in item["destinations"]] if run_id is None else []
            success = failure is None and not required.intersection(waiting)
            if self.stop_event.is_set():
                success, failure = False, error("interrupted")
            if failure is None:
                failure = next((dest.get("last_error") or dest.get("cleanup_error") for dest in state["destinations"] if dest.get("last_error") or dest.get("cleanup_error")), None)
            state.update({"current_run": None, "run_state": "failed" if not success else "idle", "last_error": failure})
            if run_id:
                if started is None:
                    raise ValueError("attempt start is required")
                if failure is not None and pending is None and failure["code"] in {"source_failed", "storage_full", "interrupted"}:
                    state["collection_error"] = failure
                append_attempt(state, {"run_id": run_id, "started_at": started,
                                       "finished_at": stamp(self.clock()), "success": success, "error": failure})
            return success

    @contextmanager
    def _heartbeats(self, seconds: float) -> Iterator[None]:
        finished = threading.Event()
        def beat() -> None:
            while not finished.wait(seconds):
                try:
                    self.heartbeat()
                except Exception:
                    pass
        thread = threading.Thread(target=beat, name="backup-heartbeat", daemon=True)
        thread.start()
        try:
            yield
        finally:
            finished.set()
            thread.join(timeout=seconds + 1)

    def run_once(self) -> bool:
        with self._heartbeats(20):
            return self._run_once()

    def _run_once(self) -> bool:
        with run_lock(self.state_dir):
            self._recover()
            self.heartbeat()
            self._deliver_pending()
            if self.stop_event.is_set():
                return self._finish(failure=error("interrupted"))
            started = stamp(self.clock())
            run_id = self.clock().astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:12]
            with self._state() as state:
                state.update({"last_attempt_at": started, "next_due_at": stamp(self.clock() + timedelta(seconds=self.config.interval_seconds)),
                              "current_run": {"id": run_id, "phase": "collecting", "started_at": started}, "run_state": "running", "last_error": state.get("collection_error")})
            directory = self.work_dir / run_id
            try:
                collection = self.collector(self.config, directory, deployment_metadata_path=self.metadata_path)
                with self._state() as state:
                    state["sources"] = collection.sources
                    if collection.complete:
                        state["collection_error"] = None
                if not collection.complete or self.stop_event.is_set():
                    self._remove_run(run_id)
                    return self._finish(run_id, started, error("interrupted" if self.stop_event.is_set() else "source_failed"))
                completed = stamp(self.clock())
                write_json_atomic(directory / "manifest.json", {"schema_version": 1, "run_id": run_id, "complete": True,
                    "started_at": started, "completed_at": completed, "sources": collection.sources, "artifacts": collection.artifacts})
                # Include manifest and auxiliary files in the disk budget, not only payload.
                size = sum(path.stat().st_size for path in directory.rglob("*") if path.is_file())
                with self._state() as state:
                    state["pending"].append({"run_id": run_id, "started_at": started, "completed_at": completed,
                                             "bytes": size, "destinations": [dest.id for dest in self.config.destinations]})
                self._deliver_pending(only_run=run_id)
                result = self._finish(run_id, started)
                self._budget()
                return result
            except WorkerStopped:
                if not any(item["run_id"] == run_id for item in self.snapshot()["pending"]):
                    self._remove_run(run_id)
                return self._finish(run_id, started, error("interrupted"))
            except Exception as exc:
                failure = error("storage_full" if isinstance(exc, OSError) and exc.errno == 28 else "source_failed")
                if not any(item["run_id"] == run_id for item in self.snapshot()["pending"]):
                    self._remove_run(run_id)
                return self._finish(run_id, started, failure)

    def tick(self, *, schedule_enabled: bool = True) -> bool | None:
        self.heartbeat()
        if not schedule_enabled or self.stop_event.is_set():
            return None
        state = self.snapshot()
        if state.get("current_run"):
            try:
                with run_lock(self.state_dir):
                    self._recover()
                state = self.snapshot()
            except LockBusy:
                return None
        if state.get("next_due_at") is None or instant(state["next_due_at"]) <= self.clock():
            try:
                return self.run_once()
            except LockBusy:
                return None
        next_retry = state.get("next_retry_at")
        if state["pending"] and (next_retry is None or instant(next_retry) <= self.clock()):
            try:
                with run_lock(self.state_dir):
                    self._recover()
                    self._deliver_pending()
                    self._budget()
                    return self._finish()
            except LockBusy:
                pass
        return None

    def serve(self, *, schedule_enabled: bool = True, heartbeat_seconds: float = 20) -> None:
        self.heartbeat()
        try:
            with self._heartbeats(heartbeat_seconds):
                while not self.stop_event.is_set():
                    self.tick(schedule_enabled=schedule_enabled)
                    self.stop_event.wait(min(20, heartbeat_seconds))
        finally:
            self.stop_event.set()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("config", "destinations", "state-dir", "work-dir", "projection-dir"):
        parser.add_argument("--" + flag, type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        enabled = os.environ.get("BACKUP_SCHEDULE_ENABLED", "true")
        if enabled not in {"true", "false"}:
            raise ValueError("invalid schedule flag")
        metadata = os.environ.get("BACKUP_DEPLOYMENT_METADATA")
        worker = BackupWorker(load_config(args.config, args.destinations), args.state_dir, args.work_dir,
                              args.projection_dir, deployment_metadata_path=Path(metadata) if metadata else None)
        def terminate(*_: object) -> NoReturn:
            worker.stop_event.set()
            raise WorkerStopped()
        for event in (signal.SIGTERM, signal.SIGINT):
            signal.signal(event, terminate)
        worker.serve(schedule_enabled=enabled == "true")
        return 0
    except WorkerStopped:
        return 0
    except Exception:
        print("Backup worker failed; inspect configuration and storage.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
