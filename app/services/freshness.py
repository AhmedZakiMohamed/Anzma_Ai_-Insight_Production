"""Freshness state evaluation and paged persistence for telemetry/event series.

All wall-clock values are supplied by the caller. The database layer uses
``v_telemetry_valid`` so BAD telemetry is excluded while legacy NULL quality
continues to count as operational data, matching the ingestion contract view.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import logging
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from app.adapters.database import db_adapter as da
from app.config.freshness_defaults import BUILDING_TIMEZONE, NON_WORKING_WEEKDAYS, WORK_END_LOCAL, WORK_START_LOCAL
from app.config.source_liveness import SourceLivenessConfig


SCOPE_ORDER = {"ASSET_METRIC": 0, "ASSET_TYPE_METRIC": 1, "SOURCE": 2, "GLOBAL": 3}
PAGE_SIZE = 250
MAX_ERROR_CHARS = 500
STATE_NAMES = ("FRESH", "STALE", "MISSING", "UNKNOWN", "NOT_APPLICABLE")
_MISSING_SCHEDULE_WARNED = False
_LOGGER = logging.getLogger("anzma.freshness")


def _as_utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _decimal(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _expectation_matches(exp: Mapping[str, Any], series: Mapping[str, Any]) -> bool:
    if exp["record_kind"] != series["record_kind"]:
        return False
    if exp.get("metric_code") is not None and exp["metric_code"] != series.get("metric_code"):
        return False
    if exp.get("event_type") is not None and exp["event_type"] != series.get("event_type"):
        return False
    scope = exp["scope"]
    if scope == "ASSET_METRIC":
        return exp.get("asset_id") == series.get("asset_id")
    if scope == "ASSET_TYPE_METRIC":
        return exp.get("asset_type") == series.get("asset_type")
    if scope == "SOURCE":
        return exp.get("source_id") is not None and exp["source_id"] == series.get("source_id")
    return scope == "GLOBAL"


def resolve_expectation(
    expectations: Iterable[Mapping[str, Any]],
    series: Mapping[str, Any],
    now: datetime,
) -> Mapping[str, Any] | None:
    """Resolve an active override in contract §9 order, then priority.

    Within a scope, larger numeric ``priority`` wins; ties use the most recent
    ``valid_from`` and then the larger expectation ID for deterministic output.
    Validity is half-open: ``valid_from <= now < valid_to``.
    """
    instant = _as_utc(now, "now")
    active = []
    for exp in expectations:
        if exp.get("valid_from") is None or _as_utc(exp["valid_from"], "valid_from") > instant:
            continue
        valid_to = exp.get("valid_to")
        if valid_to is not None and _as_utc(valid_to, "valid_to") <= instant:
            continue
        if exp.get("scope") not in SCOPE_ORDER or not _expectation_matches(exp, series):
            continue
        active.append(exp)
    if not active:
        return None
    return min(
        active,
        key=lambda exp: (
            SCOPE_ORDER[exp["scope"]],
            -int(exp.get("priority", 0)),
            -_as_utc(exp["valid_from"], "valid_from").timestamp(),
            -int(exp.get("expectation_id", 0)),
        ),
    )


def evaluate_state(
    last_observed_at: datetime | None,
    now: datetime,
    expectation: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return the exact contract state and non-negative age for one series."""
    instant = _as_utc(now, "now")
    if expectation is None:
        return {"freshness": "NOT_APPLICABLE", "age_seconds": None}
    if last_observed_at is None:
        return {"freshness": "UNKNOWN", "age_seconds": None}

    observed = _as_utc(last_observed_at, "last_observed_at")
    age = max(Decimal(0), Decimal(str((instant - observed).total_seconds())))
    interval = Decimal(int(expectation["expected_interval_seconds"]))
    stale_at = interval * _decimal(expectation["stale_multiplier"])
    missing_at = interval * _decimal(expectation["missing_multiplier"])
    if age <= stale_at:
        state = "FRESH"
    elif age <= missing_at:
        state = "STALE"
    else:
        state = "MISSING"
    return {"freshness": state, "age_seconds": int(age)}


_CANDIDATE_SQL = """
SELECT c.source_id,c.asset_id,c.record_kind,c.metric_code,c.event_type,c.asset_type,
       a.lifecycle_status,l.building_id,b.timezone,b.work_start_local,b.work_end_local,
       b.non_working_weekdays,ds.status AS source_status
  FROM (
    SELECT DISTINCT t.source_id,t.asset_id,'telemetry'::text AS record_kind,
           m.name AS metric_code,NULL::text AS event_type,a.asset_type
      FROM v_telemetry_valid t
      JOIN metric_types m USING (metric_id)
      JOIN assets a USING (asset_id)
    UNION
    SELECT DISTINCT e.source_id,e.asset_id,'event'::text, NULL::text,et.name,a.asset_type
      FROM event_records e
      JOIN event_types et USING (event_type_id)
      JOIN assets a USING (asset_id)
    UNION
    SELECT NULL::bigint,r.asset_id,r.record_kind,r.metric_code,r.event_type,a.asset_type
      FROM reporting_expectations r
      JOIN assets a USING (asset_id)
     WHERE r.scope='ASSET_METRIC' AND r.valid_from <= %s
       AND (r.valid_to IS NULL OR %s < r.valid_to)
       AND NOT EXISTS (
           SELECT 1 FROM v_telemetry_valid t
           JOIN metric_types m USING (metric_id)
           WHERE r.record_kind='telemetry' AND t.asset_id=r.asset_id AND m.name=r.metric_code
       )
       AND NOT EXISTS (
           SELECT 1 FROM event_records e JOIN event_types et USING (event_type_id)
           WHERE r.record_kind='event' AND e.asset_id=r.asset_id AND et.name=r.event_type
       )
    UNION
    SELECT NULL::bigint,a.asset_id,r.record_kind,r.metric_code,r.event_type,a.asset_type
      FROM reporting_expectations r
      JOIN assets a ON a.asset_type=r.asset_type
     WHERE r.scope='ASSET_TYPE_METRIC' AND r.valid_from <= %s
       AND (r.valid_to IS NULL OR %s < r.valid_to)
       AND NOT EXISTS (
           SELECT 1 FROM v_telemetry_valid t JOIN metric_types m USING (metric_id)
           WHERE r.record_kind='telemetry' AND t.asset_id=a.asset_id AND m.name=r.metric_code
       )
       AND NOT EXISTS (
           SELECT 1 FROM event_records e JOIN event_types et USING (event_type_id)
           WHERE r.record_kind='event' AND e.asset_id=a.asset_id AND et.name=r.event_type
       )
) AS c
  JOIN assets a USING (asset_id)
  JOIN locations l USING (location_id)
  JOIN buildings b USING (building_id)
  LEFT JOIN data_sources ds ON ds.source_id=c.source_id
"""


def _prepare_candidates(cur: Any, now: datetime) -> None:
    cur.execute("DROP TABLE IF EXISTS pg_temp.freshness_candidates")
    cur.execute(
        "CREATE TEMP TABLE freshness_candidates ON COMMIT PRESERVE ROWS AS " + _CANDIDATE_SQL,
        (now, now, now, now),
    )
    cur.execute(
        """CREATE UNIQUE INDEX freshness_candidates_seek_idx ON pg_temp.freshness_candidates
               (asset_id,record_kind,(COALESCE(source_id,0)),(COALESCE(metric_code,'')),(COALESCE(event_type,'')))"""
    )


def _candidate_page(cur: Any, after: tuple[Any, ...] | None, limit: int) -> list[dict[str, Any]]:
    if after is None:
        query = """SELECT *
                     FROM pg_temp.freshness_candidates
                    ORDER BY asset_id,record_kind,COALESCE(source_id,0),COALESCE(metric_code,''),COALESCE(event_type,'')
                    LIMIT %s"""
        params = (limit,)
    else:
        query = """SELECT *
                     FROM pg_temp.freshness_candidates
          WHERE ROW(asset_id,record_kind,COALESCE(source_id,0),COALESCE(metric_code,''),COALESCE(event_type,''))
             > ROW(%s::bigint,%s::text,%s::bigint,%s::text,%s::text)
          ORDER BY asset_id,record_kind,COALESCE(source_id,0),COALESCE(metric_code,''),COALESCE(event_type,'')
          LIMIT %s
        """
        params = (*after, limit)
    cur.execute(query, params)
    return [dict(row) for row in cur.fetchall()]


def _load_expectations(cur: Any, now: datetime) -> list[dict[str, Any]]:
    cur.execute(
        """SELECT expectation_id,scope,source_id,asset_id,asset_type,record_kind,metric_code,event_type,
                  expected_interval_seconds,stale_multiplier,missing_multiplier,priority,valid_from,valid_to,
                  expectation_version
             FROM reporting_expectations
            WHERE valid_from <= %s AND (valid_to IS NULL OR %s < valid_to)""",
        (now, now),
    )
    return [dict(row) for row in cur.fetchall()]


def _attach_observations(cur: Any, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not candidates:
        return []
    values = ",".join(["(%s::bigint,%s::bigint,%s::text,%s::text,%s::text)"] * len(candidates))
    params: list[Any] = []
    for row in candidates:
        params.extend((row["source_id"], row["asset_id"], row["record_kind"], row["metric_code"], row["event_type"]))
    cur.execute(
        f"""WITH page(source_id,asset_id,record_kind,metric_code,event_type) AS (VALUES {values})
            SELECT p.source_id,p.asset_id,p.record_kind,p.metric_code,p.event_type,
                   COALESCE(t.last_observed_at,e.last_observed_at) AS last_observed_at,
                   COALESCE(t.last_received_at,e.last_received_at) AS last_received_at
              FROM page p
              LEFT JOIN LATERAL (
                  SELECT max(v.ts) AS last_observed_at,max(v.received_at) AS last_received_at
                    FROM v_telemetry_valid v JOIN metric_types m USING (metric_id)
                   WHERE p.record_kind='telemetry' AND v.source_id IS NOT DISTINCT FROM p.source_id
                     AND v.asset_id=p.asset_id AND m.name=p.metric_code
              ) t ON true
              LEFT JOIN LATERAL (
                  SELECT max(v.ts) AS last_observed_at,max(v.received_at) AS last_received_at
                    FROM event_records v JOIN event_types et USING (event_type_id)
                   WHERE p.record_kind='event' AND v.source_id IS NOT DISTINCT FROM p.source_id
                     AND v.asset_id=p.asset_id AND et.name=p.event_type
              ) e ON true""",
        params,
    )
    observations = {(r["source_id"], r["asset_id"], r["record_kind"], r["metric_code"], r["event_type"]): dict(r)
                    for r in cur.fetchall()}
    return [
        {
            **candidate,
            **observations[(candidate["source_id"], candidate["asset_id"], candidate["record_kind"],
                            candidate["metric_code"], candidate["event_type"])],
        }
        for candidate in candidates
    ]


def _upsert_current(cur: Any, series: Mapping[str, Any], expectation: Mapping[str, Any],
                    result: Mapping[str, Any], now: datetime) -> None:
    cur.execute(
        """INSERT INTO series_freshness_current
               (source_id,asset_id,record_kind,metric_code,event_type,freshness,last_observed_at,last_received_at,
                age_seconds,expected_interval_seconds,evaluated_at,expectation_id,expectation_version)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT ON CONSTRAINT series_freshness_current_series_key DO UPDATE SET
               freshness=EXCLUDED.freshness,last_observed_at=EXCLUDED.last_observed_at,
               last_received_at=EXCLUDED.last_received_at,age_seconds=EXCLUDED.age_seconds,
               expected_interval_seconds=EXCLUDED.expected_interval_seconds,evaluated_at=EXCLUDED.evaluated_at,
               expectation_id=EXCLUDED.expectation_id,expectation_version=EXCLUDED.expectation_version
           WHERE ROW(series_freshness_current.freshness,series_freshness_current.last_observed_at,
                     series_freshness_current.last_received_at,series_freshness_current.age_seconds,
                     series_freshness_current.expected_interval_seconds,series_freshness_current.evaluated_at,
                     series_freshness_current.expectation_id,series_freshness_current.expectation_version)
             IS DISTINCT FROM ROW(EXCLUDED.freshness,EXCLUDED.last_observed_at,EXCLUDED.last_received_at,
                     EXCLUDED.age_seconds,EXCLUDED.expected_interval_seconds,EXCLUDED.evaluated_at,
                     EXCLUDED.expectation_id,EXCLUDED.expectation_version)""",
        (series["source_id"], series["asset_id"], series["record_kind"], series["metric_code"],
         series["event_type"], result["freshness"], series["last_observed_at"], series["last_received_at"],
         result["age_seconds"], expectation["expected_interval_seconds"], now,
         expectation["expectation_id"], expectation["expectation_version"]),
    )


def _gap_values(series: Mapping[str, Any]) -> tuple[Any, ...]:
    return (series["source_id"], series["asset_id"], series["record_kind"], series["metric_code"], series["event_type"])


def opened_in_work_hours(series: Mapping[str, Any], detected_at: datetime) -> bool:
    """Report whether detection fell in the configured local work schedule; never gates evaluation."""
    global _MISSING_SCHEDULE_WARNED
    timezone_name = series.get("timezone")
    work_start = series.get("work_start_local")
    work_end = series.get("work_end_local")
    non_working = series.get("non_working_weekdays")
    if not timezone_name or work_start is None or work_end is None or non_working is None:
        if not _MISSING_SCHEDULE_WARNED:
            _LOGGER.warning("Building schedule is incomplete; using E1 parity defaults for opened_in_work_hours.")
            _MISSING_SCHEDULE_WARNED = True
        timezone_name = timezone_name or BUILDING_TIMEZONE
        work_start = work_start if work_start is not None else WORK_START_LOCAL
        work_end = work_end if work_end is not None else WORK_END_LOCAL
        non_working = non_working if non_working is not None else NON_WORKING_WEEKDAYS
    local_time = _as_utc(detected_at, "detected_at").astimezone(ZoneInfo(timezone_name))
    clock_time = local_time.timetz().replace(tzinfo=None)
    return local_time.isoweekday() not in non_working and work_start <= clock_time < work_end


def _update_gaps(cur: Any, series: Mapping[str, Any], expectation: Mapping[str, Any],
                 state: str, now: datetime) -> tuple[int, int]:
    source_id, asset_id, record_kind, metric_code, event_type = _gap_values(series)
    observed = series["last_observed_at"]
    opened = 0
    closed = 0
    cur.execute(
        """SELECT gap_id,gap_start,detected_at FROM data_gaps
             WHERE source_id IS NOT DISTINCT FROM %s AND asset_id=%s AND record_kind=%s
               AND metric_code IS NOT DISTINCT FROM %s AND event_type IS NOT DISTINCT FROM %s
               AND gap_end IS NULL FOR UPDATE""",
        (source_id, asset_id, record_kind, metric_code, event_type),
    )
    open_gap = cur.fetchone()
    # A backfill timestamp that falls after gap_start but before detection is
    # still inside the already-detected silent interval; it cannot close it.
    if (open_gap is not None and observed is not None
            and observed > max(open_gap["gap_start"], open_gap["detected_at"])):
        cur.execute(
            """UPDATE data_gaps SET gap_end=%s,closed_by='OBSERVATION',closed_at=%s,last_evaluated_at=%s
                 WHERE gap_id=%s AND gap_end IS NULL""",
            (observed, now, now, open_gap["gap_id"]),
        )
        closed = cur.rowcount
        open_gap = None

    if open_gap is not None:
        cur.execute("UPDATE data_gaps SET last_evaluated_at=%s WHERE gap_id=%s AND last_evaluated_at IS DISTINCT FROM %s",
                    (now, open_gap["gap_id"], now))
        return opened, closed

    eligible = series.get("lifecycle_status") == "ACTIVE" and series.get("source_status") != "DISABLED"
    if state == "MISSING" and observed is not None and eligible:
        cur.execute(
            """SELECT 1 FROM data_gaps
                 WHERE source_id IS NOT DISTINCT FROM %s AND asset_id=%s AND record_kind=%s
                   AND metric_code IS NOT DISTINCT FROM %s AND event_type IS NOT DISTINCT FROM %s
                   AND gap_end IS NOT NULL AND gap_start < %s AND %s < gap_end
                 LIMIT 1""",
            (source_id, asset_id, record_kind, metric_code, event_type, observed, observed),
        )
        if cur.fetchone() is not None:
            return opened, closed
        cur.execute(
            """INSERT INTO data_gaps
                   (source_id,building_id,asset_id,record_kind,metric_code,event_type,gap_start,
                    opened_at,last_evaluated_at,expected_interval_seconds,detected_at,
                    opened_in_work_hours,cause,cause_updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'UNKNOWN',%s)
               ON CONFLICT DO NOTHING RETURNING gap_id""",
            (source_id, series["building_id"], asset_id, record_kind, metric_code, event_type,
             observed, now, now, expectation["expected_interval_seconds"], now,
             opened_in_work_hours(series, now), now),
        )
        opened = 1 if cur.fetchone() else 0
    return opened, closed


def _evaluate_source_liveness(cur: Any, now: datetime, config: SourceLivenessConfig) -> dict[str, Any]:
    """Persist one source snapshot, then classify open gaps from that snapshot.

    Series pages are committed before this function; all are evaluated with the
    same ``now``. This transaction serializes source snapshots and cause changes.
    """
    cur.execute("SELECT pg_advisory_xact_lock(718230642)")
    cur.execute(
        """SELECT ds.source_id,ds.status,ds.last_received_at,ds.last_success_at,
                  ds.last_error_at,ds.last_error,ds.keepalive_interval_seconds,
                  COALESCE(ds.keepalive_interval_seconds,global_exp.expected_interval_seconds) AS expected_interval_seconds,
                  batch.connector_status,
                  stats.active_series_count,stats.stale_series_count,stats.missing_series_count
             FROM data_sources ds
             LEFT JOIN LATERAL (
                 SELECT expected_interval_seconds FROM reporting_expectations
                  WHERE scope='GLOBAL' AND record_kind='source' AND priority=0
                    AND valid_from <= %s AND (valid_to IS NULL OR %s < valid_to)
                  ORDER BY valid_from DESC,expectation_id DESC LIMIT 1
             ) global_exp ON TRUE
             LEFT JOIN LATERAL (
                 SELECT count(*)::integer AS active_series_count,
                        count(*) FILTER (WHERE f.freshness='STALE')::integer AS stale_series_count,
                        count(*) FILTER (WHERE f.freshness='MISSING')::integer AS missing_series_count
                   FROM series_freshness_current f JOIN assets a USING(asset_id)
                  WHERE f.source_id=ds.source_id AND f.evaluated_at=%s AND a.lifecycle_status='ACTIVE'
             ) stats ON TRUE
             LEFT JOIN LATERAL (
                 SELECT b.connector_status FROM ingestion_batches b
                  WHERE b.source_id=ds.source_id
                  ORDER BY b.received_at DESC,b.ingestion_batch_id DESC LIMIT 1
             ) batch ON TRUE
            ORDER BY ds.source_id""",
        (now, now, now),
    )
    source_rows = [dict(row) for row in cur.fetchall()]
    state_counts = {state: 0 for state in ("ALIVE", "SILENT", "UNKNOWN", "DISABLED")}
    for row in source_rows:
        interval = row["expected_interval_seconds"]
        received = row["last_received_at"]
        age_seconds = max(0, int((now - received).total_seconds())) if received is not None else None
        active = row["active_series_count"] or 0
        stale = row["stale_series_count"] or 0
        missing = row["missing_series_count"] or 0
        stale_total = stale + missing
        mass_stale = (active >= config.mass_stale_min_series and
                      stale_total * 100 >= active * config.mass_stale_percent)
        if row["status"] == "DISABLED":
            state, reason = "DISABLED", "SOURCE_DISABLED"
            message = "Source is disabled."
        elif received is None:
            state, reason = "UNKNOWN", "NEVER_RECEIVED"
            message = "The source has not sent a batch."
        elif interval is None:
            state, reason = "UNKNOWN", "EXPECTATION_UNAVAILABLE"
            message = "No usable source keepalive interval is configured."
        elif age_seconds > interval * config.silence_multiplier:
            state, reason = "SILENT", "KEEPALIVE_EXCEEDED"
            message = "Time since the last received batch exceeds the configured silence threshold."
        else:
            state, reason = "ALIVE", "KEEPALIVE_WITHIN_THRESHOLD"
            message = "A batch was received within the configured silence threshold."
        state_counts[state] += 1
        cur.execute("SELECT state FROM source_liveness_current WHERE source_id=%s FOR UPDATE", (row["source_id"],))
        previous = cur.fetchone()
        old_state = previous["state"] if previous else None
        cur.execute(
            """INSERT INTO source_liveness_current
                   (source_id,state,evaluated_at,last_received_at,expected_interval_seconds,age_seconds,
                    reason_code,reason_message,active_series_count,stale_series_count,missing_series_count,
                    mass_stale,last_success_at,last_error_at,last_error,connector_status)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (source_id) DO UPDATE SET
                   state=EXCLUDED.state,evaluated_at=EXCLUDED.evaluated_at,
                   last_received_at=EXCLUDED.last_received_at,
                   expected_interval_seconds=EXCLUDED.expected_interval_seconds,age_seconds=EXCLUDED.age_seconds,
                   reason_code=EXCLUDED.reason_code,reason_message=EXCLUDED.reason_message,
                   active_series_count=EXCLUDED.active_series_count,stale_series_count=EXCLUDED.stale_series_count,
                   missing_series_count=EXCLUDED.missing_series_count,mass_stale=EXCLUDED.mass_stale,
                   last_success_at=EXCLUDED.last_success_at,last_error_at=EXCLUDED.last_error_at,
                   last_error=EXCLUDED.last_error,connector_status=EXCLUDED.connector_status""",
            (row["source_id"], state, now, received, interval, age_seconds, reason, message,
             active, stale, missing, mass_stale, row["last_success_at"], row["last_error_at"],
             row["last_error"], da.Json(cur, row["connector_status"]) if row["connector_status"] is not None else None),
        )
        if old_state != state:
            cur.execute(
                """INSERT INTO source_liveness_transitions
                       (source_id,evaluated_at,from_state,to_state,reason_code,active_series_count,
                        stale_series_count,missing_series_count,mass_stale)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (source_id,evaluated_at) DO NOTHING""",
                (row["source_id"], now, old_state, state, reason, active, stale, missing, mass_stale),
            )

    cur.execute(
        """UPDATE data_gaps g SET cause = CASE
                    WHEN s.state='SILENT' OR (s.state='ALIVE' AND s.mass_stale) THEN 'SOURCE_SUSPECTED'
                    WHEN s.state='ALIVE' THEN 'NO_DATA'
                    ELSE 'UNKNOWN' END
             FROM source_liveness_current s
            WHERE g.source_id=s.source_id AND g.gap_end IS NULL
              AND s.evaluated_at=%s
              AND g.cause IS DISTINCT FROM CASE
                    WHEN s.state='SILENT' OR (s.state='ALIVE' AND s.mass_stale) THEN 'SOURCE_SUSPECTED'
                    WHEN s.state='ALIVE' THEN 'NO_DATA'
                    ELSE 'UNKNOWN' END""",
        (now,),
    )
    return {"sources_evaluated": len(source_rows), "source_states": state_counts,
            "gaps_reclassified": cur.rowcount}


def run_once(conn: Any, now: datetime, batch_size: int = PAGE_SIZE,
             source_config: SourceLivenessConfig | None = None) -> dict[str, Any]:
    """Re-evaluate every resolved series in keyset pages, committing each page.

    ``now`` is required and shared across all pages to make runs deterministic.
    A page failure is rolled back; earlier committed pages remain durable.
    """
    instant = _as_utc(now, "now")
    if batch_size < 1:
        raise ValueError("batch_size must be at least one")
    states = {name: 0 for name in STATE_NAMES}
    totals = {"series_evaluated": 0, "gaps_opened": 0, "gaps_closed": 0, "pages_committed": 0}
    after = None
    page_number = 0
    try:
        with conn.cursor() as cur:
            _prepare_candidates(cur, instant)
        conn.commit()
    except Exception as exc:
        conn.rollback()
        message = f"freshness candidate snapshot failed: {type(exc).__name__}: {str(exc)}"
        raise RuntimeError(message[:MAX_ERROR_CHARS]) from exc
    while True:
        page_number += 1
        try:
            with conn.cursor() as cur:
                candidates = _candidate_page(cur, after, batch_size)
                if not candidates:
                    conn.commit()
                    break
                after = (candidates[-1]["asset_id"], candidates[-1]["record_kind"],
                         candidates[-1]["source_id"] or 0, candidates[-1]["metric_code"] or "",
                         candidates[-1]["event_type"] or "")
                expectations = _load_expectations(cur, instant)
                series_with_obs = _attach_observations(cur, candidates)
                for series in series_with_obs:
                    exp = resolve_expectation(expectations, series, instant)
                    result = evaluate_state(series["last_observed_at"], instant, exp)
                    states[result["freshness"]] += 1
                    if exp is None:
                        # A7: absence is NOT_APPLICABLE; do not persist it.
                        cur.execute(
                            """DELETE FROM series_freshness_current
                                 WHERE source_id IS NOT DISTINCT FROM %s AND asset_id=%s AND record_kind=%s
                                   AND metric_code IS NOT DISTINCT FROM %s AND event_type IS NOT DISTINCT FROM %s""",
                            _gap_values(series),
                        )
                        totals["series_evaluated"] += 1
                        continue
                    _upsert_current(cur, series, exp, result, instant)
                    opened, closed = _update_gaps(cur, series, exp, result["freshness"], instant)
                    totals["series_evaluated"] += 1
                    totals["gaps_opened"] += opened
                    totals["gaps_closed"] += closed
            conn.commit()
            totals["pages_committed"] += 1
        except Exception as exc:
            conn.rollback()
            try:
                with conn.cursor() as cur:
                    cur.execute("DROP TABLE IF EXISTS pg_temp.freshness_candidates")
                conn.commit()
            except Exception:
                conn.rollback()
            message = f"freshness page {page_number} failed: {type(exc).__name__}: {str(exc)}"
            raise RuntimeError(message[:MAX_ERROR_CHARS]) from exc
    try:
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS pg_temp.freshness_candidates")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    try:
        with conn.cursor() as cur:
            source_totals = _evaluate_source_liveness(cur, instant, source_config or SourceLivenessConfig.from_env())
        conn.commit()
    except Exception as exc:
        conn.rollback()
        message = f"freshness source evaluation failed: {type(exc).__name__}: {str(exc)}"
        raise RuntimeError(message[:MAX_ERROR_CHARS]) from exc
    return {**totals, **source_totals, "states": states, "evaluated_at": instant}
