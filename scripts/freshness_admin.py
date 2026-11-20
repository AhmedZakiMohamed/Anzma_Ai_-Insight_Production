"""Provision building schedules and reporting expectations for freshness evaluation."""
from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
import socket
import sys
from datetime import datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.adapters.database import db_adapter as da


AUDIT_LOGGER = logging.getLogger("anzma.audit")
AUDIT_LOGGER.setLevel(logging.INFO)
AUDIT_LOGGER.propagate = False
if not any(getattr(handler, "_anzma_audit_handler", False) for handler in AUDIT_LOGGER.handlers):
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler._anzma_audit_handler = True
    AUDIT_LOGGER.addHandler(handler)


def audit(event: str, outcome: str, **details: object) -> None:
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "event": event,
        "outcome": outcome,
        "actor": os.getenv("ANZMA_AUDIT_ACTOR") or getpass.getuser(),
        "host": socket.gethostname(),
        **details,
    }
    AUDIT_LOGGER.info(json.dumps(record, separators=(",", ":"), ensure_ascii=False))


def connect():
    return da.connect(
        host=os.getenv("PGHOST", "localhost"), port=os.getenv("PGPORT", "5432"),
        dbname=os.getenv("PGDATABASE", "anzma"), user=os.getenv("PGUSER", "postgres"),
        password=os.getenv("PGPASSWORD", ""),
    )


def parse_time(value: str) -> time:
    try:
        return time.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("time must use HH:MM or HH:MM:SS") from exc


def parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timestamp must be ISO 8601") from exc
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("timestamp must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def add_expectation_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--scope", required=True,
                        choices=("ASSET_METRIC", "ASSET_TYPE_METRIC", "SOURCE", "GLOBAL"))
    parser.add_argument("--record-kind", required=True, choices=("telemetry", "event", "source"))
    parser.add_argument("--source-code")
    parser.add_argument("--asset-code")
    parser.add_argument("--asset-type")
    parser.add_argument("--metric-code")
    parser.add_argument("--event-type")
    parser.add_argument("--expected-interval-seconds", type=int, required=True)
    parser.add_argument("--stale-multiplier", type=float, default=2)
    parser.add_argument("--missing-multiplier", type=float, default=5)
    parser.add_argument("--priority", type=int, default=100)
    parser.add_argument("--valid-from", type=parse_timestamp, default=None)
    parser.add_argument("--valid-to", type=parse_timestamp)


def validate_expectation_args(args: argparse.Namespace) -> None:
    expected = {
        "GLOBAL": (False, False, False),
        "SOURCE": (True, False, False),
        "ASSET_METRIC": (False, True, False),
        "ASSET_TYPE_METRIC": (False, False, True),
    }[args.scope]
    if bool(args.source_code) != expected[0] or bool(args.asset_code) != expected[1] or bool(args.asset_type) != expected[2]:
        raise ValueError("scope requires exactly its corresponding source/asset/asset-type key")
    if args.record_kind == "source" and (args.scope.startswith("ASSET_") or args.metric_code or args.event_type):
        raise ValueError("source expectations cannot have asset scope, metric, or event type")
    if args.record_kind == "telemetry" and args.event_type:
        raise ValueError("telemetry expectations cannot specify --event-type")
    if args.record_kind == "event" and args.metric_code:
        raise ValueError("event expectations cannot specify --metric-code")
    if args.scope.startswith("ASSET_") and args.record_kind == "telemetry" and not args.metric_code:
        raise ValueError("asset-scoped telemetry expectations require --metric-code")
    if args.scope.startswith("ASSET_") and args.record_kind == "event" and not args.event_type:
        raise ValueError("asset-scoped event expectations require --event-type")
    if args.expected_interval_seconds <= 0 or args.priority < 0:
        raise ValueError("interval must be positive and priority non-negative")
    if args.stale_multiplier <= 0 or args.missing_multiplier <= args.stale_multiplier:
        raise ValueError("multipliers must satisfy 0 < stale < missing")
    if args.valid_from and args.valid_to and args.valid_from >= args.valid_to:
        raise ValueError("--valid-from must be earlier than --valid-to")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    schedule = commands.add_parser("set-building-schedule", help="set an existing building's local schedule")
    schedule.add_argument("--building-id", type=int, required=True)
    schedule.add_argument("--timezone", required=True)
    schedule.add_argument("--work-start", type=parse_time, required=True)
    schedule.add_argument("--work-end", type=parse_time, required=True)
    schedule.add_argument("--non-working-weekdays", default="",
                          help="comma-separated ISO weekday numbers 1..7")

    expectation = commands.add_parser("set-expectation", help="create or update an expectation at its scope and priority")
    add_expectation_arguments(expectation)

    commands.add_parser("list", help="list building schedules and reporting expectations as JSON")
    args = parser.parse_args()

    if args.command == "set-building-schedule":
        try:
            ZoneInfo(args.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            parser.error(f"--timezone must be a valid installed IANA timezone: {exc}")
        if args.work_start >= args.work_end:
            parser.error("--work-start must be earlier than --work-end")
        try:
            weekdays = sorted({int(item.strip()) for item in args.non_working_weekdays.split(",") if item.strip()})
        except ValueError:
            parser.error("--non-working-weekdays must be comma-separated integers 1..7")
        if any(day not in range(1, 8) for day in weekdays):
            parser.error("--non-working-weekdays values must be ISO weekdays 1..7")
    elif args.command == "set-expectation":
        try:
            validate_expectation_args(args)
        except ValueError as exc:
            parser.error(str(exc))
        if args.valid_from is None:
            args.valid_from = datetime.now(timezone.utc)

    conn = None
    event = {"set-building-schedule": "BUILDING_SCHEDULE_SET",
             "set-expectation": "REPORTING_EXPECTATION_SET"}.get(args.command)
    try:
        conn = connect()
        with conn.cursor() as cur:
            if args.command == "set-building-schedule":
                cur.execute(
                    """UPDATE buildings SET timezone=%s,work_start_local=%s,work_end_local=%s,
                              non_working_weekdays=%s WHERE building_id=%s RETURNING building_id""",
                    (args.timezone, args.work_start, args.work_end, weekdays, args.building_id),
                )
                row = cur.fetchone()
                if row is None:
                    raise ValueError(f"unknown building_id {args.building_id}")
                conn.commit()
                audit(event, "SUCCESS", building_id=args.building_id, timezone=args.timezone)
                print(f"Updated schedule for building {args.building_id}.")
            elif args.command == "set-expectation":
                source_id = asset_id = None
                if args.source_code:
                    cur.execute("SELECT source_id FROM data_sources WHERE source_code=%s", (args.source_code,))
                    row = cur.fetchone()
                    if row is None:
                        raise ValueError(f"unknown source_code {args.source_code}")
                    source_id = row["source_id"]
                if args.asset_code:
                    cur.execute("SELECT asset_id,asset_type FROM assets WHERE asset_code=%s", (args.asset_code,))
                    row = cur.fetchone()
                    if row is None:
                        raise ValueError(f"unknown asset_code {args.asset_code}")
                    asset_id = row["asset_id"]
                if args.asset_type and not args.asset_type.strip():
                    raise ValueError("asset_type cannot be blank")
                cur.execute(
                    """INSERT INTO reporting_expectations
                           (scope,source_id,asset_id,asset_type,record_kind,metric_code,event_type,
                            expected_interval_seconds,stale_multiplier,missing_multiplier,priority,valid_from,valid_to)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT ON CONSTRAINT reporting_expectations_scope_priority_key
                       DO UPDATE SET expected_interval_seconds=EXCLUDED.expected_interval_seconds,
                           stale_multiplier=EXCLUDED.stale_multiplier,missing_multiplier=EXCLUDED.missing_multiplier,
                           valid_from=EXCLUDED.valid_from,valid_to=EXCLUDED.valid_to,
                           expectation_version=reporting_expectations.expectation_version+1,updated_at=now()
                       RETURNING expectation_id,expectation_version""",
                    (args.scope, source_id, asset_id, args.asset_type, args.record_kind, args.metric_code,
                     args.event_type, args.expected_interval_seconds, args.stale_multiplier,
                     args.missing_multiplier, args.priority, args.valid_from, args.valid_to),
                )
                row = cur.fetchone()
                conn.commit()
                audit(event, "SUCCESS", expectation_id=row["expectation_id"], scope=args.scope,
                      record_kind=args.record_kind, priority=args.priority)
                print(f"Expectation {row['expectation_id']} saved (version {row['expectation_version']}).")
            else:
                cur.execute("SELECT building_id,timezone,work_start_local,work_end_local,non_working_weekdays FROM buildings ORDER BY building_id")
                buildings = [dict(row) for row in cur.fetchall()]
                cur.execute("SELECT * FROM reporting_expectations ORDER BY scope,priority,expectation_id")
                expectations = [dict(row) for row in cur.fetchall()]
                print(json.dumps({"buildings": buildings, "expectations": expectations},
                                 default=str, separators=(",", ":")))
    except Exception as exc:
        if conn is not None:
            conn.rollback()
        if event:
            audit(event, "FAILED", command=args.command)
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        if conn is not None:
            conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
