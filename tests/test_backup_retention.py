import sys
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "infra/backup/src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "infra/scripts"))
from infra_backup.retention import RetentionRefs, advance_retention, protected_ids


class RetentionTests(unittest.TestCase):
    def test_weekly_checkpoint_does_not_follow_latest(self):
        refs = advance_retention(RetentionRefs(), "monday", datetime(2026, 10, 12, tzinfo=timezone.utc))
        refs = advance_retention(refs, "tuesday", datetime(2026, 10, 13, tzinfo=timezone.utc))
        self.assertEqual(refs.latest, "tuesday")
        self.assertEqual(refs.weekly_current, "monday")
        self.assertEqual(protected_ids(refs), {"monday", "tuesday"})

    def test_weeks_gaps_and_iso_year(self):
        refs = advance_retention(RetentionRefs(), "a", datetime(2026, 12, 28, tzinfo=timezone.utc))
        refs = advance_retention(refs, "b", datetime(2027, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(refs.weekly_key, "2026-W53")
        refs = advance_retention(refs, "c", datetime(2027, 1, 18, tzinfo=timezone.utc))
        self.assertEqual(protected_ids(refs), {"a", "c"})
        refs = advance_retention(refs, "d", datetime(2027, 1, 19, tzinfo=timezone.utc))
        self.assertEqual(protected_ids(refs), {"a", "c", "d"})

    def test_boundary_is_utc_and_time_is_monotonic(self):
        refs = advance_retention(RetentionRefs(), "a", datetime(2026, 10, 12, 1, tzinfo=timezone(timedelta(hours=4))))
        self.assertEqual(refs.weekly_key, "2026-W41")
        with self.assertRaises(ValueError):
            advance_retention(refs, "b", datetime(2026, 10, 1, tzinfo=timezone.utc))
        with self.assertRaises(ValueError):
            advance_retention(refs, "b", datetime(2026, 10, 12))
