#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from typing import Any


def build_images(model: Any) -> list[str]:
    if not isinstance(model, dict) or not isinstance(model.get("services"), dict):
        raise ValueError("Compose model must contain a services object.")
    images: set[str] = set()
    for service_name, service in model["services"].items():
        if not isinstance(service_name, str) or not isinstance(service, dict):
            raise ValueError("Compose services must be named objects.")
        if service.get("build") is None:
            continue
        image = service.get("image")
        if not isinstance(image, str) or not image:
            raise ValueError(
                f"Compose build service {service_name} must declare an image for scanning."
            )
        images.add(image)
    return sorted(images)


def main() -> int:
    try:
        model = json.load(sys.stdin)
        for image in build_images(model):
            print(image)
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"list_compose_build_images.py: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
