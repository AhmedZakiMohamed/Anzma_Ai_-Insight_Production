import logging
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import FastAPI
from fastapi.testclient import TestClient
import socketio

from app.security import configure_cors, cors_origins_from_env, socketio_cors_origins


class CorsTests(unittest.TestCase):
    def client(self, origins):
        app = FastAPI()
        configure_cors(app, origins)

        @app.get("/dashboard")
        def dashboard():
            return {"ok": True}

        return TestClient(app)

    def test_default_rejects_cross_origin_and_same_origin_dashboard_works(self):
        origins = cors_origins_from_env({})
        self.assertEqual(origins, [])
        with self.client(origins) as client:
            request = client.get("/dashboard", headers={"Origin": "https://other.example"})
            self.assertEqual(request.status_code, 200)
            self.assertNotIn("access-control-allow-origin", request.headers)
            preflight = client.options("/dashboard", headers={
                "Origin": "https://other.example",
                "Access-Control-Request-Method": "GET",
            })
            self.assertNotIn("access-control-allow-origin", preflight.headers)
            self.assertEqual(client.get("/dashboard").status_code, 200)

    def test_allowlisted_origin_gets_cors_header_and_other_origin_does_not(self):
        origins = cors_origins_from_env({"ANZMA_CORS_ALLOWED_ORIGINS": " https://ui.example , ,http://localhost:3000 "})
        self.assertEqual(origins, ["https://ui.example", "http://localhost:3000"])
        with self.client(origins) as client:
            preflight = client.options("/dashboard", headers={
                "Origin": "https://ui.example",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            })
            self.assertEqual(preflight.headers.get("access-control-allow-origin"), "https://ui.example")
            self.assertNotIn("access-control-allow-credentials", preflight.headers)
            denied = client.get("/dashboard", headers={"Origin": "https://not-listed.example"})
            self.assertEqual(denied.status_code, 200)
            self.assertNotIn("access-control-allow-origin", denied.headers)

    def test_wildcard_and_invalid_entries_are_warned_and_ignored(self):
        log = logging.getLogger("cors-test")
        with self.assertLogs(log, level="WARNING") as captured:
            origins = cors_origins_from_env({
                "ANZMA_CORS_ALLOWED_ORIGINS": "*, ftp://bad.example, https://bad.example/path, https://good.example"
            }, log=log)
        self.assertEqual(origins, ["https://good.example"])
        self.assertEqual(len(captured.records), 3)

    def test_socketio_uses_same_allowlist_and_same_origin_default(self):
        default = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins=socketio_cors_origins([]))
        environ = {"wsgi.url_scheme": "https", "HTTP_HOST": "dashboard.example",
                   "REQUEST_METHOD": "GET", "HTTP_ORIGIN": "https://dashboard.example"}
        self.assertIn(("Access-Control-Allow-Origin", "https://dashboard.example"), default.eio._cors_headers(environ))
        cross_origin = {**environ, "HTTP_ORIGIN": "https://other.example"}
        self.assertNotIn("Access-Control-Allow-Origin", dict(default.eio._cors_headers(cross_origin)))

        origins = ["https://ui.example"]
        allowed = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins=socketio_cors_origins(origins))
        allowed_origin = {**environ, "HTTP_ORIGIN": "https://ui.example"}
        denied_origin = {**environ, "HTTP_ORIGIN": "https://evil.example"}
        self.assertIn(("Access-Control-Allow-Origin", "https://ui.example"), allowed.eio._cors_headers(allowed_origin))
        self.assertNotIn("Access-Control-Allow-Origin", dict(allowed.eio._cors_headers(denied_origin)))
        self.assertNotIn("*", allowed.eio.cors_allowed_origins)


if __name__ == "__main__":
    unittest.main(verbosity=2)
