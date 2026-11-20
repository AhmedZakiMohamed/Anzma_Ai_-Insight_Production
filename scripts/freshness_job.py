#!/usr/bin/env python3
"""Run one freshness evaluation pass or repeat it on a fixed interval.

Set PGHOST, PGPORT, PGDATABASE, and PGUSER explicitly. There are no database
host/port defaults so an accidental connection to a shared local stack is avoided.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Callable, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.adapters.database import db_adapter as da
from app.services.freshness import MAX_ERROR_CHARS, run_once


def connect_from_env(env: Mapping[str, str] | None = None) -> Any:
    settings = os.environ if env is None else env
    missing = [name for name in ("PGHOST", "PGPORT", "PGDATABASE", "PGUSER") if not settings.get(name)]
    if missing:
        raise ValueError("explicit PostgreSQL settings are required: " + ", ".join(missing))
    try:
        port = int(settings["PGPORT"])
    except ValueError as exc:
        raise ValueError("PGPORT must be an integer between 1 and 65535") from exc
    if not 1 <= port <= 65535:
        raise ValueError("PGPORT must be an integer between 1 and 65535")
    return da.connect(
        host=settings["PGHOST"], port=port, dbname=settings["PGDATABASE"],
        user=settings["PGUSER"], password=settings.get("PGPASSWORD", ""),
    )


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def run_pass(
    conn: Any,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    started = monotonic()
    now = clock()
    result = run_once(conn, now)
    duration = max(0.0, monotonic() - started)
    return {
        "event": "FRESHNESS_PASS",
        "evaluated_at": _iso_utc(result["evaluated_at"]),
        "series_evaluated": result["series_evaluated"],
        "states": result["states"],
        "gaps_opened": result["gaps_opened"],
        "gaps_closed": result["gaps_closed"],
        "pages_committed": result["pages_committed"],
        "duration_s": round(duration, 3),
    }


def run_loop(
    conn: Any,
    interval: int = 60,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    sleeper: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    max_passes: int | None = None,
    emit: Callable[[str], None] = print,
) -> int:
    if interval < 1:
        raise ValueError("interval must be at least one second")
    completed = 0
    while max_passes is None or completed < max_passes:
        summary = run_pass(conn, clock=clock, monotonic=monotonic)
        emit(json.dumps(summary, separators=(",", ":")))
        completed += 1
        if max_passes is not None and completed >= max_passes:
            break
        sleeper(interval)
    return completed


def _failure_line(exc: Exception, clock: Callable[[], datetime]) -> str:
    try:
        timestamp = _iso_utc(clock())
    except Exception:
        timestamp = None
    message = f"{type(exc).__name__}: {exc}"[:MAX_ERROR_CHARS]
    return json.dumps({"event": "FRESHNESS_PASS_FAILED", "timestamp": timestamp, "error": message},
                      separators=(",", ":"))


def main(
    argv: list[str] | None = None,
    env: Mapping[str, str] | None = None,
    connect_fn: Callable[[Mapping[str, str] | None], Any] = connect_from_env,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    sleeper: Callable[[float], None] = time.sleep,
    emit: Callable[[str], None] = print,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="run one pass and exit")
    mode.add_argument("--loop", action="store_true", help="run continuously until interrupted or a pass fails")
    parser.add_argument("--interval", type=int, default=60, help="seconds between loop passes (default: 60)")
    args = parser.parse_args(argv)
    if args.interval < 1:
        parser.error("--interval must be at least one second")
    try:
        conn = connect_fn(env)
    except Exception as exc:
        emit(_failure_line(exc, clock))
        return 1
    try:
        if args.once:
            emit(json.dumps(run_pass(conn, clock=clock), separators=(",", ":")))
        else:
            run_loop(conn, interval=args.interval, clock=clock, sleeper=sleeper, emit=emit)
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        emit(_failure_line(exc, clock))
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
