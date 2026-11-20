from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone

from scripts import freshness_job


class EmptyCursor:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, *_args):
        return None

    def fetchall(self):
        return []


class EmptyConnection:
    def __init__(self):
        self.commits = 0
        self.closed = False

    def cursor(self):
        return EmptyCursor()

    def commit(self):
        self.commits += 1

    def rollback(self):
        raise AssertionError("unexpected rollback")

    def close(self):
        self.closed = True


class FreshnessJobTests(unittest.TestCase):
    def test_database_environment_requires_explicit_host_port_and_database(self):
        with self.assertRaisesRegex(ValueError, "PGHOST, PGPORT"):
            freshness_job.connect_from_env({"PGDATABASE": "anzma", "PGUSER": "test"})
        with self.assertRaisesRegex(ValueError, "PGPORT must be"):
            freshness_job.connect_from_env({"PGHOST": "127.0.0.1", "PGPORT": "0",
                                            "PGDATABASE": "anzma_e3_test", "PGUSER": "test"})

    def test_loop_uses_injected_clock_and_sleeper_without_sleeping(self):
        conn = EmptyConnection()
        ticks = iter((datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc),
                      datetime(2026, 11, 20, 12, 1, tzinfo=timezone.utc)))
        delays = []
        lines = []
        result = freshness_job.run_loop(conn, interval=60, clock=lambda: next(ticks),
                                        sleeper=delays.append, max_passes=2, emit=lines.append)
        self.assertEqual(result, 2)
        self.assertEqual(delays, [60])
        self.assertGreaterEqual(conn.commits, 2)
        self.assertEqual([json.loads(line)["evaluated_at"] for line in lines],
                         ["2026-11-20T12:00:00Z", "2026-11-20T12:01:00Z"])

    def test_once_emits_summary_and_closes_connection(self):
        conn = EmptyConnection()
        lines = []
        now = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
        code = freshness_job.main(["--once"], env={"PGHOST": "127.0.0.1", "PGPORT": "25443"},
                                  connect_fn=lambda _env: conn, clock=lambda: now, emit=lines.append)
        self.assertEqual(code, 0)
        self.assertTrue(conn.closed)
        summary = json.loads(lines[0])
        self.assertEqual(summary["event"], "FRESHNESS_PASS")
        self.assertEqual(summary["series_evaluated"], 0)
        self.assertEqual(summary["states"]["NOT_APPLICABLE"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
