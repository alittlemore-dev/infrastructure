#!/usr/bin/env python3
"""Disposable auth slots and a real SDK reader for the nginx boundary."""
from __future__ import annotations

import argparse
import json
import threading
import time
from datetime import datetime, timezone, tzinfo
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.client import HTTPResponse
from email.message import Message
from typing import cast
from unittest.mock import patch
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def serve() -> None:
    import uvicorn
    import infra_backup.status.api as backup_status_api
    from backend_sdk.auth.http import AuthApiClient, AuthApiClientConfig
    path = Path("/tmp/projection")
    path.mkdir()
    supplied = Path("/fixture/projection.json")
    now = datetime.now(timezone.utc).isoformat()
    projection = json.loads(supplied.read_text()) if supplied.exists() else {
        "schema_version": 1, "generated_at": now, "worker_heartbeat_at": now, "last_success_at": now,
        "freshness_threshold_seconds": 25200, "run_state": "idle", "destinations": [{"id": "internal", "required": True,
            "last_success_at": now, "retained_sets": [{"run_id": "fixture", "roles": ["latest", "weekly_current"],
                "complete": True, "completed_at": now, "total_bytes": 42}]}]}
    (path / "status.json").write_text(json.dumps(projection))
    fixed = datetime.fromisoformat(projection["generated_at"].replace("Z", "+00:00"))
    class Clock(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> Clock:
            return cls.fromtimestamp(fixed.timestamp(), tz=tz)

    def handler(role: str) -> type[BaseHTTPRequestHandler]:
        class Auth(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                pass
            def do_POST(self) -> None:
                if self.path != "/api/auth/verify/v2":
                    self.send_error(404)
                    return
                token = self.headers.get("Authorization", "").removeprefix("Bearer ")
                with Path("/tmp/verification-calls.jsonl").open("a") as output:
                    output.write(json.dumps({"slot": role, "token": token}) + "\n")
                if token == "outage":
                    self.send_error(503)
                    return
                if token == "timeout":
                    time.sleep(2)
                if token in {"revoked", "invalid"}:
                    self.send_error(401)
                    return
                value = {"username": "fixture", "role": role if token == "owner" else "owner" if token.startswith("alm_pat_") else token,
                    "validForSeconds": 60, "credentialType": "pat" if token.startswith("alm_pat_") else "session",
                    "credentialId": "fixture", "permissions": [], "cacheTtlSeconds": 0}
                data = json.dumps(value).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except BrokenPipeError:
                    pass
        return Auth
    for port, role in ((8082, "owner"), (8083, "admin")):
        server = ThreadingHTTPServer(("0.0.0.0", port), handler(role))
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
    client = AuthApiClient(config=AuthApiClientConfig(verify_url="http://edge:18085/api/auth/verify/v2",
        timeout_seconds=1, cache_ttl_seconds=0, max_cache_entries=1))
    with patch.object(backup_status_api, "datetime", Clock):
        uvicorn.run(backup_status_api.create_status_app(path, client), host="0.0.0.0", port=8080, access_log=False)


def response(route: str, token: str | None = None, method: str = "GET") -> tuple[int, Message, str]:
    request = Request("http://edge:8081" + route, headers={"Authorization": "Bearer " + token} if token else {}, method=method)
    try:
        result: HTTPResponse | HTTPError = cast(HTTPResponse, urlopen(request, timeout=5))
    except HTTPError as exc:
        result = exc
    with result:
        assert result.status is not None
        return result.status, result.headers, result.read().decode()


def probe(mode: str) -> None:
    if mode == "ready":
        assert response("/api/infra/healthcheck")[0] == 401
        return
    routes = ("/api/infra/healthcheck", "/api/infra/backups/sets")
    if mode in {"green", "rollback"}:
        expected = 403 if mode == "green" else 200
        deadline = time.monotonic() + 5
        while response(routes[0], "owner")[0] != expected and time.monotonic() < deadline:
            time.sleep(0.1)
        assert response(routes[0], "owner")[0] == expected
        assert response(routes[1], "owner")[0] == expected
        calls = [json.loads(line) for line in Path("/tmp/verification-calls.jsonl").read_text().splitlines()]
        assert calls[-1]["slot"] == ("admin" if mode == "green" else "owner")
        return
    for route in routes:
        for token, expected in ((None, 401), ("revoked", 401), ("user", 403), ("admin", 403), ("moderator", 403),
                                ("alm_pat_owner", 403), ("outage", 503), ("timeout", 503), ("owner", 200)):
            status, headers, body = response(route, token)
            assert status == expected, (route, token, status, body)
            assert headers["Cache-Control"] == "no-store"
            if expected != 200:
                assert "destinations" not in body and "internal" not in body
            elif route.endswith("healthcheck"):
                result = json.loads(body)
                assert result["systems"][0]["status"] == "healthy"
                Path("/tmp/healthcheck-response.json").write_text(body)
        for method in ("POST", "HEAD", "PUT", "DELETE"):
            status, headers, _ = response(route, "owner", method)
            assert status == 405 and headers["Cache-Control"] == "no-store"
    for route in ("/api/infra/projection/status.json", "/api/infra/backups/download", "/api/infra/backups/sets/one",
                  "/live", "/schema", "/status.json"):
        assert response(route, "owner")[0] == 404


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("serve", "probe"))
    parser.add_argument("mode", nargs="?", default="initial")
    args = parser.parse_args()
    serve() if args.action == "serve" else probe(args.mode)
