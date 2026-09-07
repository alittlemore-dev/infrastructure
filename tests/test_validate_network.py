#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "infra/scripts"))

from validate_network import is_allowed_private_bind_address  # noqa: E402


class PrivateBindAddressTest(unittest.TestCase):
    def test_accepts_loopback_and_rfc1918_addresses(self) -> None:
        for address in ("127.0.0.1", "10.77.0.1", "172.16.0.1", "192.168.42.5"):
            with self.subTest(address=address):
                self.assertTrue(is_allowed_private_bind_address(address))

    def test_rejects_wildcard_public_ipv6_and_invalid_addresses(self) -> None:
        for address in ("0.0.0.0", "1.1.1.1", "203.0.113.10", "::1", "localhost", ""):
            with self.subTest(address=address):
                self.assertFalse(is_allowed_private_bind_address(address))


if __name__ == "__main__":
    unittest.main()
