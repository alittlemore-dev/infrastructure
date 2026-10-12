#!/usr/bin/env python3
"""Exercise actual SDK authentication middleware and its HTTP verification client."""
from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import httpx
from backend_sdk.auth.http import AuthApiClient, AuthApiClientConfig
from litestar.testing import TestClient

from infra_backup.status.api import create_status_app


def main() -> None:
    revoked = set()
    requests = []

    def verify(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST" and request.url.path == "/api/auth/verify/v2"
        token = request.headers["Authorization"].removeprefix("Bearer ")
        requests.append(token)
        if token == "timeout":
            raise httpx.ReadTimeout("fixture timeout")
        if token == "invalid" or token in revoked:
            return httpx.Response(401)
        return httpx.Response(200, json={"username": "fixture", "role": "owner" if token.startswith("alm_pat_") else token, "validForSeconds": 60, "credentialType": "pat" if token.startswith("alm_pat_") else "session", "credentialId": "fixture", "permissions": [], "cacheTtlSeconds": 0})

    auth = AuthApiClient(config=AuthApiClientConfig(verify_url="http://verification/api/auth/verify/v2", timeout_seconds=1, cache_ttl_seconds=0, max_cache_entries=1), http_client=httpx.AsyncClient(transport=httpx.MockTransport(verify)))
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary)
        now = datetime.now(timezone.utc).isoformat()
        (path / "status.json").write_text(json.dumps({"schema_version": 1, "generated_at": now, "worker_heartbeat_at": now, "last_success_at": now, "freshness_threshold_seconds": 25200, "run_state": "idle", "destinations": [{"id": "internal", "required": True, "last_success_at": now, "retained_sets": [{"run_id": "fixture", "roles": ["latest", "weekly_current"], "complete": True, "completed_at": now, "total_bytes": 42}]}]}))
        with TestClient(create_status_app(path, auth), raise_server_exceptions=False) as client:
            for route in ("/api/infra/healthcheck", "/api/infra/backups/sets"):
                for token, expected in ((None, 401), ("invalid", 401), ("user", 403), ("moderator", 403), ("admin", 403), ("alm_pat_owner", 403), ("timeout", 503), ("owner", 200)):
                    response = client.get(route, headers={"Authorization": f"Bearer {token}"} if token else {})
                    assert response.status_code == expected, (expected, response.status_code)
                    assert response.headers["Cache-Control"] == "no-store"
                    if expected != 200:
                        assert "destinations" not in response.text and "internal" not in response.text
                assert client.post(route, headers={"Authorization": "Bearer owner"}).status_code == 405
            revoked.add("owner")
            assert client.get("/api/infra/healthcheck", headers={"Authorization": "Bearer owner"}).status_code == 401
            assert requests.count("owner") >= 3
            (path / "status.json").unlink()
            assert client.get("/live").json() == {"alive": True}
    print("Owner/session-only API, revocation, auth outage, no-store and independent liveness passed.")


if __name__ == "__main__":
    main()
