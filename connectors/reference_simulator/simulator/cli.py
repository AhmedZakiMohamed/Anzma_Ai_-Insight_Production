from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import uuid

from .client import CoreClient, TransportFailure
from .config import ConfigError, load_config
from .outbox import Outbox
from .records import GENERATOR_VERSION, canonical_json, generate_records


NAMESPACE = uuid.UUID("736677d0-f274-4abf-a8db-79c2af4cb40a")


def _summary(**values) -> None:
    print(json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=False))


def _chunks(records: list[dict], target: int) -> list[list[dict]]:
    if not records:
        return [[]]
    return [records[i:i + target] for i in range(0, len(records), target)]


def _body(config, records, delivery, batch_id, anchor, buffered) -> bytes:
    source = {
        "source_code": config.source_code,
        "connector": {"name": config.connector_name, "version": config.connector_version},
        "connector_status": {
            "vendor_link": "UNKNOWN", "buffered_records": buffered,
            "dropped_records": 0, "last_poll_at": anchor,
        },
    }
    envelope = {
        "contract_version": "1.0", "batch_id": batch_id, "sent_at": anchor,
        "delivery_mode": delivery, "source": source,
        "building_code": config.building_code, "records": records,
    }
    result = canonical_json(envelope)
    if len(result) > 5 * 1024 * 1024:
        raise ValueError("generated batch exceeds the contract 5 MiB limit")
    return result


def _new_batch_id(config, scenario, anchor, sequence, kind="records"):
    manifest = (f"v{GENERATOR_VERSION}|{config.source_code}|{config.building_code}|{config.seed}|{scenario}|"
                f"{config.seed_building}|{anchor}|{config.batch_records}|{config.connector_name}|"
                f"{config.connector_version}|{kind}|{sequence}")
    return str(uuid.uuid5(NAMESPACE, manifest))


def _submit(client, outbox: Outbox, row, duplicate_mode: bool = False) -> tuple[int, dict]:
    try:
        result = client.post_batch(bytes(row["payload"]))
    except TransportFailure:
        return 1, {"batch_id": row["batch_id"], "outcome": "NETWORK_ERROR", "state": "pending"}

    payload = json.dumps(result.body, sort_keys=True, separators=(",", ":"))
    response_code = result.body.get("error", {}).get("code")
    if result.status == 200:
        counts = result.body.get("counts", {})
        if (result.body.get("status") not in {"ACCEPTED", "PARTIAL"}
                or result.body.get("transaction") != "COMMITTED"
                or not all(isinstance(counts.get(key), int)
                           for key in ("received", "accepted", "duplicate", "rejected"))):
            return 1, {"batch_id": row["batch_id"], "http_status": 200,
                       "outcome": "INCOMPLETE_RESPONSE", "state": "pending"}
        outbox.mark(row["batch_id"], "done", result.status, payload)
        if duplicate_mode and not result.idempotent_replay:
            return 1, {"batch_id": row["batch_id"], "http_status": 200, "state": "done",
                       "idempotency_header_missing": True}
        return 0, {"batch_id": row["batch_id"], "http_status": 200, "state": "done",
                   "accepted": counts.get("accepted", 0), "duplicate": counts.get("duplicate", 0),
                   "rejected": counts.get("rejected", 0), "idempotent_replay": result.idempotent_replay}
    if result.status == 422:
        outbox.mark(row["batch_id"], "failed", result.status, payload)
        outcome = "SCHEMA_INVALID" if response_code == "SCHEMA_INVALID" else "REJECTED"
        return 1, {"batch_id": row["batch_id"], "http_status": 422, "code": outcome, "state": "failed"}
    if result.status in {401, 403}:
        return 1, {"batch_id": row["batch_id"], "http_status": result.status,
                   "code": response_code or "AUTH_OR_SCOPE_ERROR", "state": "pending", "outcome": "STOP"}
    if result.status == 429 or result.status >= 500:
        return 1, {"batch_id": row["batch_id"], "http_status": result.status,
                   "code": response_code or "TRANSIENT_CORE_ERROR", "state": "pending",
                   "retry_after": result.retry_after}
    outbox.mark(row["batch_id"], "failed", result.status, payload)
    return 1, {"batch_id": row["batch_id"], "http_status": result.status,
               "code": response_code or "PERMANENT_CORE_ERROR", "state": "failed"}


def run(args) -> int:
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"simulator configuration error: {exc}", file=sys.stderr)
        return 2
    scenario = args.scenario or config.scenario
    if scenario not in {"normal", "fault", "stale-silent", "bad-record", "late-out-of-order", "duplicate-batch"}:
        print("simulator configuration error: unknown scenario", file=sys.stderr)
        return 2

    try:
        outbox = Outbox(config.state_path)
    except (OSError, sqlite3.Error, ValueError) as exc:
        _summary(event="SIMULATOR_RUN", scenario=scenario, outcome="LOCAL_STATE_ERROR",
                 error_type=type(exc).__name__)
        return 1
    summaries = []

    def finish(status: int, event: str = "SIMULATOR_RUN") -> int:
        _summary(event=event, scenario=scenario, batches=summaries,
                 pending_batches=outbox.pending_count(config.source_code),
                 keepalive_interval_seconds=config.interval_seconds)
        return status

    try:
        client = CoreClient(config.core_url, config.api_key, config.timeout_seconds)
        if args.get_batch:
            if not all(ch in "0123456789abcdef-" for ch in args.get_batch) or len(args.get_batch) != 36:
                print("batch_id must be a UUID", file=sys.stderr)
                return 2
            try:
                result = client.get_batch(args.get_batch)
            except TransportFailure:
                summaries.append({"outcome": "NETWORK_ERROR", "state": "unchanged"})
                return finish(1, "BATCH_LOOKUP")
            _summary(event="BATCH_LOOKUP", batch_id=args.get_batch, http_status=result.status, response=result.body)
            return 0 if result.status == 200 else 1

        if scenario == "duplicate-batch":
            row = outbox.latest_done(config.source_code)
            if row is None:
                summaries.append({"outcome": "NO_PREVIOUSLY_COMPLETED_BATCH"})
                return finish(1)
            status, outcome = _submit(client, outbox, row, duplicate_mode=True)
            summaries.append(outcome)
            return finish(status)

        # A restart first resumes its oldest ambiguous batch, using stored bytes only.
        pending = outbox.pending(config.source_code)
        if pending:
            status, outcome = _submit(client, outbox, pending[0])
            summaries.append(outcome)
            return finish(status)

        records, delivery = generate_records(config.seed, scenario, config.clock_anchor, config.seed_building)
        if args.keepalive:
            records, delivery = [], "LIVE"
        chunks = _chunks(records, config.batch_records)
        if args.batches < 1 or args.batches > 1000:
            print("--batches must be between 1 and 1000", file=sys.stderr)
            return finish(2)
        chunks = chunks[:args.batches]
        for index, chunk in enumerate(chunks):
            batch_id = _new_batch_id(config, scenario, config.clock_anchor, index,
                                     "keepalive" if args.keepalive else "records")
            existing = outbox.get(batch_id)
            if existing:
                if existing["status"] == "done":
                    summaries.append({"batch_id": batch_id, "outcome": "NOT_RESENT", "state": "done"})
                    continue
                if existing["status"] == "failed":
                    summaries.append({"batch_id": batch_id, "outcome": "TERMINAL_FAILURE_ALREADY_RECORDED", "state": "failed"})
                    return finish(1)
                row = existing
            else:
                body = _body(config, chunk, delivery, batch_id, config.clock_anchor,
                             outbox.pending_count(config.source_code))
                # SQLite FULL synchronous commit happens before the first HTTP request.
                row = outbox.add(batch_id, config.source_code, scenario, body)
                assert hashlib.sha256(bytes(row["payload"])).hexdigest() == row["payload_sha256"]
            status, outcome = _submit(client, outbox, row)
            summaries.append(outcome)
            if status:
                return finish(status)
        return finish(0)
    except (OSError, sqlite3.Error, ValueError) as exc:
        # Exceptions are converted to fixed messages; never print exception contents.
        summaries.append({"outcome": "LOCAL_STATE_OR_PAYLOAD_ERROR", "error_type": type(exc).__name__})
        return finish(1)
    finally:
        outbox.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="HTTP-only Anzma reference simulator")
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run", help="generate and send one or more deterministic batches")
    run_parser.add_argument("--scenario", choices=sorted({"normal", "fault", "stale-silent", "bad-record", "late-out-of-order", "duplicate-batch"}))
    run_parser.add_argument("--batches", type=int, default=1)
    run_parser.add_argument("--keepalive", action="store_true", help="send an empty contract keepalive batch")
    run_parser.add_argument("--get-batch", metavar="UUID", help="retrieve a batch's stored outcome")
    args = parser.parse_args(argv)
    if args.command == "run":
        return run(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
