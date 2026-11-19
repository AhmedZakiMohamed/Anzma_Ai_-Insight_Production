import logging
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.services.rate_limit import InMemoryTokenBucket, RateLimitSettings


class FakeClock:
    def __init__(self, now=0.0):
        self.now = now

    def __call__(self):
        return self.now


class IngestionRateLimitTests(unittest.TestCase):
    def test_under_limit_then_limited_with_integer_retry_after(self):
        clock = FakeClock()
        limiter = InMemoryTokenBucket(RateLimitSettings(per_minute=60, burst=2), clock)
        self.assertIsNone(limiter.retry_after("credential-a"))
        self.assertIsNone(limiter.retry_after("credential-a"))
        retry_after = limiter.retry_after("credential-a")
        self.assertIsInstance(retry_after, int)
        self.assertGreaterEqual(retry_after, 1)

    def test_retry_succeeds_after_fake_window_passes(self):
        clock = FakeClock()
        limiter = InMemoryTokenBucket(RateLimitSettings(per_minute=1, burst=1), clock)
        self.assertIsNone(limiter.retry_after("credential-a"))
        self.assertEqual(limiter.retry_after("credential-a"), 60)
        clock.now += 60
        self.assertIsNone(limiter.retry_after("credential-a"))

    def test_credentials_have_independent_buckets(self):
        limiter = InMemoryTokenBucket(RateLimitSettings(per_minute=1, burst=1), FakeClock())
        self.assertIsNone(limiter.retry_after("credential-a"))
        self.assertEqual(limiter.retry_after("credential-a"), 60)
        self.assertIsNone(limiter.retry_after("credential-b"))

    def test_invalid_or_zero_environment_values_use_defaults_and_warn(self):
        log = logging.getLogger("rate-limit-test")
        with self.assertLogs(log, level="WARNING") as captured:
            settings = RateLimitSettings.from_env({
                "ANZMA_INGEST_RATE_LIMIT_PER_MIN": "0",
                "ANZMA_INGEST_RATE_LIMIT_BURST": "not-a-number",
            }, log=log)
        self.assertEqual(settings, RateLimitSettings(per_minute=60, burst=10))
        self.assertEqual(len(captured.records), 2)
        missing = RateLimitSettings.from_env({}, log=logging.getLogger("rate-limit-quiet"))
        self.assertEqual(missing, RateLimitSettings(per_minute=60, burst=10))


if __name__ == "__main__":
    unittest.main(verbosity=2)
