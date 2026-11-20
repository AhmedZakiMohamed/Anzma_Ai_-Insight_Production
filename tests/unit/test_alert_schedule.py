import unittest
from datetime import datetime, time, timezone

from app.domain.alerts.alert_engine import is_occupied, local_hour


class BuildingScheduleTests(unittest.TestCase):
    def test_seed_default_schedule_matches_previous_utc_plus_two_window(self):
        seed_building = {
            "timezone": "Etc/GMT-2",
            "work_start_local": time(8),
            "work_end_local": time(18),
            "non_working_weekdays": [],
        }
        self.assertFalse(is_occupied(datetime(2026, 1, 5, 5, 59, tzinfo=timezone.utc), seed_building))
        self.assertTrue(is_occupied(datetime(2026, 1, 5, 6, 0, tzinfo=timezone.utc), seed_building))
        self.assertTrue(is_occupied(datetime(2026, 1, 5, 15, 59, tzinfo=timezone.utc), seed_building))
        self.assertFalse(is_occupied(datetime(2026, 1, 5, 16, 0, tzinfo=timezone.utc), seed_building))

    def test_buildings_use_independent_local_zones(self):
        cairo = {"timezone": "Africa/Cairo", "work_start_local": time(8), "work_end_local": time(18),
                 "non_working_weekdays": []}
        new_york = {"timezone": "America/New_York", "work_start_local": time(8), "work_end_local": time(18),
                    "non_working_weekdays": []}
        instant = datetime(2026, 7, 20, 6, 30, tzinfo=timezone.utc)
        self.assertTrue(is_occupied(instant, cairo))
        self.assertFalse(is_occupied(instant, new_york))
        self.assertEqual(local_hour(instant, cairo), 9)
        self.assertEqual(local_hour(instant, new_york), 2)

    def test_dst_changes_local_classification_for_same_utc_clock_time(self):
        new_york = {"timezone": "America/New_York", "work_start_local": time(8), "work_end_local": time(18),
                    "non_working_weekdays": []}
        winter = datetime(2026, 1, 20, 12, 30, tzinfo=timezone.utc)
        summer = datetime(2026, 7, 20, 12, 30, tzinfo=timezone.utc)
        self.assertFalse(is_occupied(winter, new_york))  # 07:30 EST
        self.assertTrue(is_occupied(summer, new_york))   # 08:30 EDT

    def test_configured_non_working_weekday_is_off_hours(self):
        schedule = {"timezone": "Africa/Cairo", "work_start_local": time(8), "work_end_local": time(18),
                    "non_working_weekdays": [5, 6]}
        friday = datetime(2026, 7, 24, 8, 0, tzinfo=timezone.utc)
        self.assertFalse(is_occupied(friday, schedule))


if __name__ == "__main__":
    unittest.main(verbosity=2)
