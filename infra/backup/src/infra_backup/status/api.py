"""Read-only owner diagnostics using the shared authentication middleware."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from backend_sdk.auth import AuthenticationClient, RoleEnum
from backend_sdk.auth.http import AuthApiClient, AuthApiClientConfig
from backend_sdk.integrations.litestar import AuthPlugin, RequireRole
from litestar import Litestar, get
from litestar.datastructures import MutableScopeHeaders
from litestar.types import Message, Scope

from infra_backup.contracts import HealthcheckEnvelope, RetainedSetsEnvelope
from infra_backup.status.projection import read_healthcheck, read_retained_sets


async def no_store(message: Message, scope: Scope) -> None:
    if message["type"] == "http.response.start":
        MutableScopeHeaders.from_message(message)["Cache-Control"] = "no-store"


def create_status_app(projection_dir: Path, auth_client: AuthenticationClient) -> Litestar:
    @get("/api/infra/healthcheck", guards=[RequireRole(RoleEnum.OWNER)], cache=False, opt={"pat_session_only": True})
    async def healthcheck() -> HealthcheckEnvelope:
        return read_healthcheck(projection_dir, datetime.now(timezone.utc))

    @get("/api/infra/backups/sets", guards=[RequireRole(RoleEnum.OWNER)], cache=False, opt={"pat_session_only": True})
    async def retained_sets() -> RetainedSetsEnvelope:
        return read_retained_sets(projection_dir, datetime.now(timezone.utc))

    @get("/live", opt={"auth_public": True}, include_in_schema=False)
    async def liveness() -> dict[str, bool]:
        return {"alive": True}

    return Litestar(route_handlers=[healthcheck, retained_sets, liveness], plugins=[AuthPlugin(auth_client=auth_client)], before_send=[no_store], openapi_config=None, debug=False)


def create_app() -> Litestar:
    client = AuthApiClient(config=AuthApiClientConfig(verify_url=os.environ["BACKUP_AUTH_VERIFY_URL"], timeout_seconds=5, cache_ttl_seconds=0, max_cache_entries=1))
    return create_status_app(Path(os.environ.get("BACKUP_PROJECTION_DIR", "/projection")), client)
