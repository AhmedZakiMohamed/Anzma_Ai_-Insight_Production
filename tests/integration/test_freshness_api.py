"""E5 read-only API checks against a disposable PostgreSQL database."""
from __future__ import annotations

import hashlib
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg


DATABASE = os.getenv("ANZMA_E5_TEST_DATABASE", "")
if not DATABASE.startswith("anzma_e5_") or os.getenv("PGHOST") != "127.0.0.1" or os.getenv("PGPORT") != "25443":
    raise RuntimeError("E5 integration tests require disposable anzma_e5_* on 127.0.0.1:25443")

TOKEN = "e5-integration-user-token-not-for-deployment-0001"
os.environ.update(PGDATABASE=DATABASE, ANZMA_API_TOKEN=TOKEN)
from fastapi.testclient import TestClient  # noqa: E402
import app.main as App  # noqa: E402


class FreshnessApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = psycopg.connect(host="127.0.0.1", port=25443, dbname=DATABASE,
                                 user=os.environ["PGUSER"], password=os.environ.get("PGPASSWORD", ""),
                                 autocommit=True)
        cls.client = TestClient(App.api)
        cls.metric_id = cls.db.execute("SELECT metric_id FROM metric_types WHERE name='temperature'").fetchone()[0]
        cls.system_type_id = cls.db.execute("SELECT system_type_id FROM system_types WHERE code='HVAC'").fetchone()[0]

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def setUp(self):
        self.suffix = uuid4().hex[:10]
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        self.buildings = []
        self.sources = []
        self.assets = []
        self.expectations = []
        for bi in range(2):
            building = self.db.execute(
                "INSERT INTO buildings(name,building_code) VALUES (%s,%s) RETURNING building_id",
                (f"E5 {self.suffix} {bi}", f"E5B-{self.suffix}-{bi}"),
            ).fetchone()[0]
            location = self.db.execute(
                "INSERT INTO locations(building_id,floor,name) VALUES (%s,0,%s) RETURNING location_id",
                (building, f"E5 {self.suffix} location {bi}"),
            ).fetchone()[0]
            self.buildings.append((building, location))
        for si, bi in enumerate((0, 0, 1)):
            source = self.db.execute(
                "INSERT INTO data_sources(source_code,building_id,connector_name,connector_version) "
                "VALUES (%s,%s,'e5-test','1') RETURNING source_id",
                (f"E5S-{self.suffix}-{si}", self.buildings[bi][0]),
            ).fetchone()[0]
            self.sources.append(source)
        disabled = self.db.execute(
            "INSERT INTO data_sources(source_code,building_id,connector_name,connector_version,status) "
            "VALUES (%s,%s,'e5-test','1','DISABLED') RETURNING source_id",
            (f"E5S-{self.suffix}-disabled", self.buildings[1][0]),
        ).fetchone()[0]
        self.sources.append(disabled)
        for ai, (bi, si, state, lifecycle) in enumerate(((0, 0, "FRESH", "ACTIVE"),
                                                          (0, 1, "MISSING", "ACTIVE"),
                                                          (1, 2, "STALE", "ACTIVE"),
                                                          (1, 2, "FRESH", "RETIRED"),
                                                          (1, 2, "FRESH", "DISABLED"))):
            asset = self.db.execute(
                "INSERT INTO assets(asset_code,name,asset_type,system_type_id,location_id,lifecycle_status) "
                "VALUES (%s,%s,'ahu',%s,%s,%s) RETURNING asset_id",
                (f"E5A-{self.suffix}-{ai}", f"E5 asset {ai}", self.system_type_id,
                 self.buildings[bi][1], lifecycle),
            ).fetchone()[0]
            self.assets.append(asset)
            expectation = self.db.execute(
                """INSERT INTO reporting_expectations(scope,asset_id,record_kind,metric_code,
                           expected_interval_seconds,stale_multiplier,missing_multiplier,priority,valid_from)
                   VALUES ('ASSET_METRIC',%s,'telemetry','temperature',300,2,5,80,%s) RETURNING expectation_id""",
                (asset, self.now - timedelta(days=1)),
            ).fetchone()[0]
            self.expectations.append(expectation)
            self.db.execute(
                """INSERT INTO series_freshness_current(source_id,asset_id,record_kind,metric_code,freshness,
                           last_observed_at,last_received_at,age_seconds,expected_interval_seconds,evaluated_at,
                           expectation_id,expectation_version)
                   VALUES (%s,%s,'telemetry','temperature',%s,%s,%s,30,300,%s,%s,1)""",
                (self.sources[si], asset, state, self.now - timedelta(seconds=30), self.now,
                 self.now, expectation),
            )
        # Two sources in one building prove source filters do not collapse their evidence.
        for si, state, status in ((0, "ALIVE", {"vendor": "connected"}),
                                  (1, "SILENT", {"html": "<script>alert(1)</script>"}),
                                  (2, "UNKNOWN", {"payload": "x" * 9000}),
                                  (3, "DISABLED", {"status": "disabled"})):
            self.db.execute("UPDATE data_sources SET last_received_at=%s,last_success_at=%s,last_error_at=%s,last_error=%s "
                            "WHERE source_id=%s", (self.now, self.now - timedelta(hours=1), self.now,
                            "METRIC_NOT_COMPATIBLE: rejected sample", self.sources[si]))
            self.db.execute(
                """INSERT INTO source_liveness_current(source_id,state,evaluated_at,last_received_at,
                           expected_interval_seconds,age_seconds,reason_code,reason_message,active_series_count,
                           stale_series_count,missing_series_count,mass_stale,last_success_at,last_error_at,last_error,
                           connector_status)
                   VALUES (%s,%s,%s,%s,300,0,'TEST','test evidence',2,0,0,false,%s,%s,%s,%s::jsonb)""",
                (self.sources[si], state, self.now, self.now, self.now - timedelta(hours=1), self.now,
                 "METRIC_NOT_COMPATIBLE: rejected sample", json.dumps(status)),
            )
        self.open_gap_id = self._gap(self.assets[1], self.sources[1], self.now - timedelta(hours=2), None)
        self.closed_gap_id = self._gap(self.assets[2], self.sources[2], self.now - timedelta(hours=3),
                                       self.now - timedelta(hours=1))
        self.db.execute("UPDATE data_gaps SET cause='NO_DATA' WHERE gap_id=%s", (self.open_gap_id,))
        self.db.execute("UPDATE data_gaps SET cause='SOURCE_SUSPECTED' WHERE gap_id=%s", (self.open_gap_id,))

    def _gap(self, asset, source, gap_start, gap_end):
        row = self.db.execute(
            """INSERT INTO data_gaps(source_id,building_id,asset_id,record_kind,metric_code,gap_start,gap_end,
                       opened_at,last_evaluated_at,closed_by,closed_at,expected_interval_seconds,detected_at,
                       opened_in_work_hours,cause,cause_updated_at)
               SELECT %s,l.building_id,%s,'telemetry','temperature',%s,%s,%s,%s,%s,%s,300,%s,true,'UNKNOWN',%s
                 FROM assets a JOIN locations l USING(location_id) WHERE a.asset_id=%s
               RETURNING gap_id""",
            (source, asset, gap_start, gap_end, self.now, self.now,
             "valid_observation" if gap_end else None, self.now if gap_end else None,
             self.now, self.now, asset),
        ).fetchone()
        return row[0]

    def tearDown(self):
        # Every fixture is scoped to unique IDs; cleanup leaves the disposable DB repeatable.
        self.db.execute("DELETE FROM data_gap_cause_history WHERE gap_id IN (SELECT gap_id FROM data_gaps WHERE asset_id=ANY(%s))",
                        (self.assets,))
        self.db.execute("DELETE FROM data_gaps WHERE asset_id=ANY(%s)", (self.assets,))
        self.db.execute("DELETE FROM series_freshness_current WHERE asset_id=ANY(%s)", (self.assets,))
        self.db.execute("DELETE FROM source_liveness_current WHERE source_id=ANY(%s)", (self.sources,))
        self.db.execute("DELETE FROM reporting_expectations WHERE expectation_id=ANY(%s)", (self.expectations,))
        self.db.execute("DELETE FROM assets WHERE asset_id=ANY(%s)", (self.assets,))
        self.db.execute("DELETE FROM data_sources WHERE source_id=ANY(%s)", (self.sources,))
        self.db.execute("DELETE FROM locations WHERE location_id=ANY(%s)", ([x[1] for x in self.buildings],))
        self.db.execute("DELETE FROM buildings WHERE building_id=ANY(%s)", ([x[0] for x in self.buildings],))

    def auth(self, token=TOKEN):
        return {"Authorization": f"Bearer {token}"} if token is not None else {}

    def test_operator_auth_all_routes_and_source_key_is_not_operator_auth(self):
        paths = ("/api/v1/freshness", "/api/v1/data-gaps", "/api/v1/sources/liveness")
        for path in paths:
            self.assertEqual(self.client.get(path).status_code, 401)
            self.assertEqual(self.client.get(path, headers=self.auth("azk_" + "a" * 48)).status_code, 401)
            self.assertEqual(self.client.get(path, headers=self.auth("bad")).status_code, 401)
            self.assertEqual(self.client.get(path, headers=self.auth()).status_code, 200)

    def test_freshness_filters_pagination_and_external_identifiers_only(self):
        path = "/api/v1/freshness"
        response = self.client.get(path, params={"building_code": f"E5B-{self.suffix}-0", "limit": 1}, headers=self.auth())
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual((data["total"], data["limit"], data["offset"]), (2, 1, 0))
        self.assertEqual(data["next_offset"], 1)
        self.assertEqual(len(data["items"]), 1)
        self.assertNotIn("asset_id", json.dumps(data).lower())
        self.assertNotIn("source_id", json.dumps(data).lower())
        second = self.client.get(path, params={"building_code": f"E5B-{self.suffix}-0", "limit": 1, "offset": 1}, headers=self.auth()).json()
        self.assertEqual(second["total"], 2)
        self.assertIsNone(second["next_offset"])
        self.assertNotEqual(data["items"][0]["asset_code"], second["items"][0]["asset_code"])
        for params, expected in (({"source_code": f"E5S-{self.suffix}-1"}, 1),
                                 ({"asset_code": f"E5A-{self.suffix}-1"}, 1),
                                 ({"metric_code": "temperature"}, 3),
                                 ({"state": "missing"}, 1), ({"building_code": "DOES-NOT-EXIST"}, 0)):
            result = self.client.get(path, params=params, headers=self.auth())
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()["total"], expected)
        self.assertEqual(self.client.get(path, params={"state": "NOT_A_STATE"}, headers=self.auth()).status_code, 400)
        self.assertEqual(self.client.get(path, params={"limit": 501}, headers=self.auth()).status_code, 400)

    def test_liveness_separates_operational_state_from_quality_evidence(self):
        path = "/api/v1/sources/liveness"
        response = self.client.get(path, params={"building_code": f"E5B-{self.suffix}-0"}, headers=self.auth())
        self.assertEqual(response.status_code, 200, response.text)
        items = {row["source_code"]: row for row in response.json()["items"]}
        self.assertEqual(len(items), 2)
        first = items[f"E5S-{self.suffix}-0"]
        self.assertEqual(first["state"], "ALIVE")
        self.assertTrue(first["last_success_at"].endswith("Z"))
        self.assertTrue(first["last_error_at"].endswith("Z"))
        self.assertEqual(first["last_error"], "METRIC_NOT_COMPATIBLE: rejected sample")
        hostile = items[f"E5S-{self.suffix}-1"]["connector_status"]
        self.assertEqual(hostile["html"], "<script>alert(1)</script>")
        big = self.client.get(path, params={"source_code": f"E5S-{self.suffix}-2"}, headers=self.auth()).json()["items"][0]
        self.assertIsNone(big["connector_status"])
        self.assertTrue(big["connector_status_omitted"])
        self.assertEqual(self.client.get(path, params={"state": "BROKEN"}, headers=self.auth()).status_code, 400)
        disabled = self.client.get(path, params={"state": "disabled"}, headers=self.auth()).json()
        self.assertEqual(disabled["total"], 1)
        self.assertEqual(disabled["items"][0]["state"], "DISABLED")

    def test_gap_status_filters_cause_history_and_timezone_format(self):
        path = "/api/v1/data-gaps"
        opened = self.client.get(path, params={"status": "open", "source_code": f"E5S-{self.suffix}-1"}, headers=self.auth())
        self.assertEqual(opened.status_code, 200, opened.text)
        self.assertEqual(opened.json()["total"], 1)
        gap = opened.json()["items"][0]
        self.assertEqual(gap["cause"], "SOURCE_SUSPECTED")
        self.assertEqual([h["new_cause"] for h in gap["cause_history"]], ["NO_DATA", "SOURCE_SUSPECTED"])
        self.assertTrue(gap["gap_start"].endswith("Z"))
        self.assertEqual(self.client.get(path, params={"status": "closed"}, headers=self.auth()).json()["total"], 1)
        self.assertEqual(self.client.get(path, params={"cause": "UNKNOWN"}, headers=self.auth()).json()["total"], 0)
        self.assertEqual(self.client.get(path, params={"since": "not-a-time"}, headers=self.auth()).status_code, 400)
        self.assertEqual(self.client.get(path, params={"status": "bad"}, headers=self.auth()).status_code, 400)

    def test_retired_asset_omitted_snapshot_metadata_and_post_rejected(self):
        response = self.client.get("/api/v1/freshness", params={"building_code": f"E5B-{self.suffix}-1"}, headers=self.auth())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["total"], 1)
        self.assertNotIn(f"E5A-{self.suffix}-3", {item["asset_code"] for item in response.json()["items"]})
        self.assertNotIn(f"E5A-{self.suffix}-4", {item["asset_code"] for item in response.json()["items"]})
        self.assertIn(response.json()["state_counts"]["STALE"], (0, 1))
        self.assertIn("evaluation_age_seconds", response.json())
        self.assertEqual(self.client.post("/api/v1/freshness", headers=self.auth()).status_code, 405)
        before = self._hashes()
        self.client.get("/api/v1/freshness", headers=self.auth())
        self.client.get("/api/v1/sources/liveness", headers=self.auth())
        self.client.get("/api/v1/data-gaps", headers=self.auth())
        self.assertEqual(before, self._hashes())

    def test_snapshot_age_and_empty_snapshot_are_explicit(self):
        response = self.client.get("/api/v1/freshness", headers=self.auth())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["snapshot_stale"])
        old = self.now - timedelta(seconds=200)
        self.db.execute("UPDATE series_freshness_current SET evaluated_at=%s WHERE asset_id=ANY(%s)", (old, self.assets))
        self.db.execute("UPDATE source_liveness_current SET evaluated_at=%s WHERE source_id=ANY(%s)", (old, self.sources))
        stale = self.client.get("/api/v1/freshness", headers=self.auth()).json()
        self.assertTrue(stale["snapshot_stale"])
        self.db.execute("DELETE FROM series_freshness_current WHERE asset_id=ANY(%s)", (self.assets,))
        self.db.execute("DELETE FROM source_liveness_current WHERE source_id=ANY(%s)", (self.sources,))
        empty = self.client.get("/api/v1/freshness", headers=self.auth()).json()
        self.assertIsNone(empty["evaluated_at"])
        self.assertIsNone(empty["evaluation_age_seconds"])
        self.assertTrue(empty["snapshot_stale"])
        self.assertEqual(empty["items"], [])

    def _hashes(self):
        result = {}
        for table in ("series_freshness_current", "source_liveness_current", "data_gaps", "data_gap_cause_history"):
            rows = self.db.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
            result[table] = hashlib.sha256(repr(rows).encode()).hexdigest()
        return result


if __name__ == "__main__":
    unittest.main(verbosity=2)
