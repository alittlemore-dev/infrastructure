"""Collect private recovery artifacts; failures never expose process output."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Mapping

from infra_backup.credentials import read_secret
from infra_backup.config import BackupConfig, SourceConfig, _recovery_path
from infra_backup.state import private_directory, write_json_atomic
from infra_backup.contracts import Artifact, SourceResult
from infra_backup.json_io import JsonObject, decode, object_value

if TYPE_CHECKING:
    from mypy_boto3_s3.client import S3Client
    from mypy_boto3_s3.type_defs import GetObjectRequestTypeDef, ListObjectsV2RequestTypeDef, ObjectTypeDef


@dataclass(frozen=True)
class CollectionResult:
    complete: bool
    sources: list[SourceResult]
    artifacts: list[Artifact]


class SourceFailure(Exception):
    """A private failure, replaced by a fixed public message at the boundary."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise SourceFailure()
    return remaining


def _command(arguments: list[str], deadline: float, environment: Mapping[str, str] | None = None) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(arguments, env=environment, stdin=subprocess.DEVNULL,
                          capture_output=True, check=False, timeout=_remaining(deadline))


def _checked(arguments: list[str], deadline: float, environment: Mapping[str, str] | None = None) -> bytes:
    result = _command(arguments, deadline, environment)
    if result.returncode:
        raise SourceFailure()
    return result.stdout


def _artifact(path: Path, root: Path, deadline: float) -> Artifact:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            _remaining(deadline)
            size += len(chunk)
            digest.update(chunk)
    path.chmod(0o600)
    return {"path": path.relative_to(root).as_posix(), "bytes": size, "sha256": digest.hexdigest()}


def _postgres(source: SourceConfig, directory: Path, deadline: float) -> None:
    # libpq treats a dbname containing '=' or a URI as a connection string.
    # Never let inventory values override the secret-file connection settings.
    if (not source.database or "=" in source.database or "://" in source.database
            or source.host is None or source.port is None or source.user is None or source.password_file is None):
        raise SourceFailure()
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
    environment.update(LC_ALL="C", PGCONNECT_TIMEOUT=str(max(1, min(15, int(_remaining(deadline))))))
    versions = {}
    for binary in ("pg_dump", "pg_dumpall", "pg_restore", "psql"):
        version = _checked([binary, "--version"], deadline, environment).decode().strip()
        if not re.fullmatch(r"[a-z_]+ \(PostgreSQL\) 18(?:\.[0-9]+)*(?: \([^\r\n]*\))?", version):
            raise SourceFailure()
        versions[binary] = version
    connection = ["--host", source.host, "--port", str(source.port), "--username", source.user, "--no-password"]
    def escape(value: str) -> str:
        if any(c in value for c in "\r\n\x00"):
            raise SourceFailure()
        return value.replace("\\", "\\\\").replace(":", "\\:")
    with tempfile.TemporaryDirectory(prefix=".credentials-", dir=directory) as temporary:
        pgpass = Path(temporary) / "pgpass"
        with pgpass.open("x", encoding="utf-8") as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(":".join(escape(str(value)) for value in
                                  (source.host, source.port, source.database, source.user, read_secret(source.password_file))) + "\n")
        environment["PGPASSFILE"] = str(pgpass)
        environment["PGOPTIONS"] = "-c lock_timeout=5000 -c statement_timeout=0"
        server_version = _checked(["psql", *connection, "--dbname", source.database, "--no-psqlrc", "--tuples-only", "--no-align", "--command", "SHOW server_version"], deadline, environment).decode().strip()
        if not re.fullmatch(r"18(?:\.[0-9]+)*(?: \([^\r\n]*\))?", server_version):
            raise SourceFailure()
        dump = directory / "database.dump"
        command = ["pg_dump", *connection, "--dbname", source.database, "--format", "custom", "--lock-wait-timeout=5000", "--file", str(dump)]
        for attempt in range(2):
            result = _command(command, deadline, environment)
            if not result.returncode:
                break
            dump.unlink(missing_ok=True)
            # PostgreSQL uses these diagnostics for concurrent DDL / lock timeout.
            ddl = any(marker in result.stderr.lower() for marker in
                      (b"lock timeout", b"could not obtain lock", b"deadlock detected", b"cache lookup failed", b"does not exist"))
            if attempt or not ddl:
                raise SourceFailure()
        _checked(["pg_restore", "--list", str(dump)], deadline, environment)
        _checked(["pg_dumpall", *connection, "--database", source.database, "--globals-only", "--no-role-passwords", "--file", str(directory / "globals.sql")], deadline, environment)
        if dump.stat().st_size == 0 or (directory / "globals.sql").stat().st_size == 0:
            raise SourceFailure()
    write_json_atomic(directory / "postgres.json", {"database": source.database, "server_version": server_version, "client_versions": versions})


def _valkey(source: SourceConfig, directory: Path, deadline: float) -> None:
    if source.host is None or source.port is None:
        raise SourceFailure()
    output = directory / "snapshot.rdb"
    _checked(["valkey-cli", "-h", source.host, "-p", str(source.port), "--rdb", str(output)], deadline)
    with output.open("rb") as stream:
        header = stream.read(9)
        stream.seek(-9, os.SEEK_END)
        trailer = stream.read(9)
    if not re.fullmatch(b"(?:REDIS0|VALKEY)[0-9]{3}", header) or trailer[:1] != b"\xff":
        raise SourceFailure()
    # Validate both the RDB structure and checksum with the matching client tools.
    _checked(["valkey-check-rdb", str(output)], deadline)


def _s3_client(source: SourceConfig, deadline: float) -> S3Client:
    import boto3
    from botocore.config import Config
    if source.access_key_file is None or source.secret_key_file is None:
        raise SourceFailure()
    timeout = max(1, min(30, _remaining(deadline)))
    return boto3.client("s3", endpoint_url=source.endpoint, region_name=source.region,
                        aws_access_key_id=read_secret(source.access_key_file),
                        aws_secret_access_key=read_secret(source.secret_key_file),
                        config=Config(connect_timeout=timeout, read_timeout=timeout,
                                      retries={"max_attempts": 0}, s3={"addressing_style": "path"}))


def _inventory(client: S3Client, source: SourceConfig, deadline: float) -> list[ObjectTypeDef]:
    if source.bucket is None:
        raise SourceFailure()
    objects: list[ObjectTypeDef] = []
    seen = set()
    token = None
    tokens = set()
    while True:
        _remaining(deadline)
        request: ListObjectsV2RequestTypeDef = {"Bucket": source.bucket}
        if token:
            request["ContinuationToken"] = token
        page = client.list_objects_v2(**request)
        for item in page.get("Contents", []):
            if item["Key"] in seen or not item.get("ETag"):
                raise SourceFailure()
            seen.add(item["Key"])
            objects.append(item)
        if not page.get("IsTruncated"):
            return sorted(objects, key=lambda item: item["Key"])
        token = page.get("NextContinuationToken")
        if not token or token in tokens:
            raise SourceFailure()
        tokens.add(token)


def _s3(source: SourceConfig, directory: Path, deadline: float) -> None:
    if source.bucket is None:
        raise SourceFailure()
    client = _s3_client(source, deadline)
    try:
        client.head_bucket(Bucket=source.bucket)
        inventory = _inventory(client, source, deadline)
        index: list[JsonObject] = []
        write_json_atomic(directory / "objects.json", {"bucket": source.bucket, "complete": False, "objects": index})
        for item in inventory:
            _remaining(deadline)
            key = item["Key"]
            selected = client.head_object(Bucket=source.bucket, Key=key, IfMatch=item["ETag"])
            if selected["ETag"] != item["ETag"] or selected["ContentLength"] != item["Size"]:
                raise SourceFailure()
            version = selected.get("VersionId")
            arguments: GetObjectRequestTypeDef = {"Bucket": source.bucket, "Key": key, "IfMatch": item["ETag"]}
            if version:
                arguments["VersionId"] = version
            response = client.get_object(**arguments)
            metadata_fields = ("ContentType", "Metadata", "CacheControl", "ContentDisposition", "ContentEncoding", "ContentLanguage", "Expires")
            if any(selected.get(field) != response.get(field) for field in metadata_fields):
                response["Body"].close()
                raise SourceFailure()
            name = hashlib.sha256((source.bucket + "\x00" + key).encode()).hexdigest() + ".object"
            output = directory / name
            body = response["Body"]
            try:
                with output.open("xb") as stream:
                    os.fchmod(stream.fileno(), 0o600)
                    while True:
                        _remaining(deadline)
                        chunk = body.read(1024 * 1024)
                        if not chunk:
                            break
                        stream.write(chunk)
            finally:
                body.close()
            artifact = _artifact(output, directory, deadline)
            if artifact["bytes"] != item["Size"] or response["ContentLength"] != item["Size"] or response["ETag"] != item["ETag"] or (version and response.get("VersionId") != version):
                raise SourceFailure()
            if not version:
                after = client.head_object(Bucket=source.bucket, Key=key, IfMatch=item["ETag"])
                fields: tuple[str, ...] = ("ETag", "ContentLength", "LastModified", "ContentType", "Metadata", "CacheControl", "ContentDisposition", "ContentEncoding", "ContentLanguage", "Expires")
                if any(selected.get(field) != after.get(field) for field in fields):
                    raise SourceFailure()
            response_fields: Mapping[str, object] = response
            headers: JsonObject = {field: response_fields[field] for field in ("ContentType", "CacheControl", "ContentDisposition", "ContentEncoding", "ContentLanguage") if field in response_fields}
            if "Expires" in response:
                headers["Expires"] = response["Expires"].isoformat()
            index.append({"bucket": source.bucket, "key": key, **artifact, "version_id": version,
                          "content_type": response.get("ContentType"), "metadata": response.get("Metadata", {}), "headers": headers})
            write_json_atomic(directory / "objects.json", {"bucket": source.bucket, "complete": False, "objects": index})
        # The list itself is not an atomic snapshot. Surface concurrent changes.
        fields = ("Key", "ETag", "Size", "LastModified")
        refreshed = _inventory(client, source, deadline)
        if [[item.get(field) for field in fields] for item in inventory] != [[item.get(field) for field in fields] for item in refreshed]:
            raise SourceFailure()
        write_json_atomic(directory / "objects.json", {"bucket": source.bucket, "complete": True, "objects": index})
    finally:
        client.close()


def _read_recovery(root: Path, relative: str) -> bytes:
    _recovery_path(relative)
    # Walk using directory descriptors: parent symlinks must not escape the root.
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parts = Path(relative).parts
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        file_descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=descriptor)
        with os.fdopen(file_descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > 16 * 1024 * 1024:
                raise SourceFailure()
            data = stream.read(16 * 1024 * 1024 + 1)
            after = os.fstat(stream.fileno())
        if len(data) != before.st_size or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise SourceFailure()
        return data
    finally:
        os.close(descriptor)


def _encrypted_sops(data: bytes) -> None:
    # The runtime inventory contains flat env-key YAML documents. Reject mixed
    # plaintext/ciphertext too; filestatus alone only verifies SOPS metadata.
    text = data.decode()
    secrets, separator, metadata = text.partition("\nsops:\n")
    if not separator or not re.search(r"^    mac: ENC\[AES256_GCM,.*\]$", metadata, re.MULTILINE):
        raise SourceFailure()
    if any(line.strip() and not line.startswith("    ") for line in metadata.splitlines()):
        raise SourceFailure()
    values = [line for line in secrets.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if not values or any(not re.fullmatch(r"[A-Z][A-Z0-9_]*: ENC\[AES256_GCM,data:[^\]\r\n]*,iv:[^\]\r\n]+,tag:[^\]\r\n]+,type:str\]", line) for line in values):
        raise SourceFailure()


def _config(source: SourceConfig, root: Path, directory: Path, deadline: float, metadata_path: Path | None) -> None:
    for relative in source.paths:
        _remaining(deadline)
        data = _read_recovery(root, relative)
        if relative.endswith(".sops.yaml"):
            _encrypted_sops(data)
        output = directory / relative
        private_directory(output.parent)
        with output.open("xb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(data)
    if metadata_path is not None:
        # Explicitly injected deployment metadata, never an archive of deploy state.
        descriptor = os.open(metadata_path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise SourceFailure()
            value = object_value(decode(stream.read(1024 * 1024 + 1)))
        if (not isinstance(value, dict) or set(value) != {"infra_release", "application_images"}
                or not isinstance(value["infra_release"], str)
                or not re.fullmatch(r"[0-9a-f]{40}", value["infra_release"])
                or not isinstance(value["application_images"], dict)
                or not value["application_images"]
                or any(not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", key)
                       or not isinstance(image, str) or not re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", image)
                       for key, image in value["application_images"].items())):
            raise SourceFailure()
        write_json_atomic(directory / "deployment.json", value)


def collect_sources(config: BackupConfig, run_directory: Path, *, deployment_metadata_path: Path | None = None) -> CollectionResult:
    """Prepare each source independently, retaining partial private artifacts."""
    private_directory(run_directory)
    sources: list[SourceResult] = []
    artifacts: list[Artifact] = []
    for source in config.sources:
        started = _now()
        deadline = time.monotonic() + source.timeout_seconds
        result: SourceResult = {"id": source.id, "type": source.type, "status": "failed", "started_at": started,
                               "complete": False, "artifacts": []}
        source_artifacts: list[Artifact] = []
        try:
            if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", source.id):
                raise SourceFailure()
            directory = run_directory / "sources" / source.id
            if directory.exists():
                raise SourceFailure()
            private_directory(directory)
            if source.type == "postgres":
                _postgres(source, directory, deadline)
            elif source.type == "valkey":
                _valkey(source, directory, deadline)
            elif source.type == "s3":
                if source.bucket in {destination.bucket for destination in config.destinations}:
                    raise SourceFailure()
                _s3(source, directory, deadline)
            elif source.type == "config":
                _config(source, config.recovery_root, directory, deadline, deployment_metadata_path)
            else:
                raise SourceFailure()
            for path in sorted(directory.rglob("*")):
                if path.is_file() and not path.is_symlink():
                    source_artifacts.append(_artifact(path, run_directory, deadline))
            result["status"] = "success"
        except Exception:
            result["error"] = {"code": "source_failed", "source_id": source.id, "message": "Source collection failed; inspect the source and retry."}
        result.update({"complete": result["status"] == "success", "finished_at": _now(),
                       "bytes": sum(item["bytes"] for item in source_artifacts), "artifacts": source_artifacts})
        artifacts.extend(source_artifacts)
        sources.append(result)
    return CollectionResult(bool(sources) and all(source["status"] == "success" for source in sources), sources, artifacts)
