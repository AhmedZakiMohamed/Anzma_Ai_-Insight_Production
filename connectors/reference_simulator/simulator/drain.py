from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from .client import CoreClient, TransportFailure
from .outbox import Outbox


MAX_BACKOFF_SECONDS = 300.0


@dataclass
class DrainReport:
    exit_code: int
    attempts: int
    batches: list[dict]
    credential_fault: bool = False
    retry_exhausted: bool = False
    terminal_failures: int = 0


def _retry_after_seconds(value: str | None) -> int:
    if value is None:
        return 0
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 1


def drain_pending(
    outbox: Outbox,
    client: CoreClient,
    source_code: str,
    *,
    max_iterations: int | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    sleeper: Callable[[float], None] = time.sleep,
    rng=random,
    when_empty: Callable[[], bool | None] | None = None,
) -> DrainReport:
    """Drain one source FIFO; injected time/sleep/random make retry tests deterministic."""
    attempts = 0
    backoff = 1.0
    results: list[dict] = []
    terminal_failures = 0

    while True:
        rows = outbox.pending(source_code)
        if not rows:
            if when_empty is not None and (max_iterations is None or attempts < max_iterations):
                if when_empty() is False:
                    return DrainReport(1 if terminal_failures else 0, attempts, results,
                                       terminal_failures=terminal_failures)
                continue
            return DrainReport(1 if terminal_failures else 0, attempts, results,
                               terminal_failures=terminal_failures)
        row = rows[0]
        if max_iterations is not None and attempts >= max_iterations:
            return DrainReport(5, attempts, results, retry_exhausted=True,
                               terminal_failures=terminal_failures)
        attempts += 1
        try:
            response = client.post_batch(bytes(row["payload"]))
            network_error = False
        except (TransportFailure, TimeoutError, OSError):
            response = None
            network_error = True

        stamp = clock().astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        if network_error:
            results.append({"batch_id": row["batch_id"], "state": "pending", "outcome": "NETWORK_ERROR",
                            "attempt_at": stamp})
            status = None
            retry_after = None
        else:
            status = response.status
            retry_after = response.retry_after
            body = response.body if isinstance(response.body, dict) else {}
            encoded = json.dumps(body, sort_keys=True, separators=(",", ":"))
            code = body.get("error", {}).get("code") if isinstance(body.get("error"), dict) else None

            if status == 200:
                counts = body.get("counts", {}) if isinstance(body.get("counts"), dict) else {}
                definitive = (body.get("status") in {"ACCEPTED", "PARTIAL"}
                              and body.get("transaction") == "COMMITTED"
                              and all(isinstance(counts.get(key), int)
                                      for key in ("received", "accepted", "duplicate", "rejected")))
                if definitive:
                    outbox.mark(row["batch_id"], "done", status, encoded, completed_at=stamp)
                    results.append({"batch_id": row["batch_id"], "state": "done", "http_status": status,
                                    "accepted": counts["accepted"], "duplicate": counts["duplicate"],
                                    "rejected": counts["rejected"], "idempotent_replay": response.idempotent_replay})
                    backoff = 1.0
                    continue
                results.append({"batch_id": row["batch_id"], "state": "pending", "outcome": "INCOMPLETE_RESPONSE",
                                "attempt_at": stamp})
                status = None
            elif status in {401, 403}:
                results.append({"batch_id": row["batch_id"], "state": "pending", "outcome": "CREDENTIAL_SOURCE_FAULT",
                                "http_status": status, "code": code, "attempt_at": stamp})
                return DrainReport(4, attempts, results, credential_fault=True,
                                   terminal_failures=terminal_failures)
            elif status in {400, 409, 413, 422} or (400 <= status < 500 and status != 429):
                outbox.mark(row["batch_id"], "failed", status, encoded)
                terminal_failures += 1
                results.append({"batch_id": row["batch_id"], "state": "failed", "http_status": status,
                                "code": code or "PERMANENT_CLIENT_ERROR"})
                backoff = 1.0
                continue
            elif status == 429 or status >= 500:
                results.append({"batch_id": row["batch_id"], "state": "pending", "http_status": status,
                                "code": code or "TRANSIENT_CORE_ERROR", "attempt_at": stamp})
            else:
                outbox.mark(row["batch_id"], "failed", status, encoded)
                terminal_failures += 1
                results.append({"batch_id": row["batch_id"], "state": "failed", "http_status": status,
                                "code": code or "PERMANENT_CORE_ERROR"})
                backoff = 1.0
                continue

        if max_iterations is not None and attempts >= max_iterations:
            return DrainReport(5, attempts, results, retry_exhausted=True,
                               terminal_failures=terminal_failures)
        jitter = min(MAX_BACKOFF_SECONDS, max(0.0, float(rng.uniform(0.0, backoff))))
        delay = max(jitter, _retry_after_seconds(retry_after))
        sleeper(delay)
        backoff = min(MAX_BACKOFF_SECONDS, backoff * 2.0)
