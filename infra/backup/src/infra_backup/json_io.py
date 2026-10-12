"""JSON boundary helpers: external data stays unknown until narrowed."""
from __future__ import annotations

import json
from typing import Mapping, cast

JsonObject = dict[str, object]


def object_value(value: object) -> JsonObject:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError("JSON object required")
    return cast(JsonObject, value)


def list_value(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ValueError("JSON array required")
    return cast(list[object], value)


def string_value(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("JSON string required")
    return value


def unique_object(pairs: list[tuple[str, object]]) -> JsonObject:
    result: JsonObject = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def decode(data: str | bytes | bytearray) -> object:
    value: object = json.loads(data, object_pairs_hook=unique_object)
    return value


def encode(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, allow_nan=False, ensure_ascii=True, separators=(",", ":")).encode()
