from app.repositories.dashboard_core_pg import open_readonly, get_dashboard_summary
from app.repositories import dashboard_core_pg as core
from datetime import timedelta

conn = open_readonly()

try:
    # The API intentionally caps dashboard ranges at 31 days. Bound this smoke
    # query to the latest supported window so old fixture rows do not make the
    # default data extent exceed that limit.
    latest = conn.execute(
        "SELECT max(ts) AS latest FROM ("
        "SELECT ts FROM telemetry_records UNION ALL SELECT ts FROM event_records"
        ") AS facts"
    ).fetchone()["latest"]
    end = latest + timedelta(seconds=1)
    start = end - timedelta(days=31)
    summary = get_dashboard_summary(
        conn, start_time=core.format_ts(start), end_time=core.format_ts(end)
    )
    print(summary)
finally:
    conn.close()
