from datetime import datetime, timedelta, timezone

from unittesting import DeferrableTestCase

from GitSavvy.core.dates import fuzzy, is_younger_than


class TestFuzzyDate(DeferrableTestCase):
    def test_describes_recent_and_old_dates(self):
        base = datetime(2025, 1, 10, 12, 0, 0)

        self.assertEqual(fuzzy(base - timedelta(seconds=30), base), "30 seconds ago")
        self.assertEqual(fuzzy(base - timedelta(minutes=5, seconds=3), base), "5 mins and 3 secs ago")
        self.assertEqual(fuzzy(base - timedelta(days=3), base), "over 3 days ago")
        self.assertEqual(fuzzy(datetime(2024, 12, 1), base), "Dec 1, 2024")

    def test_parses_formatted_dates(self):
        self.assertEqual(
            fuzzy("2025-01-10", datetime(2025, 1, 11), "%Y-%m-%d"),
            "over a day ago"
        )


class TestIsYoungerThan(DeferrableTestCase):
    def test_compares_a_timestamp_to_now(self):
        now = datetime(2025, 1, 10, 12, 0, 0)
        timestamp = int(datetime(2025, 1, 9, 12, 0, 0, tzinfo=timezone.utc).timestamp())

        self.assertTrue(is_younger_than(timedelta(days=2), now, timestamp))
        self.assertFalse(is_younger_than(timedelta(hours=12), now, timestamp))
