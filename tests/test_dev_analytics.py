from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "seed_dev_analytics",
    Path(__file__).resolve().parent.parent / "infra/scripts/seed_dev_analytics.py",
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class DemoAnalyticsTest(unittest.TestCase):
    def test_refuses_production_or_non_debug_runtime(self) -> None:
        for environment in (
            {},
            {"APP_DOMAIN": "example.com", "APP_DEBUG": "true"},
            {"APP_DOMAIN": "test.localhost", "APP_DEBUG": "false"},
        ):
            with self.subTest(environment=environment), self.assertRaises(RuntimeError):
                module.require_local_runtime(environment)

    def test_accepts_only_explicit_local_debug_runtime(self) -> None:
        module.require_local_runtime(
            {"APP_DOMAIN": "demo.localhost", "APP_DEBUG": "true"}
        )

    def test_fixture_counts_are_repeatable_and_engagement_is_bounded_by_views(
        self,
    ) -> None:
        for day in range(120):
            for source in range(6):
                views, engaged = module.daily_counts(day, 3, source)
                self.assertGreaterEqual(views, 0)
                self.assertGreaterEqual(engaged, 0)
                self.assertLessEqual(engaged, views)
                self.assertEqual((views, engaged), module.daily_counts(day, 3, source))
