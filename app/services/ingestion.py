"""Vendor-neutral validation and persistence for canonical Core ingestion batches."""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from app.adapters.database import db_adapter as da


CONTRACT_VERSION = "1.0"
MAX_RECORDS = 5000
MAX_OPAQUE_BYTES = 8192
FUTURE_TOLERANCE = timedelta(minutes=5)
SOURCE_KEY = re.compile(r"^azk_([0-9a-f]{12})_([A-Za-z0-9_-]{40,})$")
ASSET_CODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
REASON_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
QUALITY = {"GOOD", "UNCERTAIN", "BAD"}
SCOPES = {"telemetry": "ingest:telemetry", "event": "ingest:event", "state": "ingest:state"}


class IngestionError(Exception):
    def __init__(self, status: int, code: str, message: str, *, retryable: bool = False,
                 batch_id: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable
        self.batch_id = batch_id

    def response(self, batch_id: str | None = None) -> dict[str, Any]:
        value = {"error": {"code": self.code, "message": self.message, "retryable": self.retryable}}
        if batch_id:
            value["batch_id"] = batch_id
        return value


class RecordRejected(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def json_hash(value: dict[str, Any]) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def parse_key_prefix(token: str) -> str | None:
    match = SOURCE_KEY.fullmatch(token or "")
    return match.group(1) if match else None


def authenticate(cur, authorization: str | None) -> dict[str, Any]:
    if not authorization or not authorization.startswith("Bearer "):
        raise IngestionError(401, "AUTH_INVALID", "A valid source API key is required.")
    token = authorization[7:].strip()
    prefix = parse_key_prefix(token)
    if not prefix:
        raise IngestionError(401, "AUTH_INVALID", "A valid source API key is required.")
    cur.execute(
        """SELECT c.credential_id, c.source_id, c.key_hash, c.scopes, c.revoked_at, c.expires_at,
                  s.source_code, s.building_id, s.status
           FROM source_credentials c JOIN data_sources s USING (source_id)
           WHERE c.key_prefix = %s""", (prefix,)
    )
    row = cur.fetchone()
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    if not row or not hmac.compare_digest(row["key_hash"].strip(), digest):
        raise IngestionError(401, "AUTH_INVALID", "A valid source API key is required.")
    now = datetime.now(timezone.utc)
    if row["revoked_at"] is not None or (row["expires_at"] is not None and row["expires_at"] <= now) \
            or row["status"] != "ACTIVE":
        raise IngestionError(403, "SOURCE_DISABLED", "This source credential is disabled.")
    cur.execute("UPDATE source_credentials SET last_used_at = %s WHERE credential_id = %s", (now, row["credential_id"]))
    return dict(row)


def parse_timestamp(value: Any, label: str, received_at: datetime) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z", value):
        raise RecordRejected("INVALID_TIMESTAMP", f"{label} must be RFC 3339 UTC with a trailing Z.")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise RecordRejected("INVALID_TIMESTAMP", f"{label} is not a valid timestamp.") from exc
    if parsed > received_at + FUTURE_TOLERANCE:
        raise RecordRejected("FUTURE_TIMESTAMP", f"{label} is more than five minutes in the future.")
    return parsed.astimezone(timezone.utc)


def _opaque(value: Any, label: str, *, required_type=dict) -> Any:
    if value is None:
        return None
    if not isinstance(value, required_type):
        raise RecordRejected("SCHEMA_INVALID", f"{label} must be an object.")
    if len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > MAX_OPAQUE_BYTES:
        raise RecordRejected("PAYLOAD_TOO_LARGE", f"{label} exceeds 8 KiB.")
    return value


def _resolve_asset(cur, source: dict[str, Any], building_code: str, asset: Any) -> dict[str, Any]:
    if not isinstance(asset, dict) or set(asset) not in ({"asset_code"}, {"external_id"}):
        raise RecordRejected("SCHEMA_INVALID", "asset must contain exactly one of asset_code or external_id.")
    if asset.get("asset_code") is not None:
        code = asset["asset_code"]
        if not isinstance(code, str) or not ASSET_CODE.fullmatch(code):
            raise RecordRejected("SCHEMA_INVALID", "asset_code has an invalid format.")
        cur.execute(
            """SELECT a.asset_id, a.system_type_id FROM assets a JOIN locations l USING (location_id)
               JOIN buildings b USING (building_id)
               WHERE a.asset_code=%s AND b.building_code=%s AND b.building_id=%s""",
            (code, building_code, source["building_id"]),
        )
    else:
        external_id = asset["external_id"]
        if not isinstance(external_id, str) or not 1 <= len(external_id) <= 128:
            raise RecordRejected("SCHEMA_INVALID", "external_id must contain 1 to 128 characters.")
        cur.execute(
            """SELECT a.asset_id, a.system_type_id FROM asset_external_ids x
               JOIN assets a USING (asset_id) JOIN locations l USING (location_id)
               WHERE x.source_id=%s AND x.external_id=%s AND l.building_id=%s""",
            (source["source_id"], external_id, source["building_id"]),
        )
    row = cur.fetchone()
    if not row:
        raise RecordRejected("ASSET_UNRESOLVED", "Asset is not registered or mapped within this source's building.")
    return dict(row)


def _validate_record(cur, source: dict[str, Any], building_code: str, record: Any,
                     received_at: datetime) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    if not isinstance(record, dict):
        raise RecordRejected("SCHEMA_INVALID", "Record must be a JSON object.")
    kind = record.get("record_type")
    if kind not in SCOPES:
        raise RecordRejected("UNKNOWN_RECORD_TYPE", "record_type must be telemetry, event, or state.")
    warnings = [{"code": "UNKNOWN_FIELD", "message": f"unknown field '{key}' ignored"}
                for key in sorted(set(record) - _allowed_fields(kind))]
    if SCOPES[kind] not in (source.get("scopes") or []):
        raise RecordRejected("SCOPE_FORBIDDEN", f"This API key cannot ingest {kind} records.")
    asset = _resolve_asset(cur, source, building_code, record.get("asset"))
    observed_at = parse_timestamp(record.get("observed_at"), "observed_at", received_at)
    metadata = _opaque(record.get("source_metadata"), "source_metadata")

    if kind == "telemetry":
        required = ("metric_code", "value", "unit")
        if any(k not in record for k in required):
            raise RecordRejected("SCHEMA_INVALID", "telemetry requires metric_code, value, and unit.")
        metric_code, value, unit = record["metric_code"], record["value"], record["unit"]
        if not isinstance(metric_code, str) or not isinstance(unit, str):
            raise RecordRejected("SCHEMA_INVALID", "metric_code and unit must be strings.")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise RecordRejected("INVALID_VALUE", "value must be a finite JSON number.")
        quality = record.get("quality", "GOOD")
        if not isinstance(quality, str) or quality not in QUALITY:
            raise RecordRejected("SCHEMA_INVALID", "quality must be GOOD, UNCERTAIN, or BAD.")
        cur.execute(
            """SELECT m.metric_id, m.unit FROM metric_types m
               JOIN metric_type_system_compat c USING (metric_id)
               WHERE m.name=%s AND c.system_type_id=%s""", (metric_code, asset["system_type_id"])
        )
        metric = cur.fetchone()
        if not metric:
            raise RecordRejected("UNKNOWN_METRIC", "Metric is unknown or incompatible with the asset system.")
        if metric["unit"] != unit:
            raise RecordRejected("UNIT_MISMATCH", f"unit '{unit}' does not match canonical unit '{metric['unit']}'.")
        return kind, {"asset_id": asset["asset_id"], "metric_id": metric["metric_id"], "value": float(value),
                      "quality": quality, "observed_at": observed_at, "source_metadata": metadata}, warnings

    if kind == "event":
        event_type = record.get("event_type")
        event_id = record.get("source_event_id")
        if not isinstance(event_id, str) or not 1 <= len(event_id) <= 128:
            raise RecordRejected("SCHEMA_INVALID", "event source_event_id must contain 1 to 128 characters.")
        if not isinstance(event_type, str):
            raise RecordRejected("SCHEMA_INVALID", "event_type must be a string.")
        severity = record.get("severity", "INFO")
        if not isinstance(severity, str) or severity.upper() not in {"INFO", "WARNING", "CRITICAL"}:
            raise RecordRejected("SCHEMA_INVALID", "severity must be INFO, WARNING, or CRITICAL.")
        details = _opaque(record.get("details"), "details") or {}
        cur.execute(
            """SELECT t.event_type_id FROM event_types t
               JOIN event_type_system_compat c USING (event_type_id)
               WHERE t.name=%s AND c.system_type_id=%s""", (event_type.lower(), asset["system_type_id"])
        )
        event = cur.fetchone()
        if not event:
            raise RecordRejected("UNKNOWN_EVENT_TYPE", "Event type is unknown or incompatible with the asset system.")
        return kind, {"asset_id": asset["asset_id"], "event_type_id": event["event_type_id"],
                      "source_event_id": event_id, "severity": severity.lower(), "details": details,
                      "observed_at": observed_at, "source_metadata": metadata}, warnings

    state_value = record.get("reported_state")
    availability = record.get("availability")
    state_values = {"RUNNING", "STOPPED", "STANDBY", "FAULT", "UNKNOWN"}
    availability_values = {"AVAILABLE", "UNAVAILABLE", "UNKNOWN"}
    if state_value is not None and (not isinstance(state_value, str) or state_value not in state_values):
        raise RecordRejected("SCHEMA_INVALID", "reported_state is not a supported state.")
    if availability is not None and (not isinstance(availability, str) or availability not in availability_values):
        raise RecordRejected("SCHEMA_INVALID", "availability is not a supported value.")
    if state_value is None and availability is None:
        raise RecordRejected("SCHEMA_INVALID", "state requires reported_state or availability.")
    reason_code = record.get("reason_code")
    message = record.get("message")
    if reason_code is not None and (not isinstance(reason_code, str) or not REASON_CODE.fullmatch(reason_code)):
        raise RecordRejected("SCHEMA_INVALID", "reason_code must use UPPER_SNAKE format.")
    if message is not None and (not isinstance(message, str) or len(message) > 500):
        raise RecordRejected("SCHEMA_INVALID", "message must be a string of at most 500 characters.")
    return kind, {"asset_id": asset["asset_id"], "reported_state": state_value, "availability": availability,
                  "reason_code": reason_code, "message": message, "observed_at": observed_at,
                  "source_metadata": metadata}, warnings


def _allowed_fields(kind: str) -> set[str]:
    common = {"record_type", "asset", "observed_at", "source_metadata"}
    return common | {
        "telemetry": {"metric_code", "value", "unit", "quality"},
        "event": {"source_event_id", "event_type", "severity", "details"},
        "state": {"reported_state", "availability", "reason_code", "message"},
    }[kind]


def _same_measurement(existing: dict[str, Any], record: dict[str, Any]) -> bool:
    prior = float(existing["value"])
    incoming = record["value"]
    prior_quality = existing["quality"] or "GOOD"  # legacy rows predate the quality column
    return abs(prior - incoming) <= 1e-9 * max(1.0, abs(prior)) and prior_quality == record["quality"]


def _insert_record(cur, source: dict[str, Any], batch_pk: int, contract_version: str,
                   kind: str, value: dict[str, Any], received_at: datetime) -> tuple[str, int | None]:
    if kind == "telemetry":
        cur.execute(
            """INSERT INTO telemetry_records
                   (asset_id, metric_id, ts, value, source_id, received_at, quality,
                    ingestion_batch_id, contract_version, source_metadata)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT DO NOTHING RETURNING telemetry_id""",
            (value["asset_id"], value["metric_id"], value["observed_at"], value["value"], source["source_id"],
             received_at, value["quality"], batch_pk, contract_version,
             da.Json(cur, value["source_metadata"]) if value["source_metadata"] is not None else None),
        )
        row = cur.fetchone()
        if row:
            return "accepted", row["telemetry_id"]
        cur.execute("SELECT value, quality FROM telemetry_records WHERE asset_id=%s AND metric_id=%s AND ts=%s",
                    (value["asset_id"], value["metric_id"], value["observed_at"]))
        existing = cur.fetchone()
        if existing and _same_measurement(existing, value):
            return "duplicate", None
        raise RecordRejected("CONFLICT", "A different value is already stored for this asset, metric, and observed_at; first write was preserved.")

    if kind == "event":
        cur.execute(
            """INSERT INTO event_records
                   (asset_id,event_type_id,ts,severity,details,source_id,received_at,source_event_id,
                    ingestion_batch_id,contract_version,source_metadata)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT DO NOTHING RETURNING event_id""",
            (value["asset_id"], value["event_type_id"], value["observed_at"], value["severity"],
             da.Json(cur, value["details"]), source["source_id"], received_at, value["source_event_id"],
             batch_pk, contract_version, da.Json(cur, value["source_metadata"])
             if value["source_metadata"] is not None else None),
        )
        row = cur.fetchone()
        if row:
            return "accepted", row["event_id"]
        cur.execute(
            """SELECT event_type_id, severity, details, ts FROM event_records
               WHERE source_id=%s AND source_event_id=%s""", (source["source_id"], value["source_event_id"])
        )
        existing = cur.fetchone()
        if existing and existing["event_type_id"] == value["event_type_id"] \
                and existing["severity"] == value["severity"] and (existing["details"] or {}) == value["details"] \
                and existing["ts"] == value["observed_at"]:
            return "duplicate", None
        raise RecordRejected("CONFLICT", "An event already occupies this source identity or legacy event key; it was preserved.")

    cur.execute(
        """INSERT INTO equipment_state_records
               (source_id,asset_id,observed_at,received_at,reported_state,availability,reason_code,message,
                source_metadata,contract_version,ingestion_batch_id)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT DO NOTHING RETURNING state_id""",
        (source["source_id"], value["asset_id"], value["observed_at"], received_at, value["reported_state"],
         value["availability"], value["reason_code"], value["message"],
         da.Json(cur, value["source_metadata"]) if value["source_metadata"] is not None else None,
         contract_version, batch_pk),
    )
    row = cur.fetchone()
    if row:
        return "accepted", row["state_id"]
    cur.execute(
        """SELECT reported_state, availability, reason_code, message FROM equipment_state_records
           WHERE source_id=%s AND asset_id=%s AND observed_at=%s""",
        (source["source_id"], value["asset_id"], value["observed_at"]),
    )
    existing = cur.fetchone()
    if existing and all(existing[k] == value[k] for k in ("reported_state", "availability", "reason_code", "message")):
        return "duplicate", None
    raise RecordRejected("CONFLICT", "A different state update exists for this source, asset, and observed_at; first write was preserved.")


def process_batch(cur, source: dict[str, Any], body: dict[str, Any], received_at: datetime) -> tuple[dict[str, Any], bool]:
    batch_text = str(body["batch_id"])
    # Pass UUID text through the DB adapter: psycopg2 installations do not
    # necessarily register Python's UUID adapter, while PostgreSQL infers UUID.
    batch_uuid = str(UUID(batch_text))
    if body.get("contract_version") != CONTRACT_VERSION:
        raise IngestionError(422, "UNSUPPORTED_CONTRACT_VERSION", f"Only contract version {CONTRACT_VERSION} is supported.", batch_id=batch_text)
    if body["source"]["source_code"] != source["source_code"]:
        raise IngestionError(403, "SOURCE_MISMATCH", "source_code does not match the authenticated source.", batch_id=batch_text)
    if not isinstance(body.get("building_code"), str):
        raise IngestionError(422, "UNKNOWN_BUILDING", "building_code must identify the registered source building.", batch_id=batch_text)
    cur.execute("SELECT building_id FROM buildings WHERE building_code=%s", (body["building_code"],))
    building = cur.fetchone()
    if not building or building["building_id"] != source["building_id"]:
        raise IngestionError(422, "UNKNOWN_BUILDING", "building_code is not registered to this source.", batch_id=batch_text)
    if body.get("delivery_mode", "LIVE") not in {"LIVE", "BACKFILL"}:
        raise IngestionError(422, "SCHEMA_INVALID", "delivery_mode must be LIVE or BACKFILL.", batch_id=batch_text)
    records = body.get("records")
    if not isinstance(records, list) or len(records) > MAX_RECORDS:
        raise IngestionError(413, "BATCH_TOO_LARGE", "records must contain no more than 5000 items.", batch_id=batch_text)
    try:
        sent_at = parse_timestamp(body.get("sent_at"), "sent_at", received_at)
    except RecordRejected as exc:
        raise IngestionError(422, exc.code, exc.message, batch_id=batch_text) from exc
    payload_hash = json_hash(body)
    cur.execute("SELECT * FROM ingestion_batches WHERE source_id=%s AND batch_id=%s", (source["source_id"], batch_uuid))
    previous = cur.fetchone()
    if previous:
        if previous["payload_sha256"].strip() != payload_hash:
            raise IngestionError(409, "BATCH_ID_REUSED", "batch_id was previously used with a different payload.", batch_id=batch_text)
        return dict(previous["response_payload"]), True

    cur.execute("SELECT source_id FROM data_sources WHERE source_id=%s AND status='ACTIVE' FOR UPDATE", (source["source_id"],))
    if not cur.fetchone():
        raise IngestionError(403, "SOURCE_DISABLED", "This source is disabled.", batch_id=batch_text)
    cur.execute(
        """INSERT INTO ingestion_batches
           (source_id,batch_id,contract_version,payload_sha256,received_at,sent_at,connector_name,connector_version,
            received_count,accepted_count,duplicate_count,rejected_count,response_payload)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,0,0,0,0,'{}'::jsonb) RETURNING ingestion_batch_id""",
        (source["source_id"], batch_uuid, CONTRACT_VERSION, payload_hash, received_at, sent_at,
         body["source"]["connector"]["name"], body["source"]["connector"]["version"]),
    )
    batch_pk = cur.fetchone()["ingestion_batch_id"]

    results, rejected, warnings = [], [], []
    accepted_count = duplicate_count = rejected_count = 0
    for index, record in enumerate(records):
        kind = record.get("record_type") if isinstance(record, dict) else None
        try:
            kind, validated, row_warnings = _validate_record(cur, source, body["building_code"], record, received_at)
            warnings.extend({"index": index, **warning} for warning in row_warnings)
            outcome, record_id = _insert_record(cur, source, batch_pk, CONTRACT_VERSION, kind, validated, received_at)
            if outcome == "accepted":
                accepted_count += 1
            else:
                duplicate_count += 1
            result = {"index": index, "record_type": kind, "outcome": outcome.upper()}
            if record_id is not None:
                result["record_id"] = record_id
            results.append(result)
        except RecordRejected as exc:
            rejected_count += 1
            result = {"index": index, "record_type": kind, "outcome": "REJECTED",
                      "reason_code": exc.code, "message": exc.message}
            results.append(result)
            rejected.append({"record_index": index, "record_type": kind, "reason_code": exc.code,
                             "message": exc.message, "payload": record if isinstance(record, dict) else {"value": record}})

    status = "PARTIAL" if rejected_count else "ACCEPTED"
    response = {
        "batch_id": batch_text, "status": status,
        "received_at": received_at.isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "contract_version": CONTRACT_VERSION,
        "counts": {"received": len(records), "accepted": accepted_count,
                   "duplicate": duplicate_count, "rejected": rejected_count},
        "results": results,
        "rejected": [{k: v for k, v in row.items() if k != "payload"} for row in rejected[:100]],
        "rejected_truncated": rejected_count > 100,
        "warnings": warnings,
        "transaction": "COMMITTED",
    }
    for item in rejected:
        cur.execute(
            """INSERT INTO ingestion_rejections
               (ingestion_batch_id,record_index,record_type,reason_code,message,payload)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            (batch_pk, item["record_index"], item["record_type"], item["reason_code"], item["message"],
             da.Json(cur, item["payload"])),
        )
    cur.execute(
        """UPDATE ingestion_batches SET received_count=%s, accepted_count=%s, duplicate_count=%s, rejected_count=%s,
               response_payload=%s WHERE ingestion_batch_id=%s""",
        (len(records), accepted_count, duplicate_count, rejected_count, da.Json(cur, response), batch_pk),
    )
    cur.execute("UPDATE data_sources SET last_received_at=%s, last_success_at=%s WHERE source_id=%s",
                (received_at, received_at, source["source_id"]))
    if records and accepted_count == 0 and duplicate_count == 0:
        response["status"] = "REJECTED"
        cur.execute("UPDATE ingestion_batches SET response_payload=%s WHERE ingestion_batch_id=%s",
                    (da.Json(cur, response), batch_pk))
    return response, False

