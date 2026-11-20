from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass(frozen=True)
class Result:
    status: int
    body: dict
    idempotent_replay: bool
    retry_after: str | None = None


class TransportFailure(Exception):
    pass


class CoreClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.timeout = timeout

    def _request(self, method: str, path: str, body: bytes | None = None) -> Result:
        headers = {"Authorization": "Bearer " + self._api_key,
                   "Content-Type": "application/json; charset=utf-8"}
        request = urllib.request.Request(self.base_url + path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
                status = response.status
                replay = response.headers.get("Idempotent-Replay", "").lower() == "true"
                retry_after = response.headers.get("Retry-After")
        except urllib.error.HTTPError as response:
            raw = response.read()
            status = response.code
            replay = response.headers.get("Idempotent-Replay", "").lower() == "true"
            retry_after = response.headers.get("Retry-After")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TransportFailure("Core network request failed") from None
        try:
            value = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            value = {}
        if not isinstance(value, dict):
            value = {}
        if method == "GET":
            def redact(item):
                if isinstance(item, str):
                    return item.replace(self._api_key, "[REDACTED]")
                if isinstance(item, list):
                    return [redact(child) for child in item]
                if isinstance(item, dict):
                    return {key: redact(child) for key, child in item.items()}
                return item
            return Result(status, redact(value), replay, retry_after)
        # Do not retain or expose arbitrary response text; retain only contract fields.
        error = value.get("error") if isinstance(value.get("error"), dict) else {}
        counts = value.get("counts") if isinstance(value.get("counts"), dict) else {}
        safe = {
            "status": value.get("status") if isinstance(value.get("status"), str) else None,
            "transaction": value.get("transaction") if isinstance(value.get("transaction"), str) else None,
            "counts": {key: counts.get(key) for key in ("received", "accepted", "duplicate", "rejected")
                       if isinstance(counts.get(key), int)},
            "error": {"code": error.get("code")} if isinstance(error.get("code"), str) else {},
        }
        return Result(status, safe, replay, retry_after)

    def post_batch(self, body: bytes) -> Result:
        return self._request("POST", "/api/v1/ingest/batches", body)

    def get_batch(self, batch_id: str) -> Result:
        return self._request("GET", "/api/v1/ingest/batches/" + batch_id)
