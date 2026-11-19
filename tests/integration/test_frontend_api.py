"""
test_frontend_api.py - the API the dashboard page calls, tested on a REAL PostgreSQL built from the files Docker uses:
db/init/anzma_schema_and_seed.sql, then db/migrations/003 incidents, 004 views, 005 alert uniqueness and 006 ingestion.

Run:  PGHOST=localhost PGUSER=postgres PGPASSWORD=... python test_frontend_api.py      (user may CREATE DATABASE)
"""
import os
import sys
from datetime import timedelta
from datetime import datetime, timezone
import hashlib
import secrets
import uuid

TEST_DB = "anzma_api_test"
HOST, PORT = os.getenv("PGHOST", "localhost"), os.getenv("PGPORT", "5432")
USER, PASSWORD = os.getenv("PGUSER", "postgres"), os.getenv("PGPASSWORD", "")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import psycopg                                    # noqa: E402
from psycopg.rows import dict_row                 # noqa: E402


def sql_file(path):
    return open(os.path.join(ROOT, path), encoding="utf-8").read()


with psycopg.connect(host=HOST, port=PORT, user=USER, password=PASSWORD, dbname="postgres", autocommit=True) as admin:
    admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
    admin.execute(f"CREATE DATABASE {TEST_DB}")
DB = psycopg.connect(host=HOST, port=PORT, user=USER, password=PASSWORD, dbname=TEST_DB, row_factory=dict_row,
                     autocommit=True)
DB.execute(sql_file("db/init/anzma_schema_and_seed.sql"))
for _m in ("003_incidents", "004_chat_views", "005_alert_uniqueness", "006_ingestion_foundation"):   # runtime sequence
    DB.execute(sql_file(f"db/migrations/{_m}.sql"))

os.environ.update(PGHOST=HOST, PGPORT=PORT, PGUSER=USER, PGPASSWORD=PASSWORD, PGDATABASE=TEST_DB)
from fastapi.testclient import TestClient         # noqa: E402
import app.main as App                                        # noqa: E402

client = TestClient(App.api)
RESULTS = []


def test(name):
    def deco(fn):
        RESULTS.append((name, fn))
        return fn
    return deco


def one(sql, *params):
    return DB.execute(sql, params).fetchone()


def _ingestion_setup():
    asset = one("""SELECT a.asset_code, a.asset_id, a.system_type_id, l.building_id
                  FROM assets a JOIN locations l USING(location_id)
                  JOIN system_types st USING(system_type_id) WHERE st.code='HVAC' ORDER BY a.asset_id LIMIT 1""")
    metric = one("""SELECT m.name, m.unit FROM metric_types m JOIN metric_type_system_compat c USING(metric_id)
                  WHERE c.system_type_id=%s ORDER BY m.metric_id LIMIT 1""", asset["system_type_id"])
    building_code = "API-INGEST-TEST"
    DB.execute("UPDATE buildings SET building_code=%s WHERE building_id=%s", (building_code, asset["building_id"]))
    source_code = "api-ingest-test"
    DB.execute("""INSERT INTO data_sources(source_code,building_id,connector_name,connector_version)
                   VALUES (%s,%s,'integration-test','1')""", (source_code, asset["building_id"]))
    token = f"azk_{secrets.token_hex(6)}_{secrets.token_urlsafe(32)}"
    DB.execute("""INSERT INTO source_credentials(source_id,key_prefix,key_hash)
                   SELECT source_id,%s,%s FROM data_sources WHERE source_code=%s""",
               (token.split("_")[1], hashlib.sha256(token.encode()).hexdigest(), source_code))
    return asset, metric, building_code, source_code, token


@test("v1 ingestion: auth, per-record partial commit, replay, audit retrieval and immutable conflict")
def _():
    asset, metric, building_code, source_code, token = _ingestion_setup()
    auth = {"Authorization": f"Bearer {token}"}
    observed_at = "2000-01-01T00:00:00Z"
    good = {"record_type": "telemetry", "asset": {"asset_code": asset["asset_code"]},
            "observed_at": observed_at, "metric_code": metric["name"], "value": 12.5, "unit": metric["unit"]}
    bad = {**good, "value": 13.5, "unit": "wrong-unit"}
    batch_id = str(uuid.uuid4())
    body = {"contract_version": "1.0", "batch_id": batch_id,
            "sent_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "source": {"source_code": source_code, "connector": {"name": "integration-test", "version": "1"}},
            "building_code": building_code, "records": [good, bad]}
    assert client.post("/api/v1/ingest/batches", json=body).status_code == 401
    response = client.post("/api/v1/ingest/batches", json=body, headers=auth)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "PARTIAL" and result["transaction"] == "COMMITTED"
    assert [item["outcome"] for item in result["results"]] == ["ACCEPTED", "REJECTED"]
    assert result["counts"] == {"received": 2, "accepted": 1, "duplicate": 0, "rejected": 1}
    prior_value = one("SELECT value FROM telemetry_records WHERE asset_id=%s AND ts=%s",
                      asset["asset_id"], datetime(2000, 1, 1, tzinfo=timezone.utc))["value"]
    assert prior_value == 12.5
    replay = client.post("/api/v1/ingest/batches", json=body, headers=auth)
    assert replay.status_code == 200 and replay.headers.get("Idempotent-Replay") == "true"
    assert replay.json() == result
    duplicate_body = {**body, "batch_id": str(uuid.uuid4()), "records": [good]}
    duplicate = client.post("/api/v1/ingest/batches", json=duplicate_body, headers=auth)
    assert duplicate.status_code == 200 and duplicate.json()["results"][0]["outcome"] == "DUPLICATE"
    reused = {**body, "records": [{**good, "value": 14.0}]}
    assert client.post("/api/v1/ingest/batches", json=reused, headers=auth).status_code == 409
    conflict_body = {**body, "batch_id": str(uuid.uuid4()), "records": [{**good, "value": 99.0}]}
    conflict = client.post("/api/v1/ingest/batches", json=conflict_body, headers=auth)
    assert conflict.status_code == 422 and conflict.json()["status"] == "REJECTED"
    assert one("SELECT value FROM telemetry_records WHERE asset_id=%s AND ts=%s",
               asset["asset_id"], datetime(2000, 1, 1, tzinfo=timezone.utc))["value"] == 12.5
    audit = client.get(f"/api/v1/ingest/batches/{batch_id}", headers=auth)
    assert audit.status_code == 200 and audit.json()["rejected"][0]["payload"] == bad
    assert one("SELECT count(*) AS n FROM ingestion_rejections")["n"] == 2
    stored_hash = one("""SELECT c.key_hash FROM source_credentials c JOIN data_sources s USING(source_id)
                       WHERE s.source_code=%s""", source_code)["key_hash"].strip()
    assert stored_hash == hashlib.sha256(token.encode()).hexdigest() and token not in stored_hash
    DB.execute("""UPDATE source_credentials SET revoked_at=now() WHERE key_prefix=%s""", (token.split("_")[1],))
    assert client.post("/api/v1/ingest/batches", json=body, headers=auth).status_code == 403


@test("fresh database: no alerts and no incidents before detection (nothing pre-seeded, no demo fire)")
def _():
    assert one("SELECT count(*) AS n FROM alerts")["n"] == 0 and one("SELECT count(*) AS n FROM incidents")["n"] == 0
    assert client.get("/alerts").json()["count"] == 0
    assert one("SELECT count(*) AS n FROM event_records WHERE details->>'demo' IS NOT NULL")["n"] == 0


@test("POST /alerts/run detects the seeded faults; running it again creates nothing new")
def _():
    first = client.post("/alerts/run")
    assert first.status_code == 200 and first.json()["new_alerts"] >= 8 and first.json()["as_of"]
    assert any("current only up to" in x for x in first.json()["limitations"])
    second = client.post("/alerts/run").json()
    assert second["new_alerts"] == 0 and second["incidents_created"] == 0


@test("GET /dashboard/overview: window, five system tiles and KPI cards in the shape the page reads")
def _():
    r = client.get("/dashboard/overview", params={"hours": 24})
    assert r.status_code == 200
    d = r.json()
    assert {"from", "to", "hours", "as_of"} <= set(d["window"])
    assert [s["type"] for s in d["systems"]] == ["HVAC", "NETWORK", "ACCESS", "CCTV", "FIRE_ALARM"]
    assert all({"type", "status", "assets", "ok", "warning", "critical", "no_data", "unknown"} <= set(s) for s in d["systems"])
    assert {s["status"] for s in d["systems"]} <= {"OK", "WARNING", "CRITICAL", "UNKNOWN"}
    assert sum(s["assets"] for s in d["systems"]) == one("SELECT count(*) AS n FROM assets")["n"]
    keys = {k["key"] for k in d["kpis"]}
    assert {"monitored_assets", "healthy_assets_pct", "alerts_raised", "critical_alerts", "avg_zone_temp",
            "avg_packet_loss", "heartbeat_compliance", "open_incidents"} <= keys
    assert all({"label", "value", "unit", "definition"} <= set(k) for k in d["kpis"])
    open_n = one("SELECT count(*) AS n FROM incidents WHERE status <> 'resolved'")["n"]
    assert next(k for k in d["kpis"] if k["key"] == "open_incidents")["value"] == open_n
    bars = d["incident_bars"]
    assert bars and all({"asset_id", "asset_name", "system_type", "resolved", "warning", "critical", "info"}
                        <= set(row) for row in bars)
    hvac_bars = client.get("/dashboard/overview", params={"hours": 24, "system_type": "HVAC"}).json()["incident_bars"]
    assert hvac_bars and all(row["system_type"] == "HVAC" for row in hvac_bars)
    assert {row["asset_id"]: row for row in bars if row["system_type"] == "HVAC"} == \
        {row["asset_id"]: row for row in hvac_bars}
    short_window_bars = client.get("/dashboard/overview", params={"hours": 1}).json()["incident_bars"]
    assert short_window_bars == bars  # Incident history is not restricted to the telemetry time window.


@test("overview: KPI numbers equal the same counts computed straight from the tables")
def _():
    d = client.get("/dashboard/overview", params={"hours": 72}).json()
    end = one("SELECT max(ts) AS t FROM (SELECT ts FROM telemetry_records UNION ALL SELECT ts FROM event_records) u")["t"]
    n = one("""SELECT count(*) FILTER (WHERE severity IN ('warning','critical')) AS raised,
                      count(*) FILTER (WHERE severity = 'critical') AS crit FROM event_records
               WHERE ts >= %s - interval '72 hours' AND ts < %s + interval '1 second'""", end, end)
    k = {x["key"]: x["value"] for x in d["kpis"]}
    assert k["alerts_raised"] == n["raised"] and k["critical_alerts"] == n["crit"]
    assert k["monitored_assets"] == 12


@test("system_type accepts the page names (NETWORK, ACCESS) and the database codes; unknown value -> 400")
def _():
    for value in ("NETWORK", "NETWORKING", "ACCESS", "ACCESS_CONTROL", "HVAC"):
        assert client.get("/dashboard/overview", params={"system_type": value}).status_code == 200, value
    net = client.get("/dashboard/overview", params={"system_type": "NETWORK"}).json()
    assert next(k for k in net["kpis"] if k["key"] == "monitored_assets")["value"] == 3
    assert client.get("/dashboard/overview", params={"system_type": "PUMPS"}).status_code == 400
    assert client.get("/dashboard/overview", params={"hours": 0}).status_code == 400
    assert client.get("/dashboard/trends", params={"hours": 9999}).status_code == 400


@test("GET /dashboard/trends: aligned series, heartbeats not counted, access_granted is null (not stored), denied matches the table")
def _():
    d = client.get("/dashboard/trends", params={"hours": 72}).json()
    n = len(d["buckets"])
    for key in ("hvac_avg_temp", "bandwidth_avg", "bandwidth_peak", "access_denied"):
        assert len(d[key]) == n, key
    assert all(len(v) == n for v in d["events_by_system"].values())
    assert set(d["events_by_system"]) == {"HVAC", "NETWORK", "ACCESS", "CCTV", "FIRE_ALARM"}
    assert d["access_granted"] is None and d["bandwidth_unit"] == "%"
    end = one("SELECT max(ts) AS t FROM (SELECT ts FROM telemetry_records UNION ALL SELECT ts FROM event_records) u")["t"]
    cond = "ts >= %s - interval '72 hours' AND ts < %s + interval '1 second'"
    denied = one(f"""SELECT count(*) AS n FROM event_records x JOIN event_types t USING (event_type_id)
                     WHERE t.name='access_denied' AND {cond}""", end, end)["n"]
    non_hb = one(f"""SELECT count(*) AS n FROM event_records x JOIN event_types t USING (event_type_id)
                     WHERE t.name <> 'heartbeat' AND {cond}""", end, end)["n"]
    assert sum(d["access_denied"]) == denied > 0
    assert sum(sum(v) for v in d["events_by_system"].values()) == non_hb
    hot = max(v for v in d["hvac_avg_temp"] if v is not None)
    assert hot > 17, "HVAC temperature series must come from telemetry_records"


@test("trends: the S1 CRAC fault is visible as a temperature rise in the telemetry the chart is drawn from")
def _():
    d = client.get("/dashboard/trends", params={"hours": 72, "system_type": "HVAC"}).json()
    assert list(d["events_by_system"]) == ["HVAC"]
    peak = one("""SELECT max(value) AS v FROM telemetry_records t JOIN metric_types m USING (metric_id)
                  WHERE m.name='temperature' AND t.asset_id = 2""")["v"]
    assert peak > 30 and max(v for v in d["hvac_avg_temp"] if v is not None) > 18


@test("GET /dashboard/equipment-health: recovered history stays visible without an active critical; active alerts stay critical")
def _():
    d = client.get("/dashboard/equipment-health", params={"hours": 72}).json()
    assert len(d["items"]) == 12 and d["window"]["to"]
    need = {"name", "system_type", "building", "floor", "zone", "status", "reason", "events", "faults", "warnings",
            "criticals", "last_seen"}
    assert all(need <= set(i) for i in d["items"])
    rank = {"CRITICAL": 0, "WARNING": 1, "UNKNOWN": 2, "OK": 3}
    assert [rank[i["status"]] for i in d["items"]] == sorted(rank[i["status"]] for i in d["items"])

    # Use a dedicated fixture so this check has the same meaning whether it runs
    # before or after another test has created active alerts on the seeded CRAC.
    base_asset = one("""SELECT a.location_id, a.system_type_id FROM assets a
                       JOIN system_types st USING(system_type_id) WHERE st.code='HVAC' LIMIT 1""")
    fixture_name = f"Recovered Test Asset {uuid.uuid4().hex[:8]}"
    fixture_code = f"TEST-RECOVERED-{uuid.uuid4().hex[:8]}"
    fixture_id = one("""INSERT INTO assets(asset_code,name,asset_type,system_type_id,location_id)
                       VALUES (%s,%s,'sensor',%s,%s) RETURNING asset_id""",
                     fixture_code, fixture_name, base_asset["system_type_id"], base_asset["location_id"])["asset_id"]
    fault_id = one("SELECT event_type_id FROM event_types WHERE name='hvac_fault'")["event_type_id"]
    recovery_id = one("SELECT event_type_id FROM event_types WHERE name='hvac_recovered'")["event_type_id"]
    anchor = one("SELECT max(ts) AS t FROM (SELECT ts FROM telemetry_records UNION ALL SELECT ts FROM event_records) u")["t"]
    fault_ts, recovery_ts = anchor + timedelta(seconds=1), anchor + timedelta(minutes=1)
    DB.execute("""INSERT INTO event_records(asset_id,event_type_id,ts,severity,details)
                   VALUES (%s,%s,%s,'critical','{}'::jsonb),(%s,%s,%s,'info','{}'::jsonb)""",
               (fixture_id, fault_id, fault_ts, fixture_id, recovery_id, recovery_ts))
    try:
        refreshed = client.get("/dashboard/equipment-health", params={"hours": 72}).json()
        recovered = next(i for i in refreshed["items"] if i["name"] == fixture_name)
        assert recovered["status"] != "CRITICAL" and recovered["active_criticals"] == 0
        assert recovered["criticals"] == 1 and recovered["faults"] == 1

        crac = next(i for i in refreshed["items"] if i["name"] == "CRAC Unit 1")
        open_critical_alerts = one("""SELECT count(*) AS n FROM alerts WHERE asset_id=%s
                                    AND severity='critical' AND status IN ('open','acknowledged')""",
                                   next(i["asset_id"] for i in refreshed["items"] if i["name"] == "CRAC Unit 1"))["n"]
        assert (crac["status"] == "CRITICAL") == (open_critical_alerts > 0)
        assert crac["active_criticals"] >= open_critical_alerts
    finally:
        DB.execute("DELETE FROM event_records WHERE asset_id=%s", (fixture_id,))
        DB.execute("DELETE FROM assets WHERE asset_id=%s", (fixture_id,))

    healthy = next(i for i in d["items"] if i["name"] == "Lobby Camera")
    assert healthy["status"] == "OK" and healthy["last_seen"]
    only = client.get("/dashboard/equipment-health", params={"hours": 72, "system_type": "CCTV"}).json()
    assert {i["system_type"] for i in only["items"]} == {"CCTV"} and len(only["items"]) == 2


@test("status semantics: never-reported assets are Unknown; stale previously reported assets are Warning")
def _():
    anchor = one("SELECT max(ts) AS t FROM (SELECT ts FROM telemetry_records UNION ALL SELECT ts FROM event_records) u")["t"]
    location_id = one("SELECT location_id FROM locations ORDER BY location_id LIMIT 1")["location_id"]
    system_id = one("SELECT system_type_id FROM system_types WHERE code='HVAC'")["system_type_id"]
    never_id = one("""INSERT INTO assets (asset_code,name,asset_type,system_type_id,location_id)
                       VALUES ('TEST-UNKNOWN','Never Reported Test Asset','sensor',%s,%s) RETURNING asset_id""",
                   system_id, location_id)["asset_id"]
    stale_id = one("""INSERT INTO assets (asset_code,name,asset_type,system_type_id,location_id)
                      VALUES ('TEST-STALE','Stale Test Asset','sensor',%s,%s) RETURNING asset_id""",
                   system_id, location_id)["asset_id"]
    try:
        metric_id = one("SELECT metric_id FROM metric_types WHERE name='temperature'")["metric_id"]
        DB.execute("INSERT INTO telemetry_records (asset_id,metric_id,ts,value) VALUES (%s,%s,%s,%s)",
                   (stale_id, metric_id, anchor - timedelta(hours=2), 22.0))
        d = client.get("/dashboard/equipment-health", params={"hours": 1, "system_type": "HVAC"}).json()
        by_name = {item["name"]: item for item in d["items"]}
        assert by_name["Never Reported Test Asset"]["status"] == "UNKNOWN"
        assert by_name["Stale Test Asset"]["status"] == "WARNING"
        overview = client.get("/dashboard/overview", params={"hours": 1}).json()
        hvac = next(system for system in overview["systems"] if system["type"] == "HVAC")
        assert hvac["unknown"] >= 1 and hvac["no_data"] >= 1 and hvac["status"] != "OK"
        chat = client.post("/chat", json={"message": "What is the current building status?", "hours": 1,
                                          "use_llm": False}).json()
        assert chat["source"] == "DATABASE" and "1 unknown" in chat["answer"]
    finally:
        DB.execute("DELETE FROM telemetry_records WHERE asset_id = ANY(%s)", ([stale_id, never_id],))
        DB.execute("DELETE FROM assets WHERE asset_id = ANY(%s)", ([stale_id, never_id],))


@test("GET /alerts: stored alerts, newest first, UI names and upper-case enums; filters work")
def _():
    d = client.get("/alerts", params={"limit": 50}).json()
    assert d["count"] == one("SELECT count(*) AS n FROM alerts")["n"] == len(d["items"])
    need = {"id", "severity", "title", "first_ts", "system_type", "asset_name", "zone", "summary", "status", "incident_id"}
    assert all(need <= set(a) for a in d["items"])
    assert {a["severity"] for a in d["items"]} <= {"INFO", "WARNING", "CRITICAL"}
    assert {a["system_type"] for a in d["items"]} <= {"HVAC", "NETWORK", "ACCESS", "CCTV", "FIRE_ALARM"}
    times = [a["first_ts"] for a in d["items"]]
    assert times == sorted(times, reverse=True)
    crit = client.get("/alerts", params={"severity": "CRITICAL"}).json()["items"]
    assert crit and all(a["severity"] == "CRITICAL" for a in crit)
    net = client.get("/alerts", params={"system_type": "NETWORK"}).json()["items"]
    assert net and all(a["system_type"] == "NETWORK" for a in net)
    assert client.get("/alerts", params={"severity": "BOGUS"}).status_code == 400
    assert client.get("/alerts", params={"limit": 0}).status_code == 422


@test("GET /alerts/{id}: evidence rows carry source references; unknown id -> 404")
def _():
    a = client.get("/alerts", params={"limit": 50}).json()["items"]
    crac = next(x for x in a if x["rule_id"] == "HVAC_HIGH_TEMP")
    one_alert = client.get(f"/alerts/{crac['id']}").json()
    ev = one_alert["evidence"]["events"]
    assert ev and all(e["source"] == "telemetry" and e["telemetry_id"] == e["record_id"] for e in ev)
    assert client.get("/alerts/999999").status_code == 404


@test("incidents: list, evidence verified, explanation grounded; the page's three calls work together")
def _():
    inc_list = client.get("/incidents").json()["incidents"]
    assert inc_list and inc_list[0]["severity"] == "critical"
    crac = next(i for i in inc_list if "CRAC" in i["title"])
    detail = client.get(f"/incidents/{crac['id']}").json()
    assert detail["recommended_actions"] and detail["grouping_rule"] == "same_asset_or_system_within_15_min"
    ev = client.get(f"/incidents/{crac['id']}/evidence").json()
    assert ev["total"] > 0 and ev["verified"] == ev["total"] and ev["unverified"] == []
    alert_id = detail["alert_ids"][0]
    ex = client.post(f"/alerts/{alert_id}/explain", params={"use_llm": "false"}).json()
    assert ex["validation"]["status"] == "grounding_supported" and ex["validation"]["scope"] == "grounding"
    assert ex["evidence_verification"]["unverified"] == 0 and ex["recommendations"]
    assert all(r["status"] == "approved" for r in ex["recommendations"])


@test("incident lifecycle over HTTP: open -> acknowledged -> resolved; resolved is final (409); unknown -> 404")
def _():
    incident = client.get("/incidents").json()["incidents"][0]
    inc_id = incident["id"]
    assert incident["severity"] == "critical"
    asset_id = one("SELECT asset_id FROM alerts WHERE incident_id=%s ORDER BY first_ts, alert_id LIMIT 1", inc_id)["asset_id"]
    before = client.get("/dashboard/overview", params={"hours": 1}).json()["incident_bars"]
    before_bar = next(row for row in before if row["asset_id"] == asset_id)
    assert before_bar["critical"] >= 1
    r = client.post(f"/incidents/{inc_id}/status", json={"status": "acknowledged"})
    assert r.status_code == 200 and r.json()["status"] == "acknowledged"
    assert client.post(f"/incidents/{inc_id}/status", json={"status": "resolved"}).json()["status"] == "resolved"
    assert one("SELECT count(*) AS n FROM alerts WHERE incident_id=%s AND status <> 'resolved'", inc_id)["n"] == 0
    after = client.get("/dashboard/overview", params={"hours": 1}).json()["incident_bars"]
    after_bar = next(row for row in after if row["asset_id"] == asset_id)
    assert after_bar["resolved"] == before_bar["resolved"] + 1
    assert after_bar["critical"] == before_bar["critical"] - 1
    assert client.post(f"/incidents/{inc_id}/status", json={"status": "open"}).status_code == 409
    assert client.post("/incidents/999999/status", json={"status": "resolved"}).status_code == 404
    kpi = client.get("/dashboard/overview").json()["kpis"]
    assert next(k for k in kpi if k["key"] == "open_incidents")["value"] == \
        one("SELECT count(*) AS n FROM incidents WHERE status <> 'resolved'")["n"]


@test("alerts and tampered evidence: a changed fact row is reported as unverified by the explain call")
def _():
    a = next(x for x in client.get("/alerts", params={"limit": 50}).json()["items"] if x["rule_id"] == "HVAC_HIGH_TEMP")
    tid = client.get(f"/alerts/{a['id']}").json()["evidence"]["events"][0]["telemetry_id"]
    DB.execute("UPDATE telemetry_records SET value = value + 5 WHERE telemetry_id = %s", (tid,))
    try:
        ex = client.post(f"/alerts/{a['id']}/explain", params={"use_llm": "false"}).json()
    finally:
        DB.execute("UPDATE telemetry_records SET value = value - 5 WHERE telemetry_id = %s", (tid,))
    assert ex["evidence_verification"]["unverified"] >= 1 and ex["validation"]["status"] == "failed"


@test("chat widget: suggested questions; HVAC question answered from evidence; incident list; unsupported topic is said plainly")
def _():
    q = client.get("/chat/questions").json()
    assert q["questions"]
    hv = client.post("/chat", json={"message": "Why is CRAC 1 temperature high?", "use_llm": False}).json()
    assert hv["source"] == "FALLBACK" and hv["grounding"]["passed"] is True and hv["sources"]
    assert all("verified in database" in s["text"] for s in hv["sources"]) and "[F1]" in hv["answer"]
    from app.domain.alerts.alert_config import T
    latest_crac = one("""SELECT a.name, l.name AS zone FROM telemetry_records t
                        JOIN assets a USING (asset_id) JOIN locations l USING (location_id)
                        JOIN metric_types m USING (metric_id) JOIN system_types st USING (system_type_id)
                        WHERE st.code='HVAC' AND a.name ILIKE %s AND m.name='temperature'
                        ORDER BY t.ts DESC, t.telemetry_id DESC LIMIT 1""", "%crac%")
    configured_threshold = T["server_temp_warn"] if latest_crac["zone"] == "Server Room" else T["hvac_temp_warn"]
    assert latest_crac["name"] in hv["answer"] and f"{configured_threshold:g}C" in hv["answer"]
    assert "configured" in hv["answer"] and "hardcoded" not in hv["answer"].lower()
    inc = client.post("/chat", json={"message": "What is wrong with the building?", "use_llm": False}).json()
    assert inc["answer"].startswith("Open incidents") and "CRAC" in inc["answer"]
    other = client.post("/chat", json={"message": "tell me a joke please", "use_llm": False}).json()
    assert other["source"] == "RULES" and other["grounding"]["passed"] is False
    assert client.post("/chat", json={"message": "x"}).status_code == 422


@test("pages: / and /dashboard serve the dashboard; /static files are served; /health answers")
def _():
    for path in ("/", "/dashboard"):
        r = client.get(path)
        assert r.status_code == 200 and "Building operations dashboard" in r.text, path
    assert client.get("/static/style.css").status_code == 200 and client.get("/static/chart.umd.js").status_code == 200
    assert client.get("/health").json() == {"status": "ok"}
    html = client.get("/").text
    assert 'api("overview")' in html and "/alerts?limit=50" in html


@test("database down: every page endpoint answers 503 with a message instead of crashing")
def _():
    saved = os.environ["PGPORT"]
    os.environ["PGPORT"] = "1"
    try:
        calls = [client.get("/dashboard/overview"), client.get("/dashboard/trends"),
                 client.get("/dashboard/equipment-health"), client.get("/alerts"), client.get("/incidents"),
                 client.post("/alerts/run"), client.post("/chat", json={"message": "crac temperature"})]
        assert [c.status_code for c in calls] == [503] * len(calls), [c.status_code for c in calls]
    finally:
        os.environ["PGPORT"] = saved


@test("readiness: /ready is 200 while the database answers and 503 when it is unreachable; /health stays liveness-only")
def _():
    up = client.get("/ready")
    assert up.status_code == 200 and up.json() == {"status": "ready", "database": "up"}, up.text
    saved = os.environ["PGPORT"]
    os.environ["PGPORT"] = "1"
    try:
        down = client.get("/ready")
        assert down.status_code == 503 and down.json() == {"status": "not_ready", "database": "down"}, down.text
        assert client.get("/health").json() == {"status": "ok"}
    finally:
        os.environ["PGPORT"] = saved


@test("legacy POST /events and POST /alerts remain available alongside v1 ingestion")
def _():
    asset = one("""SELECT a.asset_id FROM assets a JOIN system_types st USING(system_type_id)
                   WHERE st.code='HVAC' ORDER BY a.asset_id LIMIT 1""")
    ts = "2001-01-01T00:00:00Z"
    event = client.post("/events", json={"asset_id": asset["asset_id"], "event_type": "hvac_fault",
                                        "ts": ts, "severity": "INFO", "details": {"compat": True}})
    assert event.status_code == 201, event.text
    alert = client.post("/alerts", json={"rule_id": "HVAC_HIGH_TEMP", "asset_id": asset["asset_id"],
                                        "severity": "WARNING", "summary": "legacy endpoint compatibility",
                                        "first_ts": ts, "facts": {"compat": True}})
    assert alert.status_code == 201, alert.text


if __name__ == "__main__":
    ok = 0
    for name, fn in RESULTS:
        try:
            fn()
            print("PASS ", name)
            ok += 1
        except Exception as exc:
            print("FAIL ", name, f"\n      -> {type(exc).__name__}: {exc}")
    print(f"\n{ok}/{len(RESULTS)} passed")
    sys.exit(0 if ok == len(RESULTS) else 1)
