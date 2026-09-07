#!/usr/bin/env python3
from __future__ import annotations

import sys
from ipaddress import IPv4Address, IPv4Network


PRIVATE_NETWORKS = (
    IPv4Network("10.0.0.0/8"),
    IPv4Network("172.16.0.0/12"),
    IPv4Network("192.168.0.0/16"),
)


def is_allowed_private_bind_address(raw_address: str) -> bool:
    try:
        address = IPv4Address(raw_address)
    except ValueError:
        return False
    return address.is_loopback or any(address in network for network in PRIVATE_NETWORKS)


def main() -> int:
    raw_address = sys.argv[1] if len(sys.argv) == 2 else ""
    if is_allowed_private_bind_address(raw_address):
        return 0
    print(
        "VPN_BIND_ADDRESS must be an IPv4 loopback or RFC1918 address; "
        f"got {raw_address or '<empty>'}.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
