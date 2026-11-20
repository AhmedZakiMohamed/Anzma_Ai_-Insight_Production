from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from contextlib import redirect_stderr
from io import StringIO
from unittest.mock import patch
from urllib.error import HTTPError
from email.message import Message
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SIM_PATH = ROOT / "connectors/reference_simulator"
sys.path.insert(0, str(SIM_PATH))

from simulator.client import CoreClient, Result
from simulator.cli import _new_batch_id, _submit, main
from simulator.config import ConfigError, load_config
from simulator.outbox import Outbox
from simulator.records import ASSETS, canonical_json, generate_records


ANCHOR = "2026-11-20T12:00:00Z"
GOOD_ENV = {
    "ANZMA_SIMULATOR_CORE_URL": "http://localhost:18000",
    "ANZMA_SIMULATOR_API_KEY": "azk_0123456789_abcdEFGHIJKLMN",
    "ANZMA_SIMULATOR_SOURCE_CODE": "SIM_TEST",
    "ANZMA_SIMULATOR_BUILDING_CODE": "SIM_BUILDING",
    "ANZMA_SIMULATOR_CLOCK_ANCHOR": ANCHOR,
}


class SimulatorUnitTests(unittest.TestCase):
    def test_deterministic_records_and_batch_sequence_bytes(self):
        one, delivery1 = generate_records(1701, "normal", ANCHOR)
        two, delivery2 = generate_records(1701, "normal", ANCHOR)
        self.assertEqual(delivery1, delivery2)
        self.assertEqual(hashlib.sha256(canonical_json(one)).digest(), hashlib.sha256(canonical_json(two)).digest())
        self.assertEqual([canonical_json(one[i:i + 500]) for i in range(0, len(one), 500)],
                         [canonical_json(two[i:i + 500]) for i in range(0, len(two), 500)])
        config = load_config({**GOOD_ENV, "ANZMA_SIMULATOR_SEED": "1701"})
        sequence1 = [_new_batch_id(config, "normal", ANCHOR, index)
                     for index in range((len(one) + 499) // 500)]
        sequence2 = [_new_batch_id(config, "normal", ANCHOR, index)
                     for index in range((len(two) + 499) // 500)]
        self.assertEqual(hashlib.sha256(canonical_json(sequence1)).digest(),
                         hashlib.sha256(canonical_json(sequence2)).digest())

    def test_scenario_shapes(self):
        normal, mode = generate_records(10, "normal", ANCHOR)
        self.assertEqual(mode, "LIVE")
        self.assertIn("heartbeat", {record.get("event_type") for record in normal})
        fault, _ = generate_records(10, "fault", ANCHOR, "data-center-annex")
        fault_rows = [r for r in fault if r.get("asset", {}).get("external_id") == ASSETS["HVAC-CRAC-01"]["external_id"]
                      and r.get("metric_code") == "temperature" and r["observed_at"].startswith("2026-11-19")]
        self.assertTrue(any(row["value"] >= 30 for row in fault_rows))
        stale, _ = generate_records(10, "stale-silent", ANCHOR, "data-center-annex")
        self.assertFalse(any(r.get("asset", {}).get("external_id") == ASSETS["CCTV-CAM-02"]["external_id"]
                             and r["observed_at"] >= "2026-11-20T07:00:00Z" for r in stale))
        bad, _ = generate_records(10, "bad-record", ANCHOR)
        self.assertTrue(any(r.get("quality") == "MISSING" for r in bad))
        late, mode = generate_records(10, "late-out-of-order", ANCHOR)
        self.assertEqual(mode, "BACKFILL")
        self.assertGreater(late[0]["observed_at"], late[-1]["observed_at"])
        self.assertEqual(generate_records(10, "duplicate-batch", ANCHOR), ([], "LIVE"))

    def test_sqlite_persist_before_send_restart_and_done_not_resent(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "outbox.sqlite3"
            body = b'{"batch_id":"b-1","records":[]}'
            first = Outbox(path)
            first.add("b-1", "SIM_TEST", "normal", body)
            first.close()  # Simulated crash immediately before HTTP send.
            restarted = Outbox(path)
            pending = restarted.pending("SIM_TEST")
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0]["batch_id"], "b-1")
            self.assertEqual(bytes(pending[0]["payload"]), body)
            restarted.mark("b-1", "done", 200, '{"status":"ACCEPTED"}')
            self.assertEqual(restarted.pending("SIM_TEST"), [])
            restarted.close()

    def test_committed_200_marks_batch_done(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Outbox(Path(temp) / "outbox.sqlite3")
            row = store.add("done-200", "SIM_TEST", "normal", b'{"records":[]}')
            class Accepted:
                def post_batch(self, body):
                    return Result(200, {"status": "ACCEPTED", "transaction": "COMMITTED",
                                        "counts": {"received": 0, "accepted": 0, "duplicate": 0, "rejected": 0}}, False)
            status, summary = _submit(Accepted(), store, row)
            self.assertEqual(status, 0)
            self.assertEqual(summary["state"], "done")
            self.assertEqual(store.get("done-200")["status"], "done")
            self.assertEqual(store.pending("SIM_TEST"), [])
            store.close()

    def test_no_app_import_or_core_database_driver(self):
        blocked = {"psycopg", "psycopg2", "asyncpg", "pg8000", "sqlalchemy", "databases"}
        package = SIM_PATH / "simulator"
        for path in package.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                for name in names:
                    root = name.split(".", 1)[0]
                    self.assertNotEqual(root, "app", f"forbidden app import in {path}")
                    self.assertNotIn(root, blocked, f"Core DB driver import in {path}")

    def test_secret_not_in_config_repr_state_or_error_text(self):
        secret = GOOD_ENV["ANZMA_SIMULATOR_API_KEY"]
        config = load_config(GOOD_ENV)
        self.assertNotIn(secret, repr(config))
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.sqlite3"
            store = Outbox(path)
            store.add("batch-secret-test", "SIM_TEST", "normal", b'{"records":[]}')
            store.close()
            for candidate in Path(temp).glob("state.sqlite3*"):
                self.assertNotIn(secret.encode(), candidate.read_bytes())
        client = CoreClient("http://localhost:18000", secret)
        self.assertNotIn(secret, repr(client))
        response_headers = Message()
        response_headers["Retry-After"] = "7"
        error = HTTPError("http://localhost", 429, "limited", response_headers,
                          io.BytesIO(json.dumps({"error": {"code": "RATE_LIMITED", "message": secret}}).encode()))
        with patch("urllib.request.urlopen", side_effect=error):
            result = client.post_batch(b"{}")
        self.assertEqual(result.status, 429)
        self.assertEqual(result.retry_after, "7")
        self.assertNotIn(secret, repr(result))

    def test_rate_limit_leaves_batch_pending_and_terminal_422_marks_failed(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Outbox(Path(temp) / "outbox.sqlite3")
            row = store.add("pending-429", "SIM_TEST", "normal", b'{"records":[]}')
            class RateLimited:
                def post_batch(self, body):
                    return Result(429, {"error": {"code": "RATE_LIMITED"}}, False, "4")
            status, summary = _submit(RateLimited(), store, row)
            self.assertEqual(status, 1)
            self.assertEqual(summary["retry_after"], "4")
            self.assertEqual(store.pending("SIM_TEST")[0]["batch_id"], "pending-429")
            failed = store.add("schema-422", "SIM_TEST", "normal", b'{"records":[]}')
            class SchemaRejected:
                def post_batch(self, body):
                    return Result(422, {"error": {"code": "SCHEMA_INVALID"}}, False)
            status, summary = _submit(SchemaRejected(), store, failed)
            self.assertEqual(status, 1)
            self.assertEqual(summary["state"], "failed")
            self.assertEqual(store.get("schema-422")["status"], "failed")
            self.assertEqual([row["batch_id"] for row in store.pending("SIM_TEST")], ["pending-429"])
            store.close()

    def test_invalid_config_is_clear_and_does_not_echo_key(self):
        env = {**GOOD_ENV, "ANZMA_SIMULATOR_API_KEY": "bad-secret-value"}
        with self.assertRaises(ConfigError) as error:
            load_config(env)
        self.assertIn("invalid shape", str(error.exception))
        self.assertNotIn("bad-secret-value", str(error.exception))
        stderr = StringIO()
        with patch("simulator.cli.load_config", side_effect=ConfigError("invalid simulator config")), redirect_stderr(stderr):
            self.assertEqual(main(["run"]), 2)
        self.assertIn("configuration error", stderr.getvalue())


def _staging_configured() -> bool:
    required = ["ANZMA_SIMULATOR_CORE_URL", "ANZMA_SIMULATOR_TEST_DISPOSABLE",
                "ANZMA_SIMULATOR_TEST_BUILDING_ID", "ANZMA_SIMULATOR_TEST_BUILDING_CODE",
                "PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD"]
    return (os.environ.get("ANZMA_SIMULATOR_TEST_DISPOSABLE") == "1"
            and all(os.environ.get(name) for name in required))


@unittest.skipUnless(_staging_configured(),
                     "NOT RUN: set explicit disposable staging Core/PG variables to run real-Core simulator checks")
class SimulatorDisposableCoreTests(unittest.TestCase):
    """Opt-in destructive setup; only runs with explicit disposable DB acknowledgement."""

    def test_normal_duplicate_and_bad_record_against_core(self):
        if not shutil.which("psql"):
            self.skipTest("NOT RUN: psql is required for staging setup/count assertions")
        admin_env = os.environ.copy()
        source = "SIM_D2_" + uuid.uuid4().hex[:10]
        building_code = admin_env["ANZMA_SIMULATOR_TEST_BUILDING_CODE"]
        building_id = admin_env["ANZMA_SIMULATOR_TEST_BUILDING_ID"]
        seed_building = {1: "main-tower", 2: "data-center-annex"}.get(int(building_id))
        if not seed_building:
            self.skipTest("NOT RUN: disposable database building ID must match the checked-in seed catalogue (1 or 2)")
        admin = [sys.executable, str(ROOT / "scripts/ingestion_admin.py")]
        register = subprocess.run(admin + ["register-building", "--building-id", building_id,
                                           "--building-code", building_code], env=admin_env,
                                  capture_output=True, text=True)
        if register.returncode:
            self.fail("staging building registration failed; output redacted")
        create = subprocess.run(admin + ["create-source", "--source-code", source,
                                         "--building-code", building_code,
                                         "--connector-name", "reference-simulator",
                                         "--connector-version", "test"], env=admin_env,
                                capture_output=True, text=True)
        if create.returncode:
            self.fail("staging source provisioning failed; output redacted")
        token = next((line.split(": ", 1)[1] for line in create.stdout.splitlines()
                      if line.startswith("API key (shown once): ")), "")
        self.assertTrue(token.startswith("azk_"))
        catalog = json.loads((SIM_PATH / "simulator/catalogue.json").read_text())
        in_building = set(self._query(admin_env,
            "SELECT a.asset_code FROM assets a JOIN locations l USING(location_id) "
            f"WHERE l.building_id={int(building_id)} ORDER BY a.asset_code"))
        for asset in catalog["assets"]:
            if asset["asset_code"] not in in_building:
                continue
            mapped = subprocess.run(admin + ["map-asset", "--source-code", source,
                                             "--external-id", asset["external_id"],
                                             "--asset-code", asset["asset_code"]], env=admin_env,
                                    capture_output=True, text=True)
            if mapped.returncode:
                self.fail("staging asset mapping failed; output redacted")

        with tempfile.TemporaryDirectory() as temp:
            cli_env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(SIM_PATH),
                       "ANZMA_SIMULATOR_CORE_URL": os.environ["ANZMA_SIMULATOR_CORE_URL"],
                       "ANZMA_SIMULATOR_API_KEY": token,
                       "ANZMA_SIMULATOR_SOURCE_CODE": source,
                       "ANZMA_SIMULATOR_BUILDING_CODE": building_code,
                       "ANZMA_SIMULATOR_SEED_BUILDING": seed_building,
                       "ANZMA_SIMULATOR_STATE_PATH": str(Path(temp) / "state.sqlite3"),
                       "ANZMA_SIMULATOR_BATCH_RECORDS": "100",
                       "ANZMA_SIMULATOR_CLOCK_ANCHOR": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")}
            entry = [sys.executable, "-m", "simulator.cli", "run"]
            normal = subprocess.run(entry + ["--scenario", "normal", "--batches", "1"],
                                    env=cli_env, capture_output=True, text=True)
            self.assertEqual(normal.returncode, 0, "normal staging ingestion failed; output redacted")
            normal_summary = json.loads(normal.stdout.splitlines()[-1])
            self.assertGreater(normal_summary["batches"][0]["accepted"], 0)

            before = self._counts(admin_env)
            duplicate = subprocess.run(entry + ["--scenario", "duplicate-batch"], env=cli_env,
                                        capture_output=True, text=True)
            self.assertEqual(duplicate.returncode, 0, "duplicate replay failed; output redacted")
            replay = json.loads(duplicate.stdout.splitlines()[-1])
            self.assertTrue(replay["batches"][0]["idempotent_replay"])
            self.assertEqual(self._counts(admin_env), before, "duplicate replay added fact rows")

            bad = subprocess.run(entry + ["--scenario", "bad-record"], env=cli_env,
                                 capture_output=True, text=True)
            self.assertEqual(bad.returncode, 0, "bad-record partial ingestion failed; output redacted")
            bad_batch = json.loads(bad.stdout.splitlines()[-1])["batches"][0]
            rejected = bad_batch["rejected"]
            self.assertGreaterEqual(rejected, 1)
            lookup = subprocess.run(entry + ["--get-batch", bad_batch["batch_id"]], env=cli_env,
                                    capture_output=True, text=True)
            self.assertEqual(lookup.returncode, 0, "staging batch lookup failed; output redacted")
            body = json.loads(lookup.stdout.splitlines()[-1])["response"]
            self.assertTrue(any(item.get("reason_code") == "SCHEMA_INVALID" for item in body.get("rejected", [])))
            self.assertNotIn(token, normal.stdout + normal.stderr + duplicate.stdout + duplicate.stderr + bad.stdout + bad.stderr)

    def _counts(self, env):
        query = "SELECT (SELECT count(*) FROM telemetry_records),(SELECT count(*) FROM event_records)"
        return tuple(int(value) for value in self._query(env, query)[0].split("|"))

    def _query(self, env, query):
        psql = shutil.which("psql")
        if not psql:
            self.skipTest("NOT RUN: psql is required to compare disposable staging fact counts")
        result = subprocess.run([psql, "-X", "-A", "-t", "-q", "-c", query], env=env,
                                capture_output=True, text=True)
        if result.returncode:
            self.fail("could not query disposable staging counts; output redacted")
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]


if __name__ == "__main__":
    unittest.main(verbosity=2)
