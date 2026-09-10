#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path


class DevStateError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare stable local-development runtime state.")
    parser.add_argument("--repo-dir", required=True, type=Path)
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--personal-workspace-dir", required=True, type=Path)
    parser.add_argument("--competency-trainer-dir", required=True, type=Path)
    return parser.parse_args()


def ensure_directory(path: Path, mode: int = 0o700) -> None:
    if path.is_symlink():
        raise DevStateError(f"Local development directory must not be a symlink: {path}")
    if path.exists() and not path.is_dir():
        raise DevStateError(f"Local development path must be a directory: {path}")
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(mode)


def validate_checkout(path: Path, label: str) -> Path:
    try:
        resolved = path.expanduser().resolve(strict=True)
    except FileNotFoundError as exc:
        raise DevStateError(f"{label} checkout could not be found: {path}") from exc
    for relative_path in ("backend/Dockerfile",):
        candidate = resolved / relative_path
        if not candidate.is_file():
            raise DevStateError(f"{label} checkout is missing {relative_path}: {resolved}")
    return resolved


def atomic_write(path: Path, value: str, mode: int = 0o600) -> None:
    ensure_directory(path.parent)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(value)
        temporary_path.chmod(mode)
        temporary_path.replace(path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def stable_file(path: Path, factory, *, allow_empty: bool = False) -> str:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise DevStateError(f"Local development value must be a regular file: {path}")
    if path.exists():
        value = path.read_text(encoding="utf-8")
        if value or allow_empty:
            path.chmod(0o600)
            return value
        raise DevStateError(f"Local development value is unexpectedly empty: {path}")
    value = factory()
    if not value and not allow_empty:
        raise DevStateError(f"Generated local development value is empty: {path}")
    atomic_write(path, value)
    return value


def random_token(bytes_count: int = 32) -> str:
    return secrets.token_urlsafe(bytes_count)


def run_openssl(*arguments: str) -> None:
    try:
        subprocess.run(
            ["openssl", *arguments],
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as exc:
        raise DevStateError("openssl could not be found. Install it before local development.") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() or "unknown OpenSSL error"
        raise DevStateError(f"Could not generate local development PKI: {detail}") from exc


def require_complete_group(paths: tuple[Path, ...], label: str) -> bool:
    existing = [path.exists() or path.is_symlink() for path in paths]
    if any(existing) and not all(existing):
        raise DevStateError(
            f"{label} is incomplete in local development state; remove .dev-state and its "
            "Docker volumes together before recreating it."
        )
    if all(existing):
        for path in paths:
            if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
                raise DevStateError(f"{label} contains an invalid file: {path}")
        return True
    return False


def ensure_auth_key_pair(secret_dir: Path) -> tuple[Path, Path]:
    private_key = secret_dir / "auth_private_key"
    public_key = secret_dir / "auth_public_key.pem"
    if require_complete_group((private_key, public_key), "Competency Trainer auth key pair"):
        run_openssl("pkey", "-in", str(private_key), "-noout")
        run_openssl("pkey", "-pubin", "-in", str(public_key), "-noout")
        return private_key, public_key

    run_openssl(
        "genpkey",
        "-algorithm",
        "EC",
        "-pkeyopt",
        "ec_paramgen_curve:P-256",
        "-pkeyopt",
        "ec_param_enc:named_curve",
        "-out",
        str(private_key),
    )
    run_openssl("pkey", "-in", str(private_key), "-pubout", "-out", str(public_key))
    private_key.chmod(0o600)
    public_key.chmod(0o600)
    return private_key, public_key


def ensure_agent_ca(state_dir: Path, secret_dir: Path) -> None:
    root_dir = state_dir / "agent-root"
    ensure_directory(root_dir)
    root_key = root_dir / "agent-root-ca.key.pem"
    root_certificate = root_dir / "agent-root-ca.cert.pem"
    issuing_key = secret_dir / "agent_issuing_private_key"
    issuing_certificate = secret_dir / "agent_issuing_certificate"
    chain = secret_dir / "agent_certificate_chain"
    group = (root_key, root_certificate, issuing_key, issuing_certificate, chain)
    if require_complete_group(group, "Competency Trainer local agent CA"):
        run_openssl("verify", "-CAfile", str(root_certificate), str(issuing_certificate))
        return

    request = root_dir / "agent-issuing-ca.csr.pem"
    extension = root_dir / "agent-issuing-ca.ext"
    run_openssl(
        "genpkey",
        "-algorithm",
        "EC",
        "-pkeyopt",
        "ec_paramgen_curve:P-256",
        "-pkeyopt",
        "ec_param_enc:named_curve",
        "-out",
        str(root_key),
    )
    run_openssl(
        "req",
        "-x509",
        "-new",
        "-sha256",
        "-key",
        str(root_key),
        "-days",
        "3650",
        "-subj",
        "/CN=Competency Trainer Local Agent Root CA",
        "-addext",
        "basicConstraints=critical,CA:TRUE,pathlen:1",
        "-addext",
        "keyUsage=critical,keyCertSign,cRLSign",
        "-out",
        str(root_certificate),
    )
    run_openssl(
        "genpkey",
        "-algorithm",
        "EC",
        "-pkeyopt",
        "ec_paramgen_curve:P-256",
        "-pkeyopt",
        "ec_param_enc:named_curve",
        "-out",
        str(issuing_key),
    )
    run_openssl(
        "req",
        "-new",
        "-sha256",
        "-key",
        str(issuing_key),
        "-subj",
        "/CN=Competency Trainer Local Agent Issuing CA",
        "-out",
        str(request),
    )
    atomic_write(
        extension,
        "basicConstraints=critical,CA:TRUE,pathlen:0\n"
        "keyUsage=critical,keyCertSign,cRLSign\n"
        "subjectKeyIdentifier=hash\n"
        "authorityKeyIdentifier=keyid,issuer\n",
    )
    run_openssl(
        "x509",
        "-req",
        "-sha256",
        "-in",
        str(request),
        "-CA",
        str(root_certificate),
        "-CAkey",
        str(root_key),
        "-set_serial",
        "1",
        "-days",
        "1825",
        "-extfile",
        str(extension),
        "-out",
        str(issuing_certificate),
    )
    atomic_write(
        chain,
        issuing_certificate.read_text(encoding="utf-8")
        + root_certificate.read_text(encoding="utf-8"),
    )
    for path in group:
        path.chmod(0o600)
    request.unlink(missing_ok=True)
    extension.unlink(missing_ok=True)
    run_openssl("verify", "-CAfile", str(root_certificate), str(issuing_certificate))


def ensure_tls(state_dir: Path) -> None:
    tls_dir = state_dir / "tls"
    ensure_directory(tls_dir, 0o755)
    root_key = tls_dir / "local-development-ca.key.pem"
    root_certificate = tls_dir / "local-development-ca.cert.pem"
    private_key = tls_dir / "privkey.pem"
    fullchain = tls_dir / "fullchain.pem"
    group = (root_key, root_certificate, private_key, fullchain)
    has_existing_hierarchy = require_complete_group(group, "Local HTTPS certificate hierarchy")
    if has_existing_hierarchy:
        run_openssl("verify", "-CAfile", str(root_certificate), str(fullchain))
        required_hostnames = (
            "alittlemore.localhost",
            "agent.alittlemore.localhost",
            "s3.localhost",
        )
        certificate_details = subprocess.run(
            ["openssl", "x509", "-in", str(fullchain), "-noout", "-text"],
            check=False,
            capture_output=True,
            text=True,
        )
        if certificate_details.returncode == 0 and all(
            f"DNS:{hostname}" in certificate_details.stdout for hostname in required_hostnames
        ):
            return

    request = tls_dir / "server.csr.pem"
    extension = tls_dir / "server.ext"
    leaf = tls_dir / "server.cert.pem"
    if not has_existing_hierarchy:
        run_openssl(
            "genpkey",
            "-algorithm",
            "EC",
            "-pkeyopt",
            "ec_paramgen_curve:P-256",
            "-pkeyopt",
            "ec_param_enc:named_curve",
            "-out",
            str(root_key),
        )
        run_openssl(
            "req",
            "-x509",
            "-new",
            "-sha256",
            "-key",
            str(root_key),
            "-days",
            "3650",
            "-subj",
            "/CN=alittlemore.dev Local Development CA",
            "-addext",
            "basicConstraints=critical,CA:TRUE,pathlen:0",
            "-addext",
            "keyUsage=critical,keyCertSign,cRLSign",
            "-out",
            str(root_certificate),
        )
        run_openssl(
            "genpkey",
            "-algorithm",
            "EC",
            "-pkeyopt",
            "ec_paramgen_curve:P-256",
            "-pkeyopt",
            "ec_param_enc:named_curve",
            "-out",
            str(private_key),
        )
    run_openssl(
        "req",
        "-new",
        "-sha256",
        "-key",
        str(private_key),
        "-subj",
        "/CN=alittlemore.localhost",
        "-out",
        str(request),
    )
    atomic_write(
        extension,
        "basicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature,keyEncipherment\n"
        "extendedKeyUsage=serverAuth\n"
        "subjectAltName=DNS:alittlemore.localhost,DNS:agent.alittlemore.localhost,"
        "DNS:s3.localhost\n",
    )
    run_openssl(
        "x509",
        "-req",
        "-sha256",
        "-in",
        str(request),
        "-CA",
        str(root_certificate),
        "-CAkey",
        str(root_key),
        "-set_serial",
        "1",
        "-days",
        "365",
        "-extfile",
        str(extension),
        "-out",
        str(leaf),
    )
    atomic_write(
        fullchain,
        leaf.read_text(encoding="utf-8") + root_certificate.read_text(encoding="utf-8"),
        0o644,
    )
    root_key.chmod(0o600)
    root_certificate.chmod(0o644)
    private_key.chmod(0o644)
    request.unlink(missing_ok=True)
    extension.unlink(missing_ok=True)
    leaf.unlink(missing_ok=True)
    run_openssl("verify", "-CAfile", str(root_certificate), str(fullchain))


def dotenv_value(value: str) -> str:
    return json.dumps(value.replace("$", "$$"), ensure_ascii=True)


def prepare(args: argparse.Namespace) -> None:
    repo_dir = args.repo_dir.expanduser().resolve(strict=True)
    personal_workspace = validate_checkout(args.personal_workspace_dir, "Personal Workspace")
    competency_trainer = validate_checkout(args.competency_trainer_dir, "Competency Trainer")
    state_dir = args.state_dir.expanduser().absolute()
    ensure_directory(state_dir)
    state_dir = state_dir.resolve(strict=True)

    platform_secrets = state_dir / "secrets/platform"
    personal_secrets = state_dir / "secrets/personal-workspace"
    competency_secrets = state_dir / "secrets/competency-trainer"
    for directory in (platform_secrets, personal_secrets, competency_secrets):
        ensure_directory(directory)

    owner_password_file = state_dir / "owner-password"
    owner_password = stable_file(owner_password_file, lambda: random_token(18))
    values = {
        platform_secrets / "minio_root_access_key": lambda: f"local-root-{secrets.token_hex(6)}",
        platform_secrets / "minio_root_secret_key": random_token,
        platform_secrets / "databasus_minio_access_key": lambda: f"local-backup-{secrets.token_hex(6)}",
        platform_secrets / "databasus_minio_secret_key": random_token,
        personal_secrets / "app_secret_key": lambda: random_token(48),
        personal_secrets / "db_password": random_token,
        personal_secrets / "minio_access_key": lambda: f"local-personal-{secrets.token_hex(6)}",
        personal_secrets / "minio_secret_key": random_token,
        competency_secrets / "app_secret_key": lambda: random_token(48),
        competency_secrets / "db_password": random_token,
        competency_secrets / "minio_access_key": lambda: f"local-competency-{secrets.token_hex(6)}",
        competency_secrets / "minio_secret_key": random_token,
    }
    for path, factory in values.items():
        stable_file(path, factory)
    stable_file(personal_secrets / "sentry_dsn", str, allow_empty=True)
    stable_file(competency_secrets / "sentry_dsn", str, allow_empty=True)
    stable_file(competency_secrets / "owner_init_password", lambda: owner_password)

    owner_hash = personal_secrets / "owner_password_hash"
    if owner_hash.is_symlink() or (owner_hash.exists() and not owner_hash.is_file()):
        raise DevStateError(f"Local development value must be a regular file: {owner_hash}")
    if not owner_hash.exists():
        atomic_write(owner_hash, "")
    else:
        owner_hash.chmod(0o600)

    _, auth_public_key = ensure_auth_key_pair(competency_secrets)
    ensure_agent_ca(state_dir, competency_secrets)
    ensure_tls(state_dir)

    credentials = (
        f"Personal Workspace: owner / {owner_password}\n"
        f"Competency Trainer: owner / {owner_password}\n"
    )
    atomic_write(state_dir / "credentials", credentials)

    environment = {
        "PERSONAL_WORKSPACE_BUILD_CONTEXT": str(personal_workspace / "backend"),
        "COMPETENCY_BUILD_CONTEXT": str(competency_trainer / "backend"),
        "NGINX_CERTS_DIR": str(state_dir / "tls"),
        "COMPETENCY_AUTH_PUBLIC_KEY": auth_public_key.read_text(encoding="utf-8"),
        "COMPOSE_MINIO_ROOT_ACCESS_KEY_FILE": str(platform_secrets / "minio_root_access_key"),
        "COMPOSE_MINIO_ROOT_SECRET_KEY_FILE": str(platform_secrets / "minio_root_secret_key"),
        "COMPOSE_DATABASUS_MINIO_ACCESS_KEY_FILE": str(platform_secrets / "databasus_minio_access_key"),
        "COMPOSE_DATABASUS_MINIO_SECRET_KEY_FILE": str(platform_secrets / "databasus_minio_secret_key"),
        "COMPOSE_PERSONAL_WORKSPACE_APP_SECRET_KEY_FILE": str(personal_secrets / "app_secret_key"),
        "COMPOSE_PERSONAL_WORKSPACE_DB_PASSWORD_FILE": str(personal_secrets / "db_password"),
        "COMPOSE_PERSONAL_WORKSPACE_MINIO_ACCESS_KEY_FILE": str(personal_secrets / "minio_access_key"),
        "COMPOSE_PERSONAL_WORKSPACE_MINIO_SECRET_KEY_FILE": str(personal_secrets / "minio_secret_key"),
        "COMPOSE_PERSONAL_WORKSPACE_OWNER_PASSWORD_HASH_FILE": str(owner_hash),
        "COMPOSE_PERSONAL_WORKSPACE_SENTRY_DSN_FILE": str(personal_secrets / "sentry_dsn"),
        "COMPOSE_COMPETENCY_APP_SECRET_KEY_FILE": str(competency_secrets / "app_secret_key"),
        "COMPOSE_COMPETENCY_AUTH_PRIVATE_KEY_FILE": str(competency_secrets / "auth_private_key"),
        "COMPOSE_COMPETENCY_DB_PASSWORD_FILE": str(competency_secrets / "db_password"),
        "COMPOSE_COMPETENCY_MINIO_ACCESS_KEY_FILE": str(competency_secrets / "minio_access_key"),
        "COMPOSE_COMPETENCY_MINIO_SECRET_KEY_FILE": str(competency_secrets / "minio_secret_key"),
        "COMPOSE_COMPETENCY_OWNER_INIT_PASSWORD_FILE": str(competency_secrets / "owner_init_password"),
        "COMPOSE_COMPETENCY_SENTRY_DSN_FILE": str(competency_secrets / "sentry_dsn"),
        "COMPOSE_COMPETENCY_AGENT_ISSUING_CERTIFICATE_FILE": str(competency_secrets / "agent_issuing_certificate"),
        "COMPOSE_COMPETENCY_AGENT_ISSUING_PRIVATE_KEY_FILE": str(competency_secrets / "agent_issuing_private_key"),
        "COMPOSE_COMPETENCY_AGENT_CERTIFICATE_CHAIN_FILE": str(competency_secrets / "agent_certificate_chain"),
    }
    compose_environment = "".join(
        f"{name}={dotenv_value(value)}\n" for name, value in sorted(environment.items())
    )
    atomic_write(state_dir / "compose.env", compose_environment)
    if repo_dir == state_dir or repo_dir in state_dir.parents:
        relative_state = state_dir.relative_to(repo_dir)
        print(f"Local development state is ready in {relative_state}.")
    else:
        print(f"Local development state is ready in {state_dir}.")


def main() -> int:
    try:
        prepare(parse_args())
    except (DevStateError, OSError) as exc:
        print(f"prepare_dev_state.py: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
