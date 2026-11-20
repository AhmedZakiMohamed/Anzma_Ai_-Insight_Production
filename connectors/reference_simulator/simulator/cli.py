from __future__ import annotations

import argparse
import hashlib
import json
import random
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timezone

from .client import CoreClient, TransportFailure
from .config import ConfigError, load_config
from .drain import DrainReport, drain_pending
from .outbox import BufferFullError, Outbox, OutboxCorruptError
from .records import GENERATOR_VERSION, canonical_json, generate_records


NAMESPACE = uuid.UUID("736677d0-f274-4abf-a8db-79c2af4cb40a")


def _summary(**values) -> None:
    print(json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=False))


def _chunks(records: list[dict], target: int) -> list[list[dict]]:
    if not records:
        return [[]]
    return [records[i:i + target] for i in range(0, len(records), target)]


def _body(config, records, delivery, batch_id, anchor, buffered, dropped) -> bytes:
    connector_status = {
        "vendor_link": "UNKNOWN", "buffered_records": buffered,
        "dropped_records": dropped, "last_poll_at": anchor,
    }
    if len(canonical_json(connector_status)) > 8 * 1024:
        raise ValueError("connector_status exceeds the contract 8 KiB limit")
    envelope = {
        "contract_version": "1.0", "batch_id": batch_id, "sent_at": anchor,
        "delivery_mode": delivery,
        "source": {
            "source_code": config.source_code,
            "connector": {"name": config.connector_name, "version": config.connector_version},
            "connector_status": connector_status,
        },
        "building_code": config.building_code, "records": records,
    }
    result = canonical_json(envelope)
    if len(result) > 5 * 1024 * 1024:
        raise ValueError("generated batch exceeds the contract 5 MiB limit")
    return result


def _batch_id(config, scenario, anchor, sequence, kind="records"):
    manifest = (f"v{GENERATOR_VERSION}|{config.source_code}|{config.building_code}|{config.seed}|{scenario}|"
                f"{config.seed_building}|{anchor}|{config.batch_records}|{config.connector_name}|"
                f"{config.connector_version}|{kind}|{sequence}")
    return str(uuid.uuid5(NAMESPACE, manifest))


# Keep the D2 helper names available for downstream tests and callers while
# the command implementation uses the D3 drain engine.
_new_batch_id = _batch_id


def _submit(client, outbox: Outbox, row, duplicate_mode: bool = False) -> tuple[int, dict]:
    """Submit one persisted row using the original D2 result contract."""
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


def _enqueue_keepalive(outbox: Outbox, config, now: datetime):
    stamp = now.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    batch_id = str(uuid.uuid4())
    body = _body(config, [], "LIVE", batch_id, stamp,
                 outbox.pending_count(config.source_code), outbox.dropped_records())
    row = outbox.add(batch_id, config.source_code, "keepalive", body, created_at=stamp)
    if hashlib.sha256(bytes(row["payload"])).hexdigest() != row["payload_sha256"]:
        raise RuntimeError("persisted keepalive hash mismatch")
    return row


def enqueue_keepalive_if_due(outbox: Outbox, config, now: datetime, *, force=False):
    if outbox.keepalive_pending(config.source_code):
        return None
    due, _ = outbox.keepalive_due(now, config.keepalive_seconds)
    if not force and not due:
        return None
    return _enqueue_keepalive(outbox, config, now)


def _summary_for(outbox, report, source_code, scenario, event="SIMULATOR_RUN") -> int:
    _summary(event=event, scenario=scenario, attempts=report.attempts, batches=report.batches,
             pending_batches=len(outbox.pending(source_code)), buffered_records=outbox.pending_count(source_code),
             buffered_bytes=outbox.buffered_bytes(), dropped_records=outbox.dropped_records())
    return report.exit_code


def _drain(outbox, client, config, scenario, *, max_iterations=None, loop=False,
           clock=lambda: datetime.now(timezone.utc), sleeper=time.sleep, rng=random):
    def when_empty():
        now = clock()
        if outbox.keepalive_pending(config.source_code):
            return False
        row = enqueue_keepalive_if_due(outbox, config, now)
        if row is None:
            _, remaining = outbox.keepalive_due(now, config.keepalive_seconds)
            sleeper(remaining)
            row = enqueue_keepalive_if_due(outbox, config, clock())
        if row is None:
            return False
        return True

    return drain_pending(outbox, client, config.source_code, max_iterations=max_iterations,
                         clock=clock, sleeper=sleeper, rng=rng,
                         when_empty=when_empty if loop else None)


def _open_outbox(config):
    return Outbox(config.state_path, config.buffer_max_bytes)


def _config_and_outbox():
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"simulator configuration error: {exc}", file=sys.stderr)
        return None, None, 2
    try:
        outbox = _open_outbox(config)
    except OutboxCorruptError:
        print("outbox corruption detected; sending and generation stopped; preserve the state file for recovery", file=sys.stderr)
        return config, None, 3
    except (OSError, sqlite3.Error, ValueError):
        print("could not open simulator state; no batches were sent", file=sys.stderr)
        return config, None, 1
    return config, outbox, 0


def run(args) -> int:
    config, outbox, error_code = _config_and_outbox()
    if error_code:
        if config and error_code == 3:
            _summary(event="SIMULATOR_RUN", outcome="OUTBOX_CORRUPT", exit_code=3)
        return error_code
    scenario = args.scenario or config.scenario
    if scenario not in {"normal", "fault", "stale-silent", "bad-record", "late-out-of-order", "duplicate-batch"}:
        print("simulator configuration error: unknown scenario", file=sys.stderr)
        outbox.close()
        return 2
    try:
        client = CoreClient(config.core_url, config.api_key, config.timeout_seconds)
        if args.get_batch:
            if not all(ch in "0123456789abcdef-" for ch in args.get_batch) or len(args.get_batch) != 36:
                print("batch_id must be a UUID", file=sys.stderr)
                return 2
            try:
                result = client.get_batch(args.get_batch)
            except TransportFailure:
                _summary(event="BATCH_LOOKUP", batch_id=args.get_batch, outcome="NETWORK_ERROR")
                return 1
            _summary(event="BATCH_LOOKUP", batch_id=args.get_batch,
                     http_status=result.status, response=result.body)
            return 0 if result.status == 200 else 1

        if scenario == "duplicate-batch":
            row = outbox.latest_done(config.source_code)
            if row is None:
                _summary(event="SIMULATOR_RUN", scenario=scenario,
                         outcome="NO_PREVIOUSLY_COMPLETED_BATCH", exit_code=1)
                return 1
            try:
                response = client.post_batch(bytes(row["payload"]))
            except TransportFailure:
                _summary(event="SIMULATOR_RUN", scenario=scenario, batch_id=row["batch_id"],
                         outcome="NETWORK_ERROR", idempotent_replay=False)
                return 1
            replay = response.idempotent_replay
            _summary(event="SIMULATOR_RUN", scenario=scenario, batch_id=row["batch_id"],
                     http_status=response.status, idempotent_replay=replay, response=response.body)
            return 0 if response.status == 200 and replay else 1

        if not outbox.can_generate() and not outbox.pending(config.source_code):
            print("buffer cap reached; generation paused and no queued data was deleted", file=sys.stderr)
            _summary(event="SIMULATOR_RUN", scenario=scenario, outcome="BUFFER_FULL", exit_code=6,
                     buffered_bytes=outbox.buffered_bytes(), dropped_records=outbox.dropped_records())
            return 6
        if outbox.pending(config.source_code):
            report = _drain(outbox, client, config, scenario,
                            max_iterations=args.max_iterations, loop=args.loop)
            if report.credential_fault:
                print("credential/source fault; queued batches are preserved. Correct the source key/scopes and restart.", file=sys.stderr)
            if report.retry_exhausted:
                print("retry limit reached; pending batches are preserved for a later drain", file=sys.stderr)
            return _summary_for(outbox, report, config.source_code, scenario)

        if args.keepalive:
            enqueue_keepalive_if_due(outbox, config, datetime.now(timezone.utc), force=True)
        else:
            records, delivery = generate_records(config.seed, scenario, config.clock_anchor, config.seed_building)
            chunks = _chunks(records, config.batch_records)[:args.batches]
            for index, chunk in enumerate(chunks):
                batch_id = _batch_id(config, scenario, config.clock_anchor, index)
                existing = outbox.get(batch_id)
                if existing:
                    continue
                payload = _body(config, chunk, delivery, batch_id, config.clock_anchor,
                                outbox.pending_count(config.source_code), outbox.dropped_records())
                try:
                    outbox.add(batch_id, config.source_code, scenario, payload,
                               created_at=config.clock_anchor)
                except BufferFullError:
                    outbox.record_dropped(sum(len(rest) for rest in chunks[index + 1:]))
                    raise
        report = _drain(outbox, client, config, scenario,
                        max_iterations=args.max_iterations, loop=args.loop)
        if report.credential_fault:
            print("credential/source fault; queued batches are preserved. Correct the source key/scopes and restart.", file=sys.stderr)
        if report.retry_exhausted:
            print("retry limit reached; pending batches are preserved for a later drain", file=sys.stderr)
        return _summary_for(outbox, report, config.source_code, scenario)
    except BufferFullError:
        print("buffer cap reached; generation paused; pending batches were preserved", file=sys.stderr)
        _summary(event="SIMULATOR_RUN", scenario=scenario, outcome="BUFFER_FULL", exit_code=6,
                 buffered_bytes=outbox.buffered_bytes(), dropped_records=outbox.dropped_records())
        return 6
    except OutboxCorruptError:
        print("outbox corruption detected; sending and generation stopped; preserve the state file for recovery", file=sys.stderr)
        _summary(event="SIMULATOR_RUN", scenario=scenario, outcome="OUTBOX_CORRUPT", exit_code=3)
        return 3
    except (OSError, sqlite3.Error, ValueError) as exc:
        _summary(event="SIMULATOR_RUN", scenario=scenario,
                 outcome="LOCAL_STATE_OR_PAYLOAD_ERROR", error_type=type(exc).__name__, exit_code=1)
        return 1
    finally:
        outbox.close()


def drain(args) -> int:
    config, outbox, error_code = _config_and_outbox()
    if error_code:
        if config and error_code == 3:
            _summary(event="SIMULATOR_DRAIN", outcome="OUTBOX_CORRUPT", exit_code=3)
        return error_code
    try:
        client = CoreClient(config.core_url, config.api_key, config.timeout_seconds)
        report = _drain(outbox, client, config, "drain", max_iterations=args.max_iterations,
                        loop=args.loop)
        if report.credential_fault:
            print("credential/source fault; queued batches are preserved. Correct the source key/scopes and restart.", file=sys.stderr)
        if report.retry_exhausted:
            print("retry limit reached; pending batches are preserved for a later drain", file=sys.stderr)
        return _summary_for(outbox, report, config.source_code, "drain", "SIMULATOR_DRAIN")
    except OutboxCorruptError:
        print("outbox corruption detected; sending stopped; preserve the state file for recovery", file=sys.stderr)
        _summary(event="SIMULATOR_DRAIN", outcome="OUTBOX_CORRUPT", exit_code=3)
        return 3
    finally:
        outbox.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="HTTP-only Anzma reference simulator")
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run", help="generate and send deterministic batches")
    run_parser.add_argument("--scenario", choices=sorted({"normal", "fault", "stale-silent", "bad-record", "late-out-of-order", "duplicate-batch"}))
    run_parser.add_argument("--batches", type=int, default=1)
    run_parser.add_argument("--keepalive", action="store_true", help="send an empty contract keepalive batch")
    run_parser.add_argument("--loop", action="store_true", help="continue with keepalives at the configured interval")
    run_parser.add_argument("--max-iterations", type=int)
    run_parser.add_argument("--get-batch", metavar="UUID", help="retrieve a batch's stored outcome")
    drain_parser = sub.add_parser("drain", help="deliver pending batches oldest first")
    drain_parser.add_argument("--max-iterations", type=int)
    drain_parser.add_argument("--loop", action="store_true", help="send keepalives at the configured interval after draining")
    args = parser.parse_args(argv)
    if args.max_iterations is not None and args.max_iterations < 1:
        parser.error("--max-iterations must be >= 1")
    if args.command == "run":
        if args.batches < 1 or args.batches > 1000:
            parser.error("--batches must be between 1 and 1000")
        return run(args)
    return drain(args)


if __name__ == "__main__":
    raise SystemExit(main())
