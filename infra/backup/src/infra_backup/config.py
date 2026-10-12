#!/usr/bin/env python3
"""Load validated backup inventory without retaining credential values."""
from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit
from typing import NoReturn, TypedDict

from infra_backup.contracts import SourceType
from infra_backup.json_io import JsonObject, decode, object_value, unique_object


class BackupConfigError(ValueError):
    """Invalid inventory; messages deliberately exclude supplied values."""


@dataclass(frozen=True)
class SourceConfig:
    id: str
    type: SourceType
    timeout_seconds: int = 3600
    host: str | None = None
    port: int | None = None
    database: str | None = None
    user: str | None = None
    password_file: Path | None = None
    endpoint: str | None = None
    region: str | None = None
    bucket: str | None = None
    access_key_file: Path | None = None
    secret_key_file: Path | None = None
    internal: bool = False
    paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class DestinationConfig:
    id: str
    endpoint: str
    region: str
    bucket: str
    prefix: str
    access_key_file: Path
    secret_key_file: Path
    required: bool
    timeout_seconds: int = 3600
    internal: bool = False


@dataclass(frozen=True)
class BackupConfig:
    sources: tuple[SourceConfig, ...]
    destinations: tuple[DestinationConfig, ...]
    interval_seconds: int = 21600
    freshness_threshold_seconds: int = 25200
    pending_budget_bytes: int = 10 * 1024**3
    recovery_root: Path = Path("/recovery")


_ID = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
_ENV = re.compile(r"[A-Z][A-Z0-9_]*\Z")
_HOST = re.compile(r"[a-zA-Z0-9](?:[a-zA-Z0-9.-]{0,251}[a-zA-Z0-9])?\Z")
_BUCKET = re.compile(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]\Z")
_MAX_JSON_BYTES = 1024 * 1024
_MAX_SECRET_BYTES = 65536


def _fail(message: str) -> NoReturn:
    raise BackupConfigError(message) from None


def _json(path: Path) -> JsonObject:
    try:
        with path.open("rb") as stream:
            data = stream.read(_MAX_JSON_BYTES + 1)
        if len(data) > _MAX_JSON_BYTES:
            _fail("Backup configuration is too large.")
        value = decode(data)
    except (OSError, ValueError, UnicodeError):
        _fail("Backup configuration cannot be read as valid JSON.")
    if not isinstance(value, dict):
        _fail("Backup configuration must be an object.")
    return object_value(value)


def _unique_object(pairs: list[tuple[str, object]]) -> JsonObject:
    return unique_object(pairs)


def _fields(value: object, required: set[str], optional: set[str]) -> JsonObject:
    if (
        not isinstance(value, dict)
        or not required <= value.keys()
        or value.keys() - required - optional
    ):
        _fail("Backup configuration has missing or unsupported fields.")
    return object_value(value)


def _string(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(ord(c) < 32 for c in value)
    ):
        _fail("Backup configuration contains an invalid string.")
    return value


def _identifier(value: object) -> str:
    value = _string(value)
    if not _ID.fullmatch(value):
        _fail("Backup configuration contains an invalid identifier.")
    return value


def _number(value: object, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        _fail("Backup configuration contains an out-of-range number.")
    return value


def _boolean(value: object) -> bool:
    if type(value) is not bool:
        _fail("Backup configuration contains an invalid boolean.")
    return value


def _absolute_path(value: object) -> Path:
    value = _string(value)
    path = Path(value)
    if not path.is_absolute() or str(path) != value or ".." in path.parts:
        _fail("Backup configuration requires a normalized absolute path.")
    return path


def _secret(value: object) -> Path:
    path = _absolute_path(value)
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            _fail("Backup secret must be a readable regular file.")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            content = stream.read(_MAX_SECRET_BYTES + 1)
        if not content.strip() or len(content) > _MAX_SECRET_BYTES:
            _fail("Backup secret must be nonempty and within the size limit.")
    except OSError:
        _fail("Backup secret must be a readable regular file.")
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return path


def _environment(value: object) -> str:
    name = _string(value)
    if not _ENV.fullmatch(name):
        _fail("Backup configuration contains an invalid environment reference.")
    result = os.environ.get(name)
    if result is None:
        _fail("A required backup environment variable is missing.")
    return _string(result)


def _host(value: object) -> str:
    value = _string(value)
    if not _HOST.fullmatch(value) or any(
        not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
        for label in value.split(".")
    ):
        _fail("Backup configuration contains an invalid host.")
    return value


def _endpoint(value: object, internal: bool) -> str:
    value = _string(value)
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
        if parsed.username is not None or parsed.password is not None or not host:
            _fail("Backup endpoint must not contain credentials.")
        _host(host)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            _fail("Backup endpoint must be an HTTP origin.")
        if port is not None and not 1 <= port <= 65535:
            _fail("Backup endpoint contains an invalid port.")
        if parsed.scheme == "http" and (not internal or "." in host or host.isdigit()):
            _fail("Backup endpoints outside the internal service network require TLS.")
    except ValueError:
        _fail("Backup endpoint is invalid or contains credentials.")
    return value.rstrip("/")


def _bucket(value: object) -> str:
    value = _string(value)
    if (
        not _BUCKET.fullmatch(value)
        or ".." in value
        or ".-" in value
        or "-." in value
        or re.fullmatch(r"\d+\.\d+\.\d+\.\d+", value)
    ):
        _fail("Backup configuration contains an invalid bucket name.")
    return value


def _relative_path(value: object) -> str:
    value = _string(value)
    path = PurePosixPath(value)
    if (
        not path.parts
        or path.is_absolute()
        or str(path) != value
        or ".." in path.parts
        or "\\" in value
    ):
        _fail("Backup configuration requires a normalized relative path.")
    return value


def _recovery_path(value: object) -> str:
    value = _relative_path(value)
    path = PurePosixPath(value)
    allowed = (
        (path.parts[0] == "config" and path.suffix in {".env", ".json"})
        or (path.parts[0] == "secrets" and path.name.endswith(".sops.yaml"))
        or (len(path.parts) == 1 and path.name.startswith("docker-compose") and path.suffix == ".yml")
        or (path.parts[:2] == ("infra", "deploy") and path.name.endswith(".manifest.json"))
        or (path.parts[:3] == ("infra", "minio", "policies") and path.suffix == ".json")
        or value == "infra/backup/sources.json"
    )
    if not allowed or any(part.startswith(".") for part in path.parts):
        _fail("Recovery configuration path is outside the permitted allowlist.")
    return value


class S3Fields(TypedDict):
    internal: bool
    endpoint: str
    region: str
    bucket: str
    access_key_file: Path
    secret_key_file: Path


def _s3_fields(raw: JsonObject) -> S3Fields:
    internal = _boolean(raw.get("internal", False))
    return {
        "internal": internal,
        "endpoint": _endpoint(raw["endpoint"], internal),
        "region": _environment(raw["region_env"]),
        "bucket": _bucket(raw["bucket"]),
        "access_key_file": _secret(raw["access_key_file"]),
        "secret_key_file": _secret(raw["secret_key_file"]),
    }


def _source(value: object) -> SourceConfig:
    raw = _fields(value, {"id", "type"}, {
        "timeout_seconds", "host", "port", "database_env", "user_env",
        "password_file", "endpoint", "region_env", "bucket", "access_key_file",
        "secret_key_file", "internal", "paths",
    })
    source_id = _identifier(raw["id"])
    kind = _string(raw["type"])
    timeout = _number(raw.get("timeout_seconds", 3600), 1, 86400)
    base = {"id", "type"}
    if kind == "postgres":
        _fields(raw, base | {"host", "database_env", "user_env", "password_file"}, {"timeout_seconds", "port"})
        return SourceConfig(
            id=source_id, type="postgres", timeout_seconds=timeout,
            host=_host(raw["host"]),
            port=_number(raw.get("port", 5432), 1, 65535),
            database=_environment(raw["database_env"]),
            user=_environment(raw["user_env"]),
            password_file=_secret(raw["password_file"]),
        )
    if kind == "valkey":
        _fields(raw, base | {"host"}, {"timeout_seconds", "port"})
        return SourceConfig(
            id=source_id, type="valkey", timeout_seconds=timeout, host=_host(raw["host"]),
            port=_number(raw.get("port", 6379), 1, 65535),
        )
    if kind == "s3":
        _fields(raw, base | {"endpoint", "region_env", "bucket", "access_key_file", "secret_key_file"}, {"timeout_seconds", "internal"})
        return SourceConfig(id=source_id, type="s3", timeout_seconds=timeout, **_s3_fields(raw))
    if kind == "config":
        _fields(raw, base | {"paths"}, {"timeout_seconds"})
        if not isinstance(raw["paths"], list) or not raw["paths"]:
            _fail("Recovery configuration requires an explicit file allowlist.")
        paths = tuple(_recovery_path(path) for path in raw["paths"])
        if len(paths) != len(set(paths)):
            _fail("Recovery configuration paths must be unique.")
        return SourceConfig(id=source_id, type="config", timeout_seconds=timeout, paths=paths)
    _fail("Backup configuration contains an unsupported source type.")


def _destination(value: object) -> DestinationConfig:
    raw = _fields(value, {
        "id", "endpoint", "region_env", "bucket", "prefix",
        "access_key_file", "secret_key_file", "required",
    }, {"timeout_seconds", "internal"})
    prefix = _relative_path(raw["prefix"])
    if not all(re.fullmatch(r"[a-zA-Z0-9_-]+", part) for part in PurePosixPath(prefix).parts):
        _fail("Backup destination prefix is unsafe.")
    return DestinationConfig(
        id=_identifier(raw["id"]), prefix=prefix,
        required=_boolean(raw["required"]),
        timeout_seconds=_number(raw.get("timeout_seconds", 3600), 1, 86400),
        **_s3_fields(raw),
    )


def load_config(sources_path: Path, destinations_path: Path) -> BackupConfig:
    raw = _fields(_json(sources_path), {"sources"}, {
        "interval_seconds", "freshness_threshold_seconds", "pending_budget_bytes",
        "recovery_root",
    })
    destinations_raw = _fields(_json(destinations_path), {"destinations"}, set())
    if (
        not isinstance(raw["sources"], list)
        or not raw["sources"]
        or not isinstance(destinations_raw["destinations"], list)
        or not destinations_raw["destinations"]
    ):
        _fail("Backup sources and destinations must be nonempty lists.")
    sources = tuple(_source(source) for source in raw["sources"])
    destinations = tuple(_destination(destination) for destination in destinations_raw["destinations"])
    if (
        len({source.id for source in sources}) != len(sources)
        or len({destination.id for destination in destinations}) != len(destinations)
    ):
        _fail("Backup source and destination identifiers must be unique.")
    if not any(destination.required for destination in destinations):
        _fail("At least one backup destination must be required.")
    destination_buckets = {destination.bucket for destination in destinations}
    if any(source.type == "s3" and source.bucket in destination_buckets for source in sources):
        _fail("Backup destination buckets must be excluded from sources.")
    interval = _number(raw.get("interval_seconds", 21600), 60, 604800)
    return BackupConfig(
        sources=sources, destinations=destinations, interval_seconds=interval,
        freshness_threshold_seconds=_number(
            raw.get("freshness_threshold_seconds", 25200), interval, 2678400,
        ),
        pending_budget_bytes=_number(
            raw.get("pending_budget_bytes", 10 * 1024**3), 1024**2, 1024**4,
        ),
        recovery_root=_absolute_path(raw.get("recovery_root", "/recovery")),
    )
