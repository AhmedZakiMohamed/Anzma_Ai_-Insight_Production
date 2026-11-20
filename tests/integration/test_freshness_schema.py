"""Schema-constraint checks for migration 009 on a named disposable database."""
from __future__ import annotations

import os
import unittest
from uuid import uuid4

try:
    import psycopg
    from psycopg import errors
except ImportError:  # pragma: no cover - dependency is declared in requirements.txt
    psycopg = None
    errors = None


DATABASE = os.environ.get("ANZMA_E2_TEST_DATABASE", "")


@unittest.skipUnless(psycopg and DATABASE.startswith("anzma_e2_"),
                     "set ANZMA_E2_TEST_DATABASE to a dedicated anzma_e2_* database")
class FreshnessSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = psycopg.connect(
            host=os.environ.get("PGHOST", "127.0.0.1"),
            port=os.environ.get("PGPORT", "5432"),
            dbname=DATABASE,
            user=os.environ.get("PGUSER", "postgres"),
            password=os.environ.get("PGPASSWORD", ""),
        )

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "conn"):
            cls.conn.close()

    def setUp(self):
        self.conn.execute("BEGIN")
        self.building_id = self.conn.execute(
            "SELECT building_id FROM buildings ORDER BY building_id LIMIT 1"
        ).fetchone()[0]
        self.asset_id = self.conn.execute(
            "SELECT asset_id FROM assets ORDER BY asset_id LIMIT 1"
        ).fetchone()[0]
        self.metric_code = self.conn.execute(
            "SELECT name FROM metric_types ORDER BY metric_id LIMIT 1"
        ).fetchone()[0]
        suffix = uuid4().hex[:12]
        self.source_id = self.conn.execute(
            """INSERT INTO data_sources
                   (source_code, building_id, connector_name, connector_version)
               VALUES (%s,%s,'freshness-schema-test','1') RETURNING source_id""",
            (f"E2_SCHEMA_{suffix}", self.building_id),
        ).fetchone()[0]

    def tearDown(self):
        self.conn.rollback()

    def assert_constraint_rejects(self, sql, params, error_type):
        with self.assertRaises(error_type):
            with self.conn.transaction():
                self.conn.execute(sql, params)

    def test_global_defaults_and_existing_building_backfill(self):
        rows = self.conn.execute(
            """SELECT record_kind,event_type,expected_interval_seconds,stale_multiplier,missing_multiplier
               FROM reporting_expectations WHERE scope='GLOBAL' AND priority=0 ORDER BY record_kind"""
        ).fetchall()
        self.assertEqual(rows, [
            ("event", "heartbeat", 3600, 2, 5),
            ("source", None, 300, 2, 5),
            ("telemetry", None, 900, 2, 5),
        ])
        buildings = self.conn.execute(
            """SELECT timezone,work_start_local,work_end_local,non_working_weekdays
               FROM buildings ORDER BY building_id"""
        ).fetchall()
        self.assertTrue(buildings)
        self.assertTrue(all(
            row[0] == "Etc/GMT-2" and str(row[1]) == "08:00:00"
            and str(row[2]) == "18:00:00" and row[3] == []
            for row in buildings
        ))

    def test_scope_keys_are_exact(self):
        self.assert_constraint_rejects(
            """INSERT INTO reporting_expectations
                   (scope,source_id,record_kind,expected_interval_seconds)
               VALUES ('GLOBAL',%s,'telemetry',900)""",
            (self.source_id,), errors.CheckViolation,
        )

    def test_missing_multiplier_must_exceed_stale_multiplier(self):
        self.assert_constraint_rejects(
            """INSERT INTO reporting_expectations
                   (scope,source_id,record_kind,expected_interval_seconds,stale_multiplier,missing_multiplier)
               VALUES ('SOURCE',%s,'source',300,5,5)""",
            (self.source_id,), errors.CheckViolation,
        )

    def test_work_start_must_precede_work_end(self):
        self.assert_constraint_rejects(
            "UPDATE buildings SET work_start_local=TIME '18:00',work_end_local=TIME '08:00' WHERE building_id=%s",
            (self.building_id,), errors.CheckViolation,
        )

    def test_equal_priority_expectations_cannot_be_ambiguous(self):
        values = ("SOURCE", self.source_id, "source", 300, 2, 5, 71)
        sql = """INSERT INTO reporting_expectations
                    (scope,source_id,record_kind,expected_interval_seconds,stale_multiplier,missing_multiplier,priority)
                 VALUES (%s,%s,%s,%s,%s,%s,%s)"""
        self.conn.execute(sql, values)
        self.assert_constraint_rejects(sql, values, errors.UniqueViolation)

    def test_duplicate_gap_series_and_start_is_rejected(self):
        values = (self.asset_id, "telemetry", self.metric_code, "2026-01-01T00:00:00Z")
        sql = """INSERT INTO data_gaps(asset_id,record_kind,metric_code,gap_start)
                 VALUES (%s,%s,%s,%s)"""
        self.conn.execute(sql, values)
        self.assert_constraint_rejects(sql, values, errors.UniqueViolation)

    def test_non_working_weekdays_are_iso_1_through_7(self):
        self.assert_constraint_rejects(
            "UPDATE buildings SET non_working_weekdays=ARRAY[0]::smallint[] WHERE building_id=%s",
            (self.building_id,), errors.CheckViolation,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
