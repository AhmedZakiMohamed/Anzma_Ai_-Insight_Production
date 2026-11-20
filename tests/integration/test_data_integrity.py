"""
test_data_integrity.py - proves the DATABASE itself rejects bad data (FK, CHECK, UNIQUE, NOT NULL), that the views
exist with their columns, that migrations 005/006 constraints hold, and pins the
CURRENT behaviour of the *_compat tables (not enforced by the database).

Builds a throw-away database (anzma_integrity_test) with the official sequence:
  db/init/anzma_schema_and_seed.sql -> 003_incidents.sql -> 004_chat_views.sql -> 005_alert_uniqueness.sql -> 006_ingestion_foundation.sql -> 007_event_source_identity.sql -> 008_ingestion_contract_conformance.sql -> 009_freshness_expectations.sql
(migrations are applied twice to prove idempotency). Never touches any other database.

Run:  python test_data_integrity.py      (needs a reachable PostgreSQL; PGHOST/PGPORT/PGUSER/PGPASSWORD as in the other tests)
"""
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO = ROOT
sys.path.insert(0, ROOT)
import app.adapters.database.db_adapter as da                                   # noqa: E402  (selects the driver)

HOST, PORT = os.getenv("PGHOST", "localhost"), os.getenv("PGPORT", "5432")
USER, PASSWORD = os.getenv("PGUSER", "anzma"), os.getenv("PGPASSWORD", "anzma")
TEST_DB = "anzma_integrity_test"
SEQUENCE = [os.path.join(REPO, "db", "init", "anzma_schema_and_seed.sql"),
            os.path.join(ROOT, "db", "migrations", "003_incidents.sql"),
            os.path.join(ROOT, "db", "migrations", "004_chat_views.sql"),
            os.path.join(ROOT, "db", "migrations", "005_alert_uniqueness.sql"),
            os.path.join(ROOT, "db", "migrations", "006_ingestion_foundation.sql"),
            os.path.join(ROOT, "db", "migrations", "007_event_source_identity.sql"),
            os.path.join(ROOT, "db", "migrations", "008_ingestion_contract_conformance.sql"),
            os.path.join(ROOT, "db", "migrations", "009_freshness_expectations.sql")]
CHECK, FK, UNIQUE, NOTNULL = "23514", "23503", "23505", "23502"
T0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

VIEW_COLUMNS = {
    "v_data_clock": {"as_of"},
    "v_asset_overview": {"asset_id", "asset_name", "asset_type", "system", "zone", "floor", "building", "health_24h",
                         "critical_events_24h", "warning_events_24h", "open_alerts", "last_seen"},
    "v_incident_overview": {"incident_id", "title", "severity", "status", "is_active", "zone", "first_ts", "last_ts",
                            "acknowledged_ts", "resolved_ts", "alert_count"},
    "v_alert_overview": {"alert_id", "rule_id", "detection_method", "severity", "status", "is_active", "title", "summary",
                         "first_ts", "last_ts", "incident_id", "asset_name", "asset_type", "system", "zone"},
    "v_camera_status": {"asset_id", "asset_name", "zone", "last_offline", "last_online", "is_offline"},
    "v_telemetry_valid": {"telemetry_id", "asset_id", "metric_id", "ts", "value", "source_id", "received_at",
                          "quality", "ingestion_batch_id", "contract_version", "source_metadata"},
    "v_asset_current_state": {"state_id", "source_id", "asset_id", "observed_at", "received_at", "reported_state",
                              "availability", "reason_code", "message"},
    "v_unmapped_external_assets": {"source_id", "source", "external_id", "count", "first_seen", "last_seen"},
}


def connect(db, **kw):
    return da.connect(host=HOST, port=PORT, user=USER, password=PASSWORD, dbname=db, **kw)


def build_database():
    admin = connect("postgres")
    admin.autocommit = True
    with admin.cursor() as c:
        c.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
        c.execute(f"CREATE DATABASE {TEST_DB}")
    admin.close()
    conn = connect(TEST_DB)
    with conn.cursor() as c:
        for path in SEQUENCE:
            sql = open(path, encoding="utf-8").read()
            c.execute(sql); conn.commit()
            if "migrations" in path:
                c.execute(sql); conn.commit()             # idempotent: second run must not fail
    return conn


CONN = build_database()
CUR = CONN.cursor()
RESULTS = []


def test(name):
    def deco(fn):
        RESULTS.append((name, fn))
        return fn
    return deco


def sqlstate(exc):
    return getattr(exc, "sqlstate", None) or getattr(exc, "pgcode", None)


def expect_rejected(sql, params=(), code=None):
    """Run a statement that must be rejected by the database; the transaction is kept usable via a savepoint."""
    CUR.execute("SAVEPOINT s")
    try:
        CUR.execute(sql, params)
    except Exception as exc:                               # noqa: BLE001
        CUR.execute("ROLLBACK TO SAVEPOINT s")
        got = sqlstate(exc)
        assert got is not None, f"not a database error: {exc!r}"
        assert code is None or got == code, f"expected SQLSTATE {code}, got {got}: {exc}"
        return
    CUR.execute("ROLLBACK TO SAVEPOINT s")
    raise AssertionError("the database accepted a row it must reject")


def one(sql, params=()):
    CUR.execute(sql, params)
    return CUR.fetchone()


def first_id(table, col):
    return list(one(f"SELECT {col} FROM {table} ORDER BY {col} LIMIT 1").values())[0]


ASSET, METRIC, ETYPE = first_id("assets", "asset_id"), first_id("metric_types", "metric_id"), first_id("event_types", "event_type_id")
ALERT_SQL = """INSERT INTO alerts (rule_id, detection_method, severity, asset_id, title, summary, first_ts, last_ts,
                                   detected_ts, evidence, status, resolved_ts)
               VALUES (%s,%s,%s,%s,'t','s',%s,%s,%s,'{}'::jsonb,%s,%s)"""


def alert_params(rule="R1", method="threshold", sev="warning", asset=None, first=T0, last=None, status="open", resolved=None):
    return (rule, method, sev, asset or ASSET, first, last or first, first, status, resolved)


@test("clean install: sequence applied, 23 tables including freshness schema and 8 views exist")
def _():
    n = one("SELECT count(*) AS n FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'")["n"]
    assert n == 23, n
    for table in ("reporting_expectations", "data_gaps", "series_freshness_current"):
        assert one("SELECT count(*) AS n FROM information_schema.tables WHERE table_schema='public' AND table_name=%s", (table,))["n"] == 1
    v = one("SELECT count(*) AS n FROM information_schema.views WHERE table_schema='public'")["n"]
    assert v == 8, v


@test("views: all eight exist, are queryable and have exactly the documented columns")
def _():
    for view, cols in VIEW_COLUMNS.items():
        CUR.execute(f"SELECT * FROM {view} LIMIT 0")
        got = {d[0] for d in CUR.description}
        assert got == cols, (view, got ^ cols)


@test("006 is additive: existing facts remain and new provenance fields stay NULL")
def _():
    assert one("SELECT count(*) AS n FROM telemetry_records")["n"] > 0
    assert one("SELECT count(*) AS n FROM telemetry_records WHERE source_id IS NOT NULL OR quality IS NOT NULL")["n"] == 0


@test("008 is additive: per-source, batch, rejection and plausibility fields preserve legacy facts")
def _():
    assert one("SELECT count(*) AS n FROM telemetry_records WHERE source_id IS NOT NULL OR quality IS NOT NULL")["n"] == 0
    assert one("SELECT count(*) AS n FROM metric_types WHERE min_plausible IS NOT NULL OR max_plausible IS NOT NULL")["n"] == 0
    assert one("SELECT count(*) AS n FROM information_schema.columns WHERE table_name='ingestion_rejections' AND column_name IN ('source_id','external_id')")["n"] == 2
    assert one("SELECT count(*) AS n FROM information_schema.columns WHERE table_name='ingestion_batches' AND column_name IN ('delivery_mode','connector_status')")["n"] == 2
    metric_id = first_id("metric_types", "metric_id")
    expect_rejected("UPDATE metric_types SET min_plausible=10,max_plausible=5 WHERE metric_id=%s", (metric_id,), CHECK)


@test("006 credentials and source-scoped mapping constraints reject invalid rows")
def _():
    building_id = first_id("buildings", "building_id")
    CUR.execute("UPDATE buildings SET building_code='INTEGRITY-TEST' WHERE building_id=%s", (building_id,))
    CUR.execute("""INSERT INTO data_sources(source_code,building_id,connector_name,connector_version)
                    VALUES ('integrity-source',%s,'test','1') RETURNING source_id""", (building_id,))
    source_id = CUR.fetchone()["source_id"]
    expect_rejected("INSERT INTO source_credentials(source_id,key_prefix,key_hash) VALUES (%s,'plaintext','not-a-hash')",
                    (source_id,), CHECK)
    CUR.execute("SELECT a.asset_id FROM assets a JOIN locations l USING(location_id) WHERE l.building_id=%s LIMIT 1",
                (building_id,))
    asset_id = CUR.fetchone()["asset_id"]
    CUR.execute("""SELECT a.asset_id FROM assets a JOIN locations l USING(location_id)
                   WHERE l.building_id<>%s LIMIT 1""", (building_id,))
    other_building_asset = CUR.fetchone()["asset_id"]
    CUR.execute("INSERT INTO asset_external_ids(source_id,external_id,asset_id) VALUES (%s,'ext-1',%s)",
                (source_id, asset_id))
    expect_rejected("INSERT INTO asset_external_ids(source_id,external_id,asset_id) VALUES (%s,'ext-1',%s)",
                    (source_id, asset_id), UNIQUE)
    expect_rejected("INSERT INTO asset_external_ids(source_id,external_id,asset_id) VALUES (%s,'cross-building',%s)",
                    (source_id, other_building_asset), FK)
    CONN.rollback()


@test("006 valid telemetry view excludes BAD quality and retains legacy NULL quality")
def _():
    t = T0 + timedelta(days=900)
    CUR.execute("INSERT INTO telemetry_records(asset_id,metric_id,ts,value,quality) VALUES (%s,%s,%s,1,'BAD')",
                (ASSET, METRIC, t))
    assert one("SELECT count(*) AS n FROM v_telemetry_valid WHERE asset_id=%s AND ts=%s", (ASSET, t))["n"] == 0
    CUR.execute("INSERT INTO telemetry_records(asset_id,metric_id,ts,value) VALUES (%s,%s,%s,1)",
                (ASSET, METRIC, t + timedelta(seconds=1)))
    assert one("SELECT count(*) AS n FROM v_telemetry_valid WHERE asset_id=%s AND ts=%s",
               (ASSET, t + timedelta(seconds=1)))["n"] == 1
    CONN.rollback()


@test("check: telemetry NaN / Infinity / -Infinity are rejected")
def _():
    for lit in ("'NaN'", "'Infinity'", "'-Infinity'"):
        expect_rejected(f"INSERT INTO telemetry_records (asset_id, metric_id, ts, value) VALUES (%s,%s,%s,{lit}::float8)",
                        (ASSET, METRIC, T0), CHECK)


@test("check: invalid severity is rejected (event_records, alerts, incidents)")
def _():
    expect_rejected("INSERT INTO event_records (asset_id, event_type_id, ts, severity) VALUES (%s,%s,%s,'urgent')",
                    (ASSET, ETYPE, T0), CHECK)
    expect_rejected(ALERT_SQL, alert_params(sev="urgent"), CHECK)
    expect_rejected("""INSERT INTO incidents (location_id, title, severity, first_ts, last_ts)
                       SELECT location_id, 't', 'urgent', %s, %s FROM locations LIMIT 1""", (T0, T0), CHECK)


@test("check: invalid detection_method / status / explanation_source are rejected")
def _():
    expect_rejected(ALERT_SQL, alert_params(method="magic"), CHECK)
    expect_rejected(ALERT_SQL, alert_params(status="closed"), CHECK)
    expect_rejected("""INSERT INTO alerts (rule_id, detection_method, severity, asset_id, title, summary, first_ts, last_ts,
                       detected_ts, evidence, explanation_source) VALUES ('R1','threshold','info',%s,'t','s',%s,%s,%s,'{}','gpt')""",
                    (ASSET, T0, T0, T0), CHECK)


@test("check: alert with last_ts < first_ts is rejected")
def _():
    expect_rejected(ALERT_SQL, alert_params(last=T0 - timedelta(minutes=1)), CHECK)


@test("check: status=resolved without resolved_ts (and resolved_ts without resolved) is rejected")
def _():
    expect_rejected(ALERT_SQL, alert_params(status="resolved", resolved=None), CHECK)
    expect_rejected(ALERT_SQL, alert_params(status="open", resolved=T0), CHECK)


@test("check: incident last_ts < first_ts and resolved without resolved_ts are rejected")
def _():
    expect_rejected("""INSERT INTO incidents (location_id, title, severity, first_ts, last_ts)
                       SELECT location_id, 't', 'info', %s, %s FROM locations LIMIT 1""", (T0, T0 - timedelta(minutes=1)), CHECK)
    expect_rejected("""INSERT INTO incidents (location_id, title, severity, status, first_ts, last_ts)
                       SELECT location_id, 't', 'info', 'resolved', %s, %s FROM locations LIMIT 1""", (T0, T0), CHECK)


@test("check: scenario_runs end_ts < start_ts is rejected")
def _():
    expect_rejected("""INSERT INTO scenario_runs (scenario_key, name, description, asset_ids, start_ts, end_ts, expected_rules)
                       VALUES ('k','n','d','{}',%s,%s,'{}')""", (T0, T0 - timedelta(hours=1)), CHECK)


@test("check: system_types.code outside the five allowed codes is rejected")
def _():
    expect_rejected("INSERT INTO system_types (code) VALUES ('ELEVATOR')", (), CHECK)


@test("foreign key: unknown asset / metric / event type are rejected")
def _():
    expect_rejected("INSERT INTO telemetry_records (asset_id, metric_id, ts, value) VALUES (-1,%s,%s,1)", (METRIC, T0), FK)
    expect_rejected("INSERT INTO telemetry_records (asset_id, metric_id, ts, value) VALUES (%s,-1,%s,1)", (ASSET, T0), FK)
    expect_rejected("INSERT INTO event_records (asset_id, event_type_id, ts, severity) VALUES (-1,%s,%s,'info')", (ETYPE, T0), FK)
    expect_rejected("INSERT INTO event_records (asset_id, event_type_id, ts, severity) VALUES (%s,-1,%s,'info')", (ASSET, T0), FK)
    expect_rejected(ALERT_SQL, alert_params(asset=-1), FK)
    expect_rejected("INSERT INTO assets (asset_code, name, asset_type, system_type_id, location_id) VALUES ('X','x','x',-1,-1)", (), FK)


@test("not null: alert without evidence / rule_id is rejected")
def _():
    expect_rejected("""INSERT INTO alerts (rule_id, detection_method, severity, asset_id, title, summary, first_ts, last_ts, detected_ts)
                       VALUES ('R1','threshold','info',%s,'t','s',%s,%s,%s)""", (ASSET, T0, T0, T0), NOTNULL)
    expect_rejected("""INSERT INTO alerts (detection_method, severity, asset_id, title, summary, first_ts, last_ts, detected_ts, evidence)
                       VALUES ('threshold','info',%s,'t','s',%s,%s,%s,'{}')""", (ASSET, T0, T0, T0), NOTNULL)


@test("unique: duplicate telemetry (asset, metric, ts) and duplicate event (asset, event_type, ts) are rejected")
def _():
    t = T0 + timedelta(days=400)                           # a timestamp the seed cannot use
    CUR.execute("INSERT INTO telemetry_records (asset_id, metric_id, ts, value) VALUES (%s,%s,%s,1)", (ASSET, METRIC, t))
    expect_rejected("INSERT INTO telemetry_records (asset_id, metric_id, ts, value) VALUES (%s,%s,%s,2)", (ASSET, METRIC, t), UNIQUE)
    CUR.execute("INSERT INTO event_records (asset_id, event_type_id, ts, severity) VALUES (%s,%s,%s,'info')", (ASSET, ETYPE, t))
    expect_rejected("INSERT INTO event_records (asset_id, event_type_id, ts, severity) VALUES (%s,%s,%s,'info')", (ASSET, ETYPE, t), UNIQUE)
    CONN.rollback()


@test("007 event identity: source IDs may differ at one instant; legacy rows keep their natural key")
def _():
    building_id = first_id("buildings", "building_id")
    CUR.execute("UPDATE buildings SET building_code='EVENT-IDENTITY-TEST' WHERE building_id=%s", (building_id,))
    CUR.execute("""INSERT INTO data_sources(source_code,building_id,connector_name,connector_version)
                    VALUES ('event-identity-test',%s,'test','1') RETURNING source_id""", (building_id,))
    source_id = CUR.fetchone()["source_id"]
    t = T0 + timedelta(days=950)
    CUR.execute("""INSERT INTO event_records(asset_id,event_type_id,ts,severity,source_id,source_event_id)
                    VALUES (%s,%s,%s,'info',%s,'event-a'),(%s,%s,%s,'info',%s,'event-b')""",
                (ASSET, ETYPE, t, source_id, ASSET, ETYPE, t, source_id))
    assert one("SELECT count(*) AS n FROM event_records WHERE source_id=%s AND ts=%s", (source_id, t))["n"] == 2
    CUR.execute("""INSERT INTO event_records(asset_id,event_type_id,ts,severity)
                    VALUES (%s,%s,%s,'info')""", (ASSET, ETYPE, t))
    expect_rejected("""INSERT INTO event_records(asset_id,event_type_id,ts,severity)
                       VALUES (%s,%s,%s,'info')""", (ASSET, ETYPE, t), UNIQUE)
    CONN.rollback()


@test("C1 migration 005: the unique index exists and a direct duplicate alert insert is rejected")
def _():
    CUR.execute("SELECT indexdef FROM pg_indexes WHERE tablename='alerts' AND indexname='uq_alerts_rule_asset_first_ts'")
    row = CUR.fetchone()
    assert row and "UNIQUE" in row["indexdef"] and "rule_id, asset_id, first_ts" in row["indexdef"], row
    CUR.execute(ALERT_SQL, alert_params(rule="DUP", first=T0 + timedelta(days=500)))
    expect_rejected(ALERT_SQL, alert_params(rule="DUP", first=T0 + timedelta(days=500)), UNIQUE)
    CONN.rollback()


@test("C1 store_alerts: second call with the same alert returns no new id and leaves one row")
def _():
    a = {"rule_id": "STORE_DUP", "detection_method": "THRESHOLD", "severity": "WARNING", "asset_id": ASSET, "title": "t",
         "summary": "s", "first_ts": T0 + timedelta(days=510), "last_ts": T0 + timedelta(days=510),
         "detected_ts": T0, "evidence": {"k": 1}}
    assert len(da.store_alerts(CUR, [a])) == 1
    assert da.store_alerts(CUR, [a]) == []
    assert one("SELECT count(*) AS n FROM alerts WHERE rule_id='STORE_DUP'")["n"] == 1
    CONN.rollback()


@test("C1 concurrency: two connections storing the same alert at the same time create exactly one row")
def _():
    CONN.rollback()
    ts = T0 + timedelta(days=520)
    alert = {"rule_id": "RACE", "detection_method": "THRESHOLD", "severity": "WARNING", "asset_id": ASSET, "title": "t",
             "summary": "s", "first_ts": ts, "last_ts": ts, "detected_ts": T0, "evidence": {}}
    a, b = connect(TEST_DB), connect(TEST_DB)
    out = {}

    def writer_b():
        with b.cursor() as cb:
            out["b"] = da.store_alerts(cb, [alert])        # blocks on A's uncommitted row until A commits
            b.commit()

    try:
        ca = a.cursor()
        out["a"] = da.store_alerts(ca, [alert])             # A inserts but does NOT commit yet
        th = threading.Thread(target=writer_b)
        th.start()
        time.sleep(0.7)                                     # B is now waiting on the unique index
        a.commit()
        th.join(timeout=10)
        assert not th.is_alive(), "writer B never finished"
    finally:
        a.close(); b.close()
    assert len(out["a"]) == 1 and out["b"] == [], out
    assert one("SELECT count(*) AS n FROM alerts WHERE rule_id='RACE'")["n"] == 1
    CONN.commit()                                           # end the read snapshot


@test("C1 migration 005 refuses to apply (clear error) when duplicates already exist")
def _():
    CONN.rollback()
    CUR.execute("DROP INDEX uq_alerts_rule_asset_first_ts")
    ts = T0 + timedelta(days=530)
    CUR.execute(ALERT_SQL, alert_params(rule="PRE", first=ts)); CUR.execute(ALERT_SQL, alert_params(rule="PRE", first=ts))
    CONN.commit()
    CUR.execute("SAVEPOINT s")
    try:
        CUR.execute(open(SEQUENCE[3], encoding="utf-8").read())
        raise AssertionError("migration applied over existing duplicates")
    except AssertionError:
        raise
    except Exception as exc:                                # noqa: BLE001
        CONN.rollback()
        assert "duplicate" in str(exc).lower(), exc
    CUR.execute("DELETE FROM alerts WHERE rule_id='PRE' AND alert_id NOT IN (SELECT min(alert_id) FROM alerts WHERE rule_id='PRE')")
    CUR.execute(open(SEQUENCE[3], encoding="utf-8").read())  # restore the index for the rest of the run
    CONN.commit()


@test("compat tables (CURRENT BEHAVIOUR): the database does NOT enforce metric/event <-> system compatibility")
def _():
    CUR.execute("""SELECT a.asset_id, m.metric_id FROM assets a CROSS JOIN metric_types m
                   WHERE NOT EXISTS (SELECT 1 FROM metric_type_system_compat c
                                     WHERE c.metric_id = m.metric_id AND c.system_type_id = a.system_type_id) LIMIT 1""")
    bad = CUR.fetchone()
    assert bad, "seed has no incompatible (asset, metric) pair to probe with"
    CUR.execute("INSERT INTO telemetry_records (asset_id, metric_id, ts, value) VALUES (%s,%s,%s,1)",
                (bad["asset_id"], bad["metric_id"], T0 + timedelta(days=600)))   # accepted: documented gap
    CUR.execute("""SELECT a.asset_id, e.event_type_id FROM assets a CROSS JOIN event_types e
                   WHERE NOT EXISTS (SELECT 1 FROM event_type_system_compat c
                                     WHERE c.event_type_id = e.event_type_id AND c.system_type_id = a.system_type_id) LIMIT 1""")
    bad = CUR.fetchone()
    assert bad, "seed has no incompatible (asset, event type) pair to probe with"
    CUR.execute("INSERT INTO event_records (asset_id, event_type_id, ts, severity) VALUES (%s,%s,%s,'info')",
                (bad["asset_id"], bad["event_type_id"], T0 + timedelta(days=600)))
    CONN.rollback()                                         # the check is application-level (integrity_rules.py / validation.py)


if __name__ == "__main__":
    ok = 0
    for name, fn in RESULTS:
        try:
            fn(); print("PASS ", name); ok += 1
        except Exception as exc:                            # noqa: BLE001
            try:
                CONN.rollback()
            except Exception:                               # noqa: BLE001
                pass
            print("FAIL ", name, f"\n      -> {type(exc).__name__}: {exc}")
    print(f"\n{ok}/{len(RESULTS)} passed")
    sys.exit(0 if ok == len(RESULTS) else 1)
