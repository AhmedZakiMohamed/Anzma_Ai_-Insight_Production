"""Per-process token-bucket limit for authenticated ingestion credentials.

The limiter interface is intentionally small so a shared implementation (such as
Redis) can replace this process-local backend without changing request handling.
"""
from __future__ import annotations

import logging
import math
import os
import threading
import time
from dataclasses import dataclass
from typing import Callable, Mapping, Protocol


logger = logging.getLogger("anzma.ingestion.rate_limit")


@dataclass(frozen=True)
class RateLimitSettings:
    per_minute: int = 60
    burst: int = 10

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None,
                 log: logging.Logger | None = None) -> "RateLimitSettings":
        values = os.environ if environ is None else environ
        sink = logger if log is None else log
        parsed = {}
        for name, default in (("ANZMA_INGEST_RATE_LIMIT_PER_MIN", 60),
                              ("ANZMA_INGEST_RATE_LIMIT_BURST", 10)):
            raw = values.get(name)
            if raw is None:
                parsed[name] = default
                continue
            try:
                value = int(raw)
                if value <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                sink.warning("Invalid %s; using safe default %d.", name, default)
                value = default
            parsed[name] = value
        return cls(parsed["ANZMA_INGEST_RATE_LIMIT_PER_MIN"],
                   parsed["ANZMA_INGEST_RATE_LIMIT_BURST"])


class RateLimiter(Protocol):
    def retry_after(self, key: str) -> int | None:
        """Consume one request token, or return integer seconds until retry."""


class InMemoryTokenBucket:
    """Thread-safe token bucket; burst is the bucket's maximum token capacity."""

    def __init__(self, settings: RateLimitSettings, clock: Callable[[], float] = time.monotonic):
        self.settings = settings
        self._clock = clock
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()
        self._refill_per_second = settings.per_minute / 60.0

    def retry_after(self, key: str) -> int | None:
        now = self._clock()
        with self._lock:
            tokens, updated_at = self._buckets.get(key, (float(self.settings.burst), now))
            tokens = min(float(self.settings.burst), tokens + max(0.0, now - updated_at) * self._refill_per_second)
            if tokens >= 1.0:
                self._buckets[key] = (tokens - 1.0, now)
                return None
            wait = (1.0 - tokens) / self._refill_per_second
            self._buckets[key] = (tokens, now)
            return max(1, math.ceil(wait))


ingestion_rate_limiter: RateLimiter = InMemoryTokenBucket(RateLimitSettings.from_env())
