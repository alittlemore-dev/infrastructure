from __future__ import annotations

from typing import Any


def allows_missing_secret(document_name: str, spec: dict[str, Any]) -> bool:
    allow_missing = spec.get("allowMissing", False)
    if not isinstance(allow_missing, bool):
        raise ValueError(f"{document_name}.{spec['name']}.allowMissing must be boolean.")
    if allow_missing and (
        (document_name, spec["name"]) != ("personal-workspace-telegram", "TELEGRAM_PROXY_URLS")
        or spec["allowEmpty"] is not True
        or spec.get("encoding") is not None
    ):
        raise ValueError("Only the optional empty Telegram proxy URL list may allow a missing secret.")
    return allow_missing
