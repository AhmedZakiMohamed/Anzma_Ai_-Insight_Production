from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
HTML = (ROOT / "app/dashboard/static/dashboard.html").read_text(encoding="utf-8")


class FreshnessDashboardTests(unittest.TestCase):
    def setUp(self):
        self.start = HTML.index("    async function loadFreshness()")
        self.end = HTML.index("    /* =========================================================", self.start)
        self.renderer = HTML[self.start:self.end]

    def test_freshness_panel_is_additive_and_describes_evidence_only(self):
        self.assertIn('<section id="freshnessPanel"', HTML)
        self.assertIn("Data freshness &amp; sources", HTML)
        self.assertIn("Source SILENT and gap SOURCE_SUSPECTED labels are evidence", HTML)
        self.assertIn('api("/api/v1/freshness?limit=100")', HTML)
        self.assertIn('api("/api/v1/sources/liveness?limit=100")', HTML)
        self.assertIn('api("/api/v1/data-gaps?status=open&limit=100")', HTML)
        self.assertIn('id="freshnessStaleBanner"', HTML)
        self.assertIn('new Set(["ALIVE", "SILENT", "UNKNOWN", "DISABLED"])', HTML)
        self.assertIn('$("refresh").onclick = () => { load(); loadIncidents(); };', HTML)
        self.assertNotIn("setInterval(loadFreshness", HTML)

    def test_dynamic_freshness_values_are_escaped_before_inner_html(self):
        for expression in ("${esc(item.building_code)}", "${esc(item.source_code)}", "${esc(item.asset_code)}",
                           "${esc(item.freshness)}", "${esc(source.connector_status ? JSON.stringify(source.connector_status) : \"—\")}",
                           "${esc(source.last_error || \"—\")}", "${esc(source.reason_message)}",
                           "${esc(source.mass_stale ? \"yes\" : \"no\")}", "${esc(gap.cause)}", "textContent = `Snapshot:"):
            with self.subTest(expression=expression):
                self.assertIn(expression, self.renderer)

    def test_panel_does_not_emit_local_time_or_outage_state(self):
        start = HTML.index('<section id="freshnessPanel"')
        panel = HTML[start:HTML.index("</section>", start)]
        self.assertIn("(UTC)", panel)
        self.assertNotIn("OFFLINE", panel)
        self.assertNotIn("toLocaleString", self.renderer)
        self.assertRegex(self.renderer, re.compile(r"utcValue\(gap\.gap_start\)"))
