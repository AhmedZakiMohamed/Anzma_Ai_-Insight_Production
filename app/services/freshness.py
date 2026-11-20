"""Freshness state evaluation and paged persistence for telemetry/event series.

All wall-clock values are supplied by the caller. The database layer uses
``v_telemetry_valid`` so BAD telemetry is excluded while legacy NULL quality
continues to count as operational data, matching the ingestion contract view.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Mapping

from app.adapters.database import db_adapter as da


SCOPE_ORDER = {"ASSET_METRIC": 0, "ASSET_TYPE_METRIC": 1, "SOURCE": 2, "GLOBAL": 3}
PAGE_SIZE = 250
MAX_ERROR_CHARS = 500
STATE_NAMES = ("FRESH", "STALE", "MISSING", "UNKNOWN", "NOT_APPLICABLE")


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
WITH candidate_series AS (
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
)
SELECT source_id,asset_id,record_kind,metric_code,event_type,asset_type
FROM candidate_series
"""


def _candidate_page(cur: Any, now: datetime, after: tuple[Any, ...] | None, limit: int) -> list[dict[str, Any]]:
    if after is None:
        query = _CANDIDATE_SQL + " ORDER BY asset_id,record_kind,COALESCE(source_id,0),COALESCE(metric_code,''),COALESCE(event_type,'') LIMIT %s"
        params = (now, now, now, now, limit)
    else:
        query = _CANDIDATE_SQL + """
          WHERE ROW(asset_id,record_kind,COALESCE(source_id,0),COALESCE(metric_code,''),COALESCE(event_type,''))
             > ROW(%s::bigint,%s::text,%s::bigint,%s::text,%s::text)
          ORDER BY asset_id,record_kind,COALESCE(source_id,0),COALESCE(metric_code,''),COALESCE(event_type,'')
          LIMIT %s
        """
        params = (now, now, now, now, *after, limit)
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


def _update_gaps(cur: Any, series: Mapping[str, Any], state: str, now: datetime) -> tuple[int, int]:
    source_id, asset_id, record_kind, metric_code, event_type = _gap_values(series)
    observed = series["last_observed_at"]
    closed = 0
    if observed is not None:
        cur.execute(
            """UPDATE data_gaps SET gap_end=%s,closed_by='OBSERVATION',closed_at=%s,last_evaluated_at=%s
                 WHERE source_id IS NOT DISTINCT FROM %s AND asset_id=%s AND record_kind=%s
                   AND metric_code IS NOT DISTINCT FROM %s AND event_type IS NOT DISTINCT FROM %s
                   AND gap_end IS NULL AND gap_start < %s""",
            (observed, now, now, source_id, asset_id, record_kind, metric_code, event_type, observed),
        )
        closed = cur.rowcount

    if state == "MISSING" and observed is not None:
        cur.execute(
            """SELECT gap_id,gap_end,last_evaluated_at FROM data_gaps
                 WHERE source_id IS NOT DISTINCT FROM %s AND asset_id=%s AND record_kind=%s
                   AND metric_code IS NOT DISTINCT FROM %s AND event_type IS NOT DISTINCT FROM %s
                   AND gap_start=%s FOR UPDATE""",
            (source_id, asset_id, record_kind, metric_code, event_type, observed),
        )
        existing = cur.fetchone()
        if existing is None:
            cur.execute(
                """INSERT INTO data_gaps
                       (source_id,asset_id,record_kind,metric_code,event_type,gap_start,opened_at,last_evaluated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT ON CONSTRAINT data_gaps_series_gap_key DO NOTHING RETURNING gap_id""",
                (source_id, asset_id, record_kind, metric_code, event_type, observed, now, now),
            )
            opened = 1 if cur.fetchone() else 0
        else:
            opened = 0
            if existing["gap_end"] is None and existing["last_evaluated_at"] != now:
                cur.execute("UPDATE data_gaps SET last_evaluated_at=%s WHERE gap_id=%s", (now, existing["gap_id"]))
    else:
        opened = 0
        if state != "MISSING":
            cur.execute(
                """UPDATE data_gaps SET last_evaluated_at=%s
                     WHERE source_id IS NOT DISTINCT FROM %s AND asset_id=%s AND record_kind=%s
                       AND metric_code IS NOT DISTINCT FROM %s AND event_type IS NOT DISTINCT FROM %s
                       AND gap_end IS NULL AND last_evaluated_at IS DISTINCT FROM %s""",
                (now, source_id, asset_id, record_kind, metric_code, event_type, now),
            )
    return opened, closed


def run_once(conn: Any, now: datetime, batch_size: int = PAGE_SIZE) -> dict[str, Any]:
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
    while True:
        page_number += 1
        try:
            with conn.cursor() as cur:
                candidates = _candidate_page(cur, instant, after, batch_size)
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
                    opened, closed = _update_gaps(cur, series, result["freshness"], instant)
                    totals["series_evaluated"] += 1
                    totals["gaps_opened"] += opened
                    totals["gaps_closed"] += closed
            conn.commit()
            totals["pages_committed"] += 1
        except Exception as exc:
            conn.rollback()
            message = f"freshness page {page_number} failed: {type(exc).__name__}: {str(exc)}"
            raise RuntimeError(message[:MAX_ERROR_CHARS]) from exc
    return {**totals, "states": states, "evaluated_at": instant}
