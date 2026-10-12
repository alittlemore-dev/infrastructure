"""Private immutable S3 backup sets, verified commits and guarded recovery."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, BinaryIO, Mapping, Protocol, cast

from infra_backup.config import DestinationConfig
from infra_backup.retention import RetentionRefs, advance_retention, protected_ids
from infra_backup.credentials import read_secret
from infra_backup.contracts import Artifact, ErrorCode, MAX_MANIFEST_BYTES, RetentionDocument, RunManifest, SafeError, SourceResult, SourceType, StoredSet
from infra_backup.json_io import JsonObject, decode, encode, list_value, object_value, string_value, unique_object

if TYPE_CHECKING:
    from mypy_boto3_s3.client import S3Client
    from mypy_boto3_s3.type_defs import CompletedPartTypeDef, GetObjectOutputTypeDef, ListObjectsV2RequestTypeDef, ListMultipartUploadsRequestTypeDef, PutObjectRequestTypeDef


class Readable(Protocol):
    def read(self, size: int = -1, /) -> bytes: ...
    def close(self) -> None: ...

_JSON_LIMIT = MAX_MANIFEST_BYTES
_CHUNK = 1024 * 1024
_PART = 16 * _CHUNK
_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")


class StorageFailure(Exception):
    """Details are private; API boundaries return fixed safe messages."""


@dataclass(frozen=True)
class DeliveryResult:
    """Verified remote copy; a superseded retry returns the retained newer run."""

    success: bool
    run_id: str | None = None
    completed_at: str | None = None
    bytes: int = 0
    refs: RetentionRefs | None = None
    error: SafeError | None = None
    cleanup_error: SafeError | None = None


@dataclass(frozen=True)
class PruneResult:
    success: bool
    deleted: int = 0
    error: SafeError | None = None


def _error(destination: DestinationConfig, code: ErrorCode = "delivery_failed") -> SafeError:
    return {"code": code, "destination_id": destination.id,
            "message": "Backup storage operation failed; inspect the destination and retry."}


def _remaining(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise StorageFailure()


def _client(destination: DestinationConfig) -> S3Client:
    # Host-only tests do not need an SDK installation or credential discovery.
    import boto3
    from botocore.config import Config
    return boto3.client("s3", endpoint_url=destination.endpoint, region_name=destination.region,
                        aws_access_key_id=read_secret(destination.access_key_file),
                        aws_secret_access_key=read_secret(destination.secret_key_file),
                        config=Config(connect_timeout=min(15, destination.timeout_seconds),
                                      read_timeout=min(30, destination.timeout_seconds),
                                      retries={"max_attempts": 0}, s3={"addressing_style": "path"}))


def _path(value: object) -> str:
    if (not isinstance(value, str) or not value or "\\" in value
            or any(ord(character) < 32 for character in value)):
        raise StorageFailure()
    path = PurePosixPath(value)
    if path.is_absolute() or str(path) != value or any(part in (".", "..") for part in path.parts):
        raise StorageFailure()
    return value


def _run_id(value: object) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise StorageFailure()
    return value


def _namespace(destination: DestinationConfig) -> str:
    prefix = _path(destination.prefix)
    return prefix + "/"


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise StorageFailure()
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    offset = parsed.utcoffset()
    if parsed.tzinfo is None or offset is None or offset.total_seconds() != 0:
        raise StorageFailure()
    return parsed


def _artifact_record(value: object) -> Artifact:
    raw = object_value(value)
    size, digest = raw.get("bytes"), raw.get("sha256")
    if (set(raw) != {"path", "bytes", "sha256"} or type(size) is not int or size < 0
            or not isinstance(digest, str) or not _HASH.fullmatch(digest)):
        raise StorageFailure()
    return {"path": _path(raw["path"]), "bytes": size, "sha256": digest}


def _source_record(value: object) -> SourceResult:
    raw = object_value(value)
    source_id, kind = raw.get("id"), raw.get("type")
    if (raw.get("complete") is not True or raw.get("status") != "success"
            or not isinstance(source_id, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", source_id)
            or kind not in {"postgres", "valkey", "s3", "config"}):
        raise StorageFailure()
    artifacts = [_artifact_record(item) for item in list_value(raw["artifacts"])]
    if any(not item["path"].startswith("sources/" + source_id + "/") for item in artifacts):
        raise StorageFailure()
    result: SourceResult = {"id": source_id, "type": cast(SourceType, kind),
        "status": "success", "complete": True, "artifacts": artifacts}
    if "started_at" in raw:
        _timestamp(raw["started_at"])
        result["started_at"] = string_value(raw["started_at"])
    if "finished_at" in raw:
        _timestamp(raw["finished_at"])
        result["finished_at"] = string_value(raw["finished_at"])
    if "bytes" in raw:
        size = raw["bytes"]
        if type(size) is not int or size < 0:
            raise StorageFailure()
        result["bytes"] = size
    return result


def _manifest(value: object, run_id: str | None = None, committed: bool = False) -> RunManifest:
    raw = object_value(value)
    required = {"schema_version", "run_id", "complete", "started_at", "completed_at", "sources", "artifacts"}
    if committed:
        required.add("delivery_completed_at")
    if (set(raw) != required or type(raw["schema_version"]) is not int or raw["schema_version"] != 1
            or raw["complete"] is not True or _run_id(raw["run_id"]) != (run_id or raw["run_id"])):
        raise StorageFailure()
    if _timestamp(raw["completed_at"]) < _timestamp(raw["started_at"]):
        raise StorageFailure()
    sources = [_source_record(item) for item in list_value(raw["sources"])]
    ids = {source["id"] for source in sources}
    artifacts = [_artifact_record(item) for item in list_value(raw["artifacts"])]
    if (not sources or len(ids) != len(sources) or not artifacts
            or [item for source in sources for item in source["artifacts"]] != artifacts):
        raise StorageFailure()
    paths: set[str] = set()
    for artifact in artifacts:
        path = artifact["path"]
        parts = PurePosixPath(path).parts
        if len(parts) < 3 or parts[0] != "sources" or parts[1] not in ids or path in paths:
            raise StorageFailure()
        paths.add(path)
    if any(any(str(parent) in paths for parent in PurePosixPath(path).parents) for path in paths):
        raise StorageFailure()
    result: RunManifest = {"schema_version": 1, "run_id": _run_id(raw["run_id"]), "complete": True,
        "started_at": string_value(raw["started_at"]), "completed_at": string_value(raw["completed_at"]),
        "sources": sources, "artifacts": artifacts}
    if committed:
        _timestamp(raw["delivery_completed_at"])
        result["delivery_completed_at"] = string_value(raw["delivery_completed_at"])
    return result


def _encode(value: object) -> bytes:
    return encode(value)


def _unique(pairs: list[tuple[str, object]]) -> JsonObject:
    return unique_object(pairs)


def _decode(data: bytes | bytearray) -> object:
    if len(data) > _JSON_LIMIT:
        raise StorageFailure()
    return decode(data)


def _missing(error: BaseException) -> bool:
    raw: object = getattr(error, "response", {})
    if not isinstance(raw, dict):
        return False
    response = object_value(raw)
    details = response.get("Error", {})
    return isinstance(details, dict) and object_value(details).get("Code") in ("NoSuchKey", "404", "NotFound")


def _get(client: S3Client, destination: DestinationConfig, key: str, deadline: float) -> GetObjectOutputTypeDef | None:
    _remaining(deadline)
    try:
        return client.get_object(Bucket=destination.bucket, Key=key)
    except Exception as error:
        if _missing(error):
            return None
        raise


def _json_get(client: S3Client, destination: DestinationConfig, key: str, deadline: float) -> tuple[object, str | None]:
    response = _get(client, destination, key, deadline)
    if response is None:
        return None, None
    body = response["Body"]
    try:
        data = bytearray()
        while True:
            _remaining(deadline)
            chunk = body.read(min(_CHUNK, _JSON_LIMIT + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > _JSON_LIMIT:
                raise StorageFailure()
        return _decode(data), response.get("ETag")
    finally:
        body.close()


def _verify_response(response: Mapping[str, object] | None, artifact: Artifact, deadline: float, output: BinaryIO | None = None) -> None:
    if response is None:
        raise StorageFailure()
    body = cast(Readable, response["Body"])
    digest, size = hashlib.sha256(), 0
    try:
        while True:
            _remaining(deadline)
            chunk = body.read(_CHUNK)
            if not chunk:
                break
            size += len(chunk)
            if size > artifact["bytes"]:
                raise StorageFailure()
            digest.update(chunk)
            if output is not None:
                output.write(chunk)
        if size != artifact["bytes"] or digest.hexdigest() != artifact["sha256"]:
            raise StorageFailure()
    finally:
        body.close()


def _open(root: Path, relative: str) -> BinaryIO:
    # Descriptor traversal rejects every symlink, including parent components.
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parts = PurePosixPath(_path(relative)).parts
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        file_descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=descriptor)
        if not stat.S_ISREG(os.fstat(file_descriptor).st_mode):
            os.close(file_descriptor)
            raise StorageFailure()
        stream: BinaryIO = os.fdopen(file_descriptor, "rb")
        return stream
    finally:
        os.close(descriptor)


def _immutable_json(client: S3Client, destination: DestinationConfig, key: str, value: Mapping[str, object], deadline: float) -> None:
    existing, _ = _json_get(client, destination, key, deadline)
    if existing is not None:
        if existing != value:
            raise StorageFailure()
        return
    _remaining(deadline)
    client.put_object(Bucket=destination.bucket, Key=key, Body=_encode(value),
                      ContentType="application/json", IfNoneMatch="*")
    stored, _ = _json_get(client, destination, key, deadline)
    if stored != value:
        raise StorageFailure()


def _upload(client: S3Client, destination: DestinationConfig, key: str, stream: BinaryIO, size: int, deadline: float) -> None:
    _remaining(deadline)
    if size <= 64 * _CHUNK:
        client.put_object(Bucket=destination.bucket, Key=key, Body=stream, ContentLength=size, IfNoneMatch="*")
        return
    part_size = max(_PART, ((size + 9999) // 10000 + _CHUNK - 1) // _CHUNK * _CHUNK)
    upload = client.create_multipart_upload(Bucket=destination.bucket, Key=key)["UploadId"]
    try:
        parts: list[CompletedPartTypeDef] = []
        while True:
            _remaining(deadline)
            data = stream.read(part_size)
            if not data:
                break
            number = len(parts) + 1
            response = client.upload_part(Bucket=destination.bucket, Key=key, UploadId=upload,
                                          PartNumber=number, Body=data)
            parts.append({"PartNumber": number, "ETag": response["ETag"]})
        _remaining(deadline)
        client.complete_multipart_upload(Bucket=destination.bucket, Key=key, UploadId=upload,
                                         MultipartUpload={"Parts": parts}, IfNoneMatch="*")
    except Exception:
        try:
            client.abort_multipart_upload(Bucket=destination.bucket, Key=key, UploadId=upload)
        except Exception:
            pass
        raise


def _retention(client: S3Client, destination: DestinationConfig, deadline: float) -> tuple[RetentionRefs, dict[str, StoredSet], str | None]:
    value, etag = _json_get(client, destination, _namespace(destination) + "retention.json", deadline)
    if value is None:
        return RetentionRefs(), {}, None
    raw = object_value(value)
    fields = {"schema_version", "latest", "weekly_current", "weekly_previous", "weekly_key", "sets"}
    if set(raw) != fields or type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
        raise StorageFailure()
    def reference(name: str) -> str | None:
        return _run_id(raw[name]) if raw[name] is not None else None
    week = raw["weekly_key"]
    if week is not None and (not isinstance(week, str) or not re.fullmatch(r"[0-9]{4}-W(?:0[1-9]|[1-4][0-9]|5[0-3])", week)):
        raise StorageFailure()
    refs = RetentionRefs(reference("latest"), reference("weekly_current"), reference("weekly_previous"), week)
    sets_raw = object_value(raw["sets"])
    if set(sets_raw) != protected_ids(refs) or not etag:
        raise StorageFailure()
    sets: dict[str, StoredSet] = {}
    for run_id, value in sets_raw.items():
        item = object_value(value)
        size = item.get("bytes")
        if set(item) != {"run_id", "completed_at", "bytes"} or item["run_id"] != run_id or type(size) is not int or size < 0:
            raise StorageFailure()
        _timestamp(item["completed_at"])
        sets[run_id] = {"run_id": run_id, "completed_at": string_value(item["completed_at"]), "bytes": size}
    return refs, sets, etag


def read_retention(destination: DestinationConfig) -> RetentionRefs:
    client = _client(destination)
    try:
        return _retention(client, destination, time.monotonic() + destination.timeout_seconds)[0]
    finally:
        client.close()


def retained_sets(destination: DestinationConfig) -> list[StoredSet]:
    client = _client(destination)
    try:
        return list(_retention(client, destination, time.monotonic() + destination.timeout_seconds)[1].values())
    finally:
        client.close()


def _prune(client: S3Client, destination: DestinationConfig, refs: RetentionRefs, deadline: float) -> PruneResult:
    # Never let an out-of-date caller remove a currently referenced set.
    current, _, _ = _retention(client, destination, deadline)
    if current != refs:
        raise StorageFailure()
    prefix = _namespace(destination) + "sets/"
    token, tokens, deleted = None, set(), 0
    while True:
        _remaining(deadline)
        request: ListObjectsV2RequestTypeDef = {"Bucket": destination.bucket, "Prefix": prefix}
        if token:
            request["ContinuationToken"] = token
        page = client.list_objects_v2(**request)
        for item in page.get("Contents", []):
            key = item["Key"]
            if not isinstance(key, str) or not key.startswith(prefix):
                raise StorageFailure()
            relative = key[len(prefix):]
            parts = relative.split("/", 1)
            if len(parts) != 2:
                continue
            try:
                run_id = _run_id(parts[0])
                suffix = _path(parts[1])
            except StorageFailure:
                continue
            if run_id in protected_ids(refs) or not (suffix in ("manifest.json", "intent.json") or suffix.startswith("payload/sources/")):
                continue
            _remaining(deadline)
            client.delete_object(Bucket=destination.bucket, Key=key)
            deleted += 1
        if not page.get("IsTruncated"):
            return PruneResult(True, deleted)
        token = page.get("NextContinuationToken")
        if not token or token in tokens:
            raise StorageFailure()
        tokens.add(token)


def prune(destination: DestinationConfig, refs: RetentionRefs) -> PruneResult:
    client = None
    try:
        client = _client(destination)
        return _prune(client, destination, refs, time.monotonic() + destination.timeout_seconds)
    except Exception:
        return PruneResult(False, error=_error(destination, "cleanup_failed"))
    finally:
        if client is not None:
            client.close()


def _abort_incomplete_uploads(client: S3Client, destination: DestinationConfig, deadline: float) -> None:
    # Called only under the worker's shared run lock, before starting any upload.
    prefix = _namespace(destination) + "sets/"
    request: ListMultipartUploadsRequestTypeDef = {"Bucket": destination.bucket, "Prefix": prefix}
    seen = set()
    while True:
        _remaining(deadline)
        page = client.list_multipart_uploads(**request)
        for upload in page.get("Uploads", []):
            key = upload["Key"]
            if not isinstance(key, str) or not key.startswith(prefix):
                raise StorageFailure()
            parts = key[len(prefix):].split("/", 1)
            if len(parts) != 2:
                continue
            _run_id(parts[0])
            suffix = _path(parts[1])
            if suffix.startswith("payload/sources/"):
                _remaining(deadline)
                client.abort_multipart_upload(Bucket=destination.bucket, Key=key, UploadId=upload["UploadId"])
        if not page.get("IsTruncated"):
            return
        next_markers = (page.get("NextKeyMarker"), page.get("NextUploadIdMarker"))
        if not all(next_markers) or next_markers in seen:
            raise StorageFailure()
        seen.add(next_markers)
        key_marker, upload_marker = next_markers
        if key_marker is None or upload_marker is None:
            raise StorageFailure()
        request["KeyMarker"] = key_marker
        request["UploadIdMarker"] = upload_marker


def _delivery_result(client: S3Client, destination: DestinationConfig, run_id: str, completed_at: str,
                     total: int, refs: RetentionRefs, deadline: float) -> DeliveryResult:
    try:
        cleanup_error = _prune(client, destination, refs, deadline).error
    except Exception:
        cleanup_error = _error(destination, "cleanup_failed")
    return DeliveryResult(True, run_id, completed_at, total, refs, cleanup_error=cleanup_error)


def deliver(run_directory: Path, destination: DestinationConfig) -> DeliveryResult:
    client, run_id = None, None
    try:
        deadline = time.monotonic() + destination.timeout_seconds
        with _open(run_directory, "manifest.json") as stream:
            manifest = _manifest(_decode(stream.read(_JSON_LIMIT + 1)))
        run_id = manifest["run_id"]
        total = sum(artifact["bytes"] for artifact in manifest["artifacts"])
        # Check every local file before reserving this immutable run identity.
        for artifact in manifest["artifacts"]:
            with _open(run_directory, artifact["path"]) as stream:
                _verify_response({"Body": stream}, artifact, deadline)
        client = _client(destination)
        _abort_incomplete_uploads(client, destination, deadline)
        refs, sets, etag = _retention(client, destination, deadline)
        if refs.latest is not None and run_id not in protected_ids(refs):
            latest_raw, _ = _json_get(client, destination,
                                     _namespace(destination) + "sets/" + refs.latest + "/manifest.json", deadline)
            latest = _manifest(latest_raw, refs.latest, committed=True)
            # A lost acknowledgement can outlive the old commit's retention.
            # Compare immutable snapshot times before uploading it again. Equal
            # snapshot times do not justify replacing an already verified copy.
            if (_timestamp(manifest["completed_at"]), _timestamp(manifest["started_at"])) <= (
                    _timestamp(latest["completed_at"]), _timestamp(latest["started_at"])):
                stored = sets[refs.latest]
                if stored["completed_at"] != latest["delivery_completed_at"]:
                    raise StorageFailure()
                return _delivery_result(client, destination, refs.latest, stored["completed_at"],
                                        stored["bytes"], refs, deadline)
        base = _namespace(destination) + "sets/" + run_id + "/"
        committed_raw, _ = _json_get(client, destination, base + "manifest.json", deadline)
        committed = _manifest(committed_raw, run_id, committed=True) if committed_raw is not None else None
        if committed is not None:
            if {key: value for key, value in committed.items() if key != "delivery_completed_at"} != manifest:
                raise StorageFailure()
        _immutable_json(client, destination, base + "intent.json", manifest, deadline)
        for artifact in manifest["artifacts"]:
            key = base + "payload/" + artifact["path"]
            existing = _get(client, destination, key, deadline)
            if existing is None:
                with _open(run_directory, artifact["path"]) as stream:
                    _upload(client, destination, key, stream, artifact["bytes"], deadline)
                existing = _get(client, destination, key, deadline)
            _verify_response(existing, artifact, deadline)
        if committed is None:
            committed = {**manifest, "delivery_completed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}
            _immutable_json(client, destination, base + "manifest.json", committed, deadline)
        completed_at = committed["delivery_completed_at"]
        # A retry of an older successful commit must not move latest backward.
        if refs.latest is None or _timestamp(completed_at) >= _timestamp(sets[refs.latest]["completed_at"]):
            updated = advance_retention(refs, run_id, _timestamp(completed_at))
            sets[run_id] = {"run_id": run_id, "completed_at": completed_at, "bytes": total}
            document: RetentionDocument = {"schema_version": 1, "latest": updated.latest,
                "weekly_current": updated.weekly_current, "weekly_previous": updated.weekly_previous,
                "weekly_key": updated.weekly_key, "sets": {key: sets[key] for key in sorted(protected_ids(updated))}}
            if updated != refs:
                _remaining(deadline)
                request: PutObjectRequestTypeDef = {"Bucket": destination.bucket, "Key": _namespace(destination) + "retention.json",
                    "Body": _encode(document), "ContentType": "application/json"}
                if etag:
                    request["IfMatch"] = etag
                else:
                    request["IfNoneMatch"] = "*"
                client.put_object(**request)
            refs = updated
        return _delivery_result(client, destination, run_id, completed_at, total, refs, deadline)
    except Exception:
        return DeliveryResult(False, run_id, error=_error(destination))
    finally:
        if client is not None:
            client.close()


def _directory_descriptor(path: Path) -> int:
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _create_file(root_descriptor: int, relative: str) -> BinaryIO:
    descriptor = os.dup(root_descriptor)
    try:
        parts = PurePosixPath(_path(relative)).parts
        for part in parts[:-1]:
            try:
                os.mkdir(part, 0o700, dir_fd=descriptor)
            except FileExistsError:
                pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        file_descriptor = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                  0o600, dir_fd=descriptor)
        stream: BinaryIO = os.fdopen(file_descriptor, "wb")
        return stream
    finally:
        os.close(descriptor)


def _remove_contents(descriptor: int) -> None:
    # Cleanup is anchored to the directory we created, never a path that can
    # be redirected through a newly inserted ancestor symlink.
    with os.scandir(descriptor) as entries:
        for entry in entries:
            if entry.is_dir(follow_symlinks=False):
                child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                try:
                    _remove_contents(child)
                finally:
                    os.close(child)
                os.rmdir(entry.name, dir_fd=descriptor)
            else:
                os.unlink(entry.name, dir_fd=descriptor)


def download(destination: DestinationConfig, run_id: str, output_directory: Path) -> None:
    """Write a verified set only to a newly created private recovery directory."""
    client, created, parent_descriptor, output_descriptor = None, False, None, None
    try:
        _run_id(run_id)
        output = Path(os.path.abspath(output_directory))
        if output != output_directory or len(output.parts) < 3 or ".." in output_directory.parts:
            raise StorageFailure()
        # Descriptor traversal rejects symlink ancestors and keeps writes anchored
        # even if directories are renamed during a long download.
        parent_descriptor = _directory_descriptor(output.parent)
        try:
            os.stat(output.name, dir_fd=parent_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise StorageFailure()
        client = _client(destination)
        deadline = time.monotonic() + destination.timeout_seconds
        base = _namespace(destination) + "sets/" + run_id + "/"
        manifest_raw, _ = _json_get(client, destination, base + "manifest.json", deadline)
        manifest = _manifest(manifest_raw, run_id, committed=True)
        os.mkdir(output.name, 0o700, dir_fd=parent_descriptor)
        created = True
        output_descriptor = os.open(output.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                    dir_fd=parent_descriptor)
        for artifact in manifest["artifacts"]:
            with _create_file(output_descriptor, artifact["path"]) as stream:
                _verify_response(_get(client, destination, base + "payload/" + artifact["path"], deadline), artifact, deadline, stream)
                stream.flush()
                os.fsync(stream.fileno())
        with _create_file(output_descriptor, "manifest.json") as stream:
            stream.write(_encode(manifest))
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(output_descriptor)
    except Exception:
        if created:
            # Never follow a replacement link while cleaning up partial output.
            # Only remove the original inode; moved output remains visibly partial.
            try:
                current = os.stat(output.name, dir_fd=parent_descriptor, follow_symlinks=False)
                opened = os.fstat(output_descriptor) if output_descriptor is not None else None
                if output_descriptor is not None and opened is not None and (current.st_dev, current.st_ino) == (opened.st_dev, opened.st_ino):
                    _remove_contents(output_descriptor)
                    os.rmdir(output.name, dir_fd=parent_descriptor)
            except OSError:
                pass
        raise StorageFailure("Backup recovery download failed; verify the set and output directory.") from None
    finally:
        if output_descriptor is not None:
            os.close(output_descriptor)
        if parent_descriptor is not None:
            os.close(parent_descriptor)
        if client is not None:
            client.close()
