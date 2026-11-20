from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path


CATALOGUE = Path(__file__).with_name("catalogue.json")
GENERATOR_VERSION = "1"
ASSETS = {item["asset_code"]: item for item in json.loads(CATALOGUE.read_text(encoding="utf-8"))["assets"]}
POINTS = json.loads(CATALOGUE.read_text(encoding="utf-8"))["points"]
EVENTS = json.loads(CATALOGUE.read_text(encoding="utf-8"))["events"]


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _noise(seed: int, asset: str, metric: str, stamp: str) -> float:
    material = f"{seed}|{asset}|{metric}|{stamp}|{GENERATOR_VERSION}".encode()
    sample = int.from_bytes(hashlib.sha256(material).digest()[:8], "big") / (2**64 - 1)
    return sample - 0.5


def _event_id(seed: int, scenario: str, asset: str, key: str) -> str:
    digest = hashlib.sha256(f"{seed}|{scenario}|{asset}|{key}".encode()).hexdigest()[:32]
    return "sim-" + digest


def generate_records(seed: int, scenario: str, clock_anchor: str,
                     seed_building: str = "main-tower") -> tuple[list[dict], str]:
    if scenario == "duplicate-batch":
        return [], "LIVE"
    anchor = datetime.fromisoformat(clock_anchor.replace("Z", "+00:00")).astimezone(timezone.utc)
    anchor = anchor.replace(minute=(anchor.minute // 15) * 15, second=0, microsecond=0)
    site_assets = {code: asset for code, asset in ASSETS.items() if asset["seed_site"] == seed_building}
    if not site_assets:
        raise ValueError("seed building has no catalogued assets")
    records: list[dict] = []

    for step in range(-312, 1):
        observed = anchor + timedelta(minutes=15 * step)
        stamp = _timestamp(observed)
        for point in POINTS:
            asset_code = point["asset_code"]
            if asset_code not in site_assets:
                continue
            minutes_ago = -15 * step
            if asset_code == "CCTV-CAM-02" and scenario == "stale-silent":
                if 2640 <= minutes_ago <= 2700 or minutes_ago <= 300:
                    continue
            phase = 2 * math.pi * observed.timestamp() / 86400.0
            value = point["base"] + point["amplitude"] * math.sin(phase)
            value += _noise(seed, asset_code, point["metric_code"], stamp) * point["noise"]
            if scenario == "fault" and asset_code == "HVAC-CRAC-01" and point["metric_code"] == "temperature" and 1170 <= minutes_ago <= 1260:
                value += 9
            if scenario == "fault" and asset_code == "NET-SW-01" and point["metric_code"] == "packet_loss" and 1830 <= minutes_ago <= 1860:
                value += 4
            value = round(max(0.0, value), 2)
            records.append({
                "record_type": "telemetry",
                "asset": {"external_id": ASSETS[asset_code]["external_id"]},
                "metric_code": point["metric_code"], "value": value,
                "unit": point["unit"], "observed_at": stamp,
            })

    for hour in range(-78, 1):
        observed = anchor + timedelta(hours=hour)
        stamp = _timestamp(observed)
        for asset_code, asset in site_assets.items():
            minutes_ago = -60 * hour
            if asset_code == "CCTV-CAM-02" and scenario == "stale-silent":
                if 2640 <= minutes_ago <= 2700 or minutes_ago <= 300:
                    continue
            records.append({
                "record_type": "event", "source_event_id": _event_id(seed, scenario, asset_code, "heartbeat|" + stamp),
                "asset": {"external_id": asset["external_id"]}, "event_type": "heartbeat",
                "observed_at": stamp, "severity": "INFO", "details": {"source": "heartbeat"},
            })

    if scenario == "fault":
        for event in EVENTS:
            if event["asset_code"] not in site_assets:
                continue
            observed = anchor - timedelta(minutes=event["minutes_ago"])
            stamp = _timestamp(observed)
            asset_code = event["asset_code"]
            logical = f"{event['event_type']}|{stamp}|{canonical_json(event['details']).decode()}"
            records.append({
                "record_type": "event", "source_event_id": _event_id(seed, scenario, asset_code, logical),
                "asset": {"external_id": site_assets[asset_code]["external_id"]},
                "event_type": event["event_type"], "observed_at": stamp,
                "severity": event["severity"], "details": event["details"],
            })

    if scenario == "bad-record":
        valid = next(record for record in records if record["record_type"] == "telemetry")
        records.append({**valid, "quality": "MISSING"})

    records.sort(key=lambda rec: (rec["observed_at"], rec["record_type"],
                                  rec["asset"]["external_id"],
                                  rec.get("metric_code", rec.get("event_type", "")),
                                  rec.get("source_event_id", "")))
    delivery = "BACKFILL" if scenario == "late-out-of-order" else "LIVE"
    if scenario == "late-out-of-order":
        records.reverse()
    return records, delivery
