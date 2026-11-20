from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app.services.freshness import evaluate_state, opened_in_work_hours, resolve_expectation


NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
SERIES = {
    "source_id": 4,
    "asset_id": 12,
    "asset_type": "AHU",
    "record_kind": "telemetry",
    "metric_code": "temperature",
    "event_type": None,
}


def expectation(scope="GLOBAL", **values):
    row = {
        "expectation_id": 1,
        "scope": scope,
        "source_id": None,
        "asset_id": None,
        "asset_type": None,
        "record_kind": "telemetry",
        "metric_code": None,
        "event_type": None,
        "expected_interval_seconds": 900,
        "stale_multiplier": 2,
        "missing_multiplier": 5,
        "priority": 0,
        "valid_from": NOW - timedelta(days=1),
        "valid_to": None,
        **values,
    }
    return row


class FreshnessPureFunctionTests(unittest.TestCase):
    def test_contract_thresholds_include_exact_two_e_and_five_e(self):
        exp = expectation()
        self.assertEqual(evaluate_state(NOW - timedelta(seconds=1800), NOW, exp)["freshness"], "FRESH")
        self.assertEqual(evaluate_state(NOW - timedelta(seconds=1801), NOW, exp)["freshness"], "STALE")
        self.assertEqual(evaluate_state(NOW - timedelta(seconds=4500), NOW, exp)["freshness"], "STALE")
        self.assertEqual(evaluate_state(NOW - timedelta(seconds=4501), NOW, exp)["freshness"], "MISSING")

    def test_unknown_is_distinct_from_missing_and_not_applicable(self):
        exp = expectation()
        self.assertEqual(evaluate_state(None, NOW, exp), {"freshness": "UNKNOWN", "age_seconds": None})
        self.assertEqual(evaluate_state(None, NOW, None), {"freshness": "NOT_APPLICABLE", "age_seconds": None})
        self.assertEqual(evaluate_state(NOW - timedelta(days=5), NOW, exp)["freshness"], "MISSING")

    def test_expectation_resolution_uses_contract_scope_order(self):
        rows = [
            expectation("GLOBAL", expected_interval_seconds=900),
            expectation("SOURCE", source_id=4, expected_interval_seconds=600),
            expectation("ASSET_TYPE_METRIC", asset_type="AHU", metric_code="temperature",
                        expected_interval_seconds=300),
            expectation("ASSET_METRIC", asset_id=12, metric_code="temperature",
                        expected_interval_seconds=120),
        ]
        self.assertEqual(resolve_expectation(rows, SERIES, NOW)["scope"], "ASSET_METRIC")
        rows.pop()
        self.assertEqual(resolve_expectation(rows, SERIES, NOW)["scope"], "ASSET_TYPE_METRIC")
        rows.pop()
        self.assertEqual(resolve_expectation(rows, SERIES, NOW)["scope"], "SOURCE")
        rows.pop()
        self.assertEqual(resolve_expectation(rows, SERIES, NOW)["scope"], "GLOBAL")

    def test_higher_priority_wins_within_scope(self):
        low = expectation("SOURCE", source_id=4, priority=10, expected_interval_seconds=300)
        high = expectation("SOURCE", source_id=4, priority=90, expected_interval_seconds=60, expectation_id=2)
        self.assertEqual(resolve_expectation([low, high], SERIES, NOW)["expected_interval_seconds"], 60)

    def test_validity_window_is_half_open_at_both_edges(self):
        exp = expectation("GLOBAL", valid_from=NOW, valid_to=NOW + timedelta(seconds=60))
        self.assertIsNotNone(resolve_expectation([exp], SERIES, NOW))
        self.assertIsNone(resolve_expectation([exp], SERIES, NOW + timedelta(seconds=60)))
        self.assertIsNone(resolve_expectation([exp], SERIES, NOW - timedelta(microseconds=1)))

    def test_overrides_use_their_own_multipliers(self):
        exp = expectation(expected_interval_seconds=100, stale_multiplier=1, missing_multiplier=3)
        self.assertEqual(evaluate_state(NOW - timedelta(seconds=100), NOW, exp)["freshness"], "FRESH")
        self.assertEqual(evaluate_state(NOW - timedelta(seconds=101), NOW, exp)["freshness"], "STALE")
        self.assertEqual(evaluate_state(NOW - timedelta(seconds=300), NOW, exp)["freshness"], "STALE")
        self.assertEqual(evaluate_state(NOW - timedelta(seconds=301), NOW, exp)["freshness"], "MISSING")

    def test_late_future_timestamp_is_fresh_with_zero_nonnegative_age(self):
        result = evaluate_state(NOW + timedelta(minutes=2), NOW, expectation())
        self.assertEqual(result, {"freshness": "FRESH", "age_seconds": 0})

    def test_naive_time_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            evaluate_state(datetime(2026, 11, 20, 12, 0), NOW, expectation())

    def test_opened_in_work_hours_uses_building_zone_and_weekdays(self):
        building = {"timezone": "America/New_York", "work_start_local": datetime.strptime("08:00", "%H:%M").time(),
                    "work_end_local": datetime.strptime("18:00", "%H:%M").time(), "non_working_weekdays": [6, 7]}
        self.assertTrue(opened_in_work_hours(building, datetime(2026, 11, 20, 15, 0, tzinfo=timezone.utc)))
        self.assertFalse(opened_in_work_hours(building, datetime(2026, 11, 20, 23, 0, tzinfo=timezone.utc)))
        self.assertFalse(opened_in_work_hours(building, datetime(2026, 11, 21, 15, 0, tzinfo=timezone.utc)))

    def test_opened_in_work_hours_observes_dst_from_zoneinfo(self):
        building = {"timezone": "America/New_York", "work_start_local": datetime.strptime("08:00", "%H:%M").time(),
                    "work_end_local": datetime.strptime("18:00", "%H:%M").time(), "non_working_weekdays": []}
        self.assertTrue(opened_in_work_hours(building, datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)))
        self.assertTrue(opened_in_work_hours(building, datetime(2026, 1, 20, 13, 0, tzinfo=timezone.utc)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
