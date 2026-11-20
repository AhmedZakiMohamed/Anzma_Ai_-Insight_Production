from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from email.message import Message
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SIM_PATH = ROOT / "connectors/reference_simulator"
sys.path.insert(0, str(SIM_PATH))

from simulator.client import CoreClient, Result, TransportFailure
from simulator.cli import _body, enqueue_keepalive_if_due
from simulator.config import load_config
from simulator.drain import drain_pending
from simulator.outbox import BufferFullError, Outbox, OutboxCorruptError

ANCHOR = "2026-11-20T12:00:00Z"
GOOD_ENV = {
    "ANZMA_SIMULATOR_CORE_URL": "http://localhost:18000",
    "ANZMA_SIMULATOR_API_KEY": "azk_0123456789_abcdEFGHIJKLMN",
    "ANZMA_SIMULATOR_SOURCE_CODE": "SIM_TEST",
    "ANZMA_SIMULATOR_BUILDING_CODE": "SIM_BUILDING",
    "ANZMA_SIMULATOR_CLOCK_ANCHOR": ANCHOR,
}


class SimulatorD3MockTests(unittest.TestCase):
    def test_rate_limit_retry_then_terminal_422_does_not_block_next_batch(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Outbox(Path(temp) / "outbox.sqlite3")
            store.add("pending-429", "SIM_TEST", "normal", b'{"batch_id":"first"}')
            store.add("after-poison", "SIM_TEST", "normal", b'{"batch_id":"second"}')
            class Responses:
                def __init__(self):
                    self.bodies = []
                    self.responses = [Result(429, {"error": {"code": "RATE_LIMITED"}}, False, "4"),
                                      Result(422, {"error": {"code": "SCHEMA_INVALID"}}, False),
                                      Result(200, {"status": "ACCEPTED", "transaction": "COMMITTED",
                                                   "counts": {"received": 1, "accepted": 1, "duplicate": 0, "rejected": 0}}, False)]
                def post_batch(self, body):
                    self.bodies.append(body)
                    return self.responses.pop(0)
            delays = []
            report = drain_pending(store, Responses(), "SIM_TEST", max_iterations=5,
                                   sleeper=delays.append, rng=type("R", (), {"uniform": lambda s, a, b: 0.0})())
            self.assertEqual(report.exit_code, 1)
            self.assertEqual(report.terminal_failures, 1)
            self.assertEqual(delays, [4])
            self.assertEqual([r["state"] for r in report.batches], ["pending", "failed", "done"])
            self.assertEqual(store.get("pending-429")["status"], "failed")
            self.assertEqual(store.get("after-poison")["status"], "done")
            store.close()

    def test_retry_same_bytes_after_network_and_5xx(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Outbox(Path(temp) / "outbox.sqlite3")
            original = b'{"batch_id":"same-id","records":[]}'
            store.add("same-id", "SIM_TEST", "normal", original)
            class Client:
                def __init__(self):
                    self.sent = []
                    self.responses = [Result(503, {"error": {"code": "SERVICE_UNAVAILABLE"}}, False),
                                      TransportFailure("simulated network failure"),
                                      Result(200, {"status": "ACCEPTED", "transaction": "COMMITTED",
                                                   "counts": {"received": 0, "accepted": 0, "duplicate": 0, "rejected": 0}}, False)]
                def post_batch(self, body):
                    self.sent.append(body)
                    response = self.responses.pop(0)
                    if isinstance(response, Exception):
                        raise response
                    return response
            client, delays = Client(), []
            rng = type("UpperRng", (), {"uniform": lambda self, a, b: b})()
            report = drain_pending(store, client, "SIM_TEST", max_iterations=4,
                                   sleeper=delays.append, rng=rng,
                                   clock=lambda: datetime(2026, 11, 20, tzinfo=timezone.utc))
            self.assertEqual(report.exit_code, 0)
            self.assertEqual(client.sent, [original, original, original])
            self.assertEqual(delays, [1, 2])
            self.assertEqual([row["batch_id"] for row in report.batches], ["same-id"] * 3)
            store.close()

    def test_backoff_cap_full_jitter_and_retry_after_then_reset(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Outbox(Path(temp) / "outbox.sqlite3")
            store.add("cap", "SIM_TEST", "normal", b'{"records":[]}')
            class Failures:
                def post_batch(self, body):
                    return Result(503, {"error": {"code": "SERVICE_UNAVAILABLE"}}, False)
            delays = []
            rng = type("UpperRng", (), {"uniform": lambda self, a, b: b})()
            report = drain_pending(store, Failures(), "SIM_TEST", max_iterations=11,
                                   sleeper=delays.append, rng=rng)
            self.assertEqual(report.exit_code, 5)
            self.assertEqual(delays, [1, 2, 4, 8, 16, 32, 64, 128, 256, 300])
            store.close()

        with tempfile.TemporaryDirectory() as temp:
            store = Outbox(Path(temp) / "outbox.sqlite3")
            store.add("one", "SIM_TEST", "normal", b'{"one":1}')
            store.add("two", "SIM_TEST", "normal", b'{"two":2}')
            class Sequence:
                def __init__(self):
                    self.responses = [Result(503, {}, False), Result(429, {}, False, "5"),
                                      Result(200, {"status": "ACCEPTED", "transaction": "COMMITTED",
                                                   "counts": {"received": 0, "accepted": 0, "duplicate": 0, "rejected": 0}}, False),
                                      Result(503, {}, False),
                                      Result(200, {"status": "ACCEPTED", "transaction": "COMMITTED",
                                                   "counts": {"received": 0, "accepted": 0, "duplicate": 0, "rejected": 0}}, False)]
                def post_batch(self, body):
                    return self.responses.pop(0)
            delays = []
            class FractionRng:
                def __init__(self): self.parts = [0.5, 0.5, 0.5]
                def uniform(self, low, high): return low + (high - low) * self.parts.pop(0)
            report = drain_pending(store, Sequence(), "SIM_TEST", max_iterations=6,
                                   sleeper=delays.append, rng=FractionRng())
            self.assertEqual(report.exit_code, 0)
            self.assertEqual(delays, [0.5, 5, 0.5])
            store.close()

    def test_auth_fault_pauses_then_resumes_exact_pending_body(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Outbox(Path(temp) / "outbox.sqlite3")
            body = b'{"batch_id":"resume-id","records":[]}'
            store.add("resume-id", "SIM_TEST", "normal", body)
            class AuthFailure:
                def post_batch(self, sent):
                    self.sent = sent
                    return Result(403, {"error": {"code": "SOURCE_MISMATCH"}}, False)
            first = AuthFailure()
            report = drain_pending(store, first, "SIM_TEST", sleeper=lambda _: self.fail("auth must not sleep"))
            self.assertEqual(report.exit_code, 4)
            self.assertTrue(report.credential_fault)
            self.assertEqual(first.sent, body)
            self.assertEqual(store.pending("SIM_TEST")[0]["batch_id"], "resume-id")
            class CorrectedKey:
                def post_batch(self, sent):
                    self.sent = sent
                    return Result(200, {"status": "ACCEPTED", "transaction": "COMMITTED",
                                        "counts": {"received": 0, "accepted": 0, "duplicate": 0, "rejected": 0}}, False)
            resumed = CorrectedKey()
            report = drain_pending(store, resumed, "SIM_TEST")
            self.assertEqual(report.exit_code, 0)
            self.assertEqual(resumed.sent, body)
            store.add("missing-auth", "SIM_TEST", "normal", b'{"batch_id":"missing-auth"}')
            class MissingAuth:
                def post_batch(self, sent):
                    self.sent = sent
                    return Result(401, {"error": {"code": "AUTH_INVALID"}}, False)
            missing = MissingAuth()
            report = drain_pending(store, missing, "SIM_TEST", sleeper=lambda _: self.fail("401 must not sleep"))
            self.assertEqual(report.exit_code, 4)
            self.assertEqual(missing.sent, b'{"batch_id":"missing-auth"}')
            self.assertEqual(store.pending("SIM_TEST")[0]["batch_id"], "missing-auth")
            store.close()

    def test_terminal_4xx_poison_batches_are_kept_but_do_not_block_fifo(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Outbox(Path(temp) / "outbox.sqlite3")
            for batch_id in ("bad-409", "bad-400", "bad-413", "bad-422", "good"):
                store.add(batch_id, "SIM_TEST", "normal", batch_id.encode())
            class Sequence:
                def __init__(self):
                    self.codes = [409, 400, 413, 422, 200]
                    self.error_codes = ["BATCH_ID_REUSED", "INVALID_CONTENT_LENGTH", "BATCH_TOO_LARGE", "SCHEMA_INVALID"]
                    self.order = []
                def post_batch(self, body):
                    self.order.append(body.decode())
                    code = self.codes.pop(0)
                    if code == 200:
                        return Result(200, {"status": "PARTIAL", "transaction": "COMMITTED",
                                            "counts": {"received": 1, "accepted": 0, "duplicate": 0, "rejected": 1}}, False)
                    return Result(code, {"error": {"code": self.error_codes.pop(0)}}, False)
            client = Sequence()
            report = drain_pending(store, client, "SIM_TEST", max_iterations=5)
            self.assertEqual(report.exit_code, 1)
            self.assertEqual(report.terminal_failures, 4)
            self.assertEqual(client.order, ["bad-409", "bad-400", "bad-413", "bad-422", "good"])
            for batch_id in ("bad-409", "bad-400", "bad-413", "bad-422"):
                self.assertEqual(store.get(batch_id)["status"], "failed")
                self.assertIn("error", store.get(batch_id)["response_json"])
            self.assertEqual(store.get("good")["status"], "done")
            store.close()

    def test_byte_cap_preserves_queue_and_drop_counter_is_persistent_and_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "outbox.sqlite3"
            queued = b'{"records":[]}'
            store = Outbox(path, max_bytes=len(queued))
            store.add("kept", "SIM_TEST", "normal", queued)
            with self.assertRaises(BufferFullError):
                store.add("not-queued", "SIM_TEST", "normal", b'{"records":[{},{}]}')
            self.assertEqual([row["batch_id"] for row in store.pending("SIM_TEST")], ["kept"])
            self.assertEqual(store.dropped_records(), 2)
            store.close()
            store = Outbox(path, max_bytes=1024)
            self.assertEqual(store.dropped_records(), 2)
            config = load_config(GOOD_ENV)
            body = json.loads(_body(config, [], "LIVE", "diagnostic", ANCHOR,
                                    store.pending_count("SIM_TEST"), store.dropped_records()))
            self.assertEqual(body["source"]["connector_status"], {
                "vendor_link": "UNKNOWN", "buffered_records": 0, "dropped_records": 2,
                "last_poll_at": ANCHOR})
            self.assertLess(len(json.dumps(body["source"]["connector_status"]).encode()), 8192)
            store.close()

    def test_cli_pauses_at_full_buffer_without_deleting_or_sending(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "outbox.sqlite3"
            store = Outbox(path, max_bytes=1024)
            payload = b"x" * 1024
            store.add("older-source-row", "OTHER_SOURCE", "normal", payload)
            store.close()
            env = {**GOOD_ENV, "ANZMA_SIMULATOR_STATE_PATH": str(path),
                   "ANZMA_SIMULATOR_BUFFER_MAX_BYTES": "1024", "PYTHONPATH": str(SIM_PATH)}
            completed = subprocess.run([sys.executable, "-m", "simulator.cli", "run"],
                                        env=env, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 6)
            self.assertIn("BUFFER_FULL", completed.stdout)
            store = Outbox(path, max_bytes=1024)
            self.assertEqual([row["batch_id"] for row in store.pending("OTHER_SOURCE")], ["older-source-row"])
            self.assertEqual(store.dropped_records(), 0)
            store.close()

    def test_corrupt_outbox_stops_startup_and_leaves_file_untouched(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "broken.sqlite3"
            damaged = b"this is deliberately not a sqlite database\x00"
            path.write_bytes(damaged)
            before = hashlib.sha256(path.read_bytes()).digest()
            with self.assertRaises(OutboxCorruptError):
                Outbox(path)
            self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), before)
            self.assertEqual(path.read_bytes(), damaged)
            env = {**GOOD_ENV, "ANZMA_SIMULATOR_STATE_PATH": str(path),
                   "PYTHONPATH": str(SIM_PATH)}
            completed = subprocess.run([sys.executable, "-m", "simulator.cli", "run"],
                                        env=env, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 3)
            self.assertIn("OUTBOX_CORRUPT", completed.stdout)
            self.assertEqual(path.read_bytes(), damaged)

    def test_keepalive_interval_fresh_ids_and_pending_suppression(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Outbox(Path(temp) / "outbox.sqlite3")
            config = load_config(GOOD_ENV)
            now = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)
            first = enqueue_keepalive_if_due(store, config, now)
            self.assertIsNotNone(first)
            self.assertEqual(json.loads(bytes(first["payload"]))["records"], [])
            self.assertIsNone(enqueue_keepalive_if_due(store, config, now + timedelta(seconds=900)))
            self.assertTrue(store.keepalive_pending("SIM_TEST"))
            stamp = now.isoformat().replace("+00:00", "Z")
            store.mark(first["batch_id"], "done", 200, '{"status":"ACCEPTED"}', completed_at=stamp)
            self.assertIsNone(enqueue_keepalive_if_due(store, config, now + timedelta(seconds=299)))
            second = enqueue_keepalive_if_due(store, config, now + timedelta(seconds=300))
            self.assertNotEqual(first["batch_id"], second["batch_id"])
            completed_at = (now + timedelta(seconds=300)).isoformat().replace("+00:00", "Z")
            store.mark(second["batch_id"], "done", 200, '{"status":"ACCEPTED"}', completed_at=completed_at)
            forced = enqueue_keepalive_if_due(store, config, now + timedelta(seconds=300), force=True)
            self.assertNotEqual(second["batch_id"], forced["batch_id"])
            store.close()

    def test_api_key_is_redacted_from_core_error_and_persisted_response(self):
        secret = GOOD_ENV["ANZMA_SIMULATOR_API_KEY"]
        client = CoreClient("http://localhost:18000", secret)
        terminal = HTTPError("http://localhost", 422, "invalid", Message(),
                             io.BytesIO(json.dumps({"error": {"code": "SCHEMA_INVALID", "message": secret},
                                                    "rejected": [{"message": secret}]}).encode()))
        with tempfile.TemporaryDirectory() as temp:
            store = Outbox(Path(temp) / "outbox.sqlite3")
            store.add("secret-error", "SIM_TEST", "normal", b'{"records":[]}')
            with patch("urllib.request.urlopen", side_effect=terminal):
                report = drain_pending(store, client, "SIM_TEST")
            self.assertEqual(report.exit_code, 1)
            saved = store.get("secret-error")["response_json"]
            self.assertNotIn(secret, saved)
            store.close()
            for candidate in Path(temp).glob("outbox.sqlite3*"):
                self.assertNotIn(secret.encode(), candidate.read_bytes())
