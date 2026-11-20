from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit


SCENARIOS = {"normal", "fault", "stale-silent", "bad-record", "late-out-of-order", "duplicate-batch"}
CODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
ENV_NAMES = {
    "ANZMA_SIMULATOR_CONFIG_FILE", "ANZMA_SIMULATOR_API_KEY", "ANZMA_SIMULATOR_API_KEY_FILE",
    "ANZMA_SIMULATOR_CORE_URL", "ANZMA_SIMULATOR_SOURCE_CODE", "ANZMA_SIMULATOR_BUILDING_CODE",
    "ANZMA_SIMULATOR_SCENARIO", "ANZMA_SIMULATOR_SEED", "ANZMA_SIMULATOR_BATCH_RECORDS",
    "ANZMA_SIMULATOR_INTERVAL_SECONDS", "ANZMA_SIMULATOR_STATE_PATH", "ANZMA_SIMULATOR_CLOCK_ANCHOR",
    "ANZMA_SIMULATOR_CONNECTOR_NAME", "ANZMA_SIMULATOR_CONNECTOR_VERSION",
    "ANZMA_SIMULATOR_SEED_BUILDING",
}


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    core_url: str
    source_code: str
    building_code: str
    connector_name: str
    connector_version: str
    seed: int
    scenario: str
    seed_building: str
    batch_records: int
    interval_seconds: int
    state_path: Path
    clock_anchor: str
    api_key: str = field(repr=False)
    timeout_seconds: float = 15.0


def _utc_anchor(value: str | None) -> str:
    if value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ConfigError("ANZMA_SIMULATOR_CLOCK_ANCHOR must be an RFC3339 timestamp") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ConfigError("ANZMA_SIMULATOR_CLOCK_ANCHOR must include a UTC timezone")
        return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_config(env: dict[str, str] | None = None) -> Config:
    source = os.environ if env is None else env
    values = {name: source[name] for name in ENV_NAMES if name in source}
    file_path = values.get("ANZMA_SIMULATOR_CONFIG_FILE")
    if file_path:
        try:
            with Path(file_path).open("rb") as stream:
                file_values = tomllib.load(stream)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError("could not read simulator TOML config") from exc
        section = file_values.get("simulator", file_values)
        if not isinstance(section, dict):
            raise ConfigError("simulator config must be a TOML table")
        if any(key.lower() in {"api_key", "api_key_file"} for key in section):
            raise ConfigError("TOML config may not contain credentials; use the API key environment variable or secret file")
        for key, value in section.items():
            values.setdefault("ANZMA_SIMULATOR_" + key.upper(), str(value))

    raw_key = values.get("ANZMA_SIMULATOR_API_KEY")
    key_file = values.get("ANZMA_SIMULATOR_API_KEY_FILE")
    if bool(raw_key) == bool(key_file):
        raise ConfigError("set exactly one of ANZMA_SIMULATOR_API_KEY or ANZMA_SIMULATOR_API_KEY_FILE")
    if key_file:
        try:
            api_key = Path(key_file).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ConfigError("could not read simulator API key file") from exc
    else:
        api_key = raw_key or ""
    if not re.fullmatch(r"azk_[A-Za-z0-9_-]{8,160}", api_key):
        raise ConfigError("simulator API key has an invalid shape")

    core_url = values.get("ANZMA_SIMULATOR_CORE_URL", "").rstrip("/")
    parts = urlsplit(core_url)
    if parts.scheme not in {"http", "https"} or not parts.netloc or parts.username or parts.password:
        raise ConfigError("ANZMA_SIMULATOR_CORE_URL must be an http(s) URL without embedded credentials")
    if parts.scheme != "https" and parts.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ConfigError("Core URL must use HTTPS except for localhost development")

    source_code = values.get("ANZMA_SIMULATOR_SOURCE_CODE", "")
    building_code = values.get("ANZMA_SIMULATOR_BUILDING_CODE", "")
    for name, value in (("SOURCE_CODE", source_code), ("BUILDING_CODE", building_code)):
        if not CODE.fullmatch(value):
            raise ConfigError(f"ANZMA_SIMULATOR_{name} is required and must be a contract code")
    scenario = values.get("ANZMA_SIMULATOR_SCENARIO", "normal")
    if scenario not in SCENARIOS:
        raise ConfigError("ANZMA_SIMULATOR_SCENARIO must be one of the documented scenario names")
    seed_building = values.get("ANZMA_SIMULATOR_SEED_BUILDING", "main-tower")
    if seed_building not in {"main-tower", "data-center-annex"}:
        raise ConfigError("ANZMA_SIMULATOR_SEED_BUILDING must be main-tower or data-center-annex")

    def integer(name: str, default: str, minimum: int, maximum: int) -> int:
        try:
            result = int(values.get(name, default))
        except ValueError as exc:
            raise ConfigError(f"{name} must be an integer") from exc
        if not minimum <= result <= maximum:
            raise ConfigError(f"{name} must be between {minimum} and {maximum}")
        return result

    try:
        seed = int(values.get("ANZMA_SIMULATOR_SEED", "42"))
    except ValueError as exc:
        raise ConfigError("ANZMA_SIMULATOR_SEED must be an integer") from exc
    state_path = Path(values.get("ANZMA_SIMULATOR_STATE_PATH", "~/.anzma-simulator/outbox.sqlite3")).expanduser()
    return Config(
        core_url=core_url,
        source_code=source_code,
        building_code=building_code,
        connector_name=values.get("ANZMA_SIMULATOR_CONNECTOR_NAME", "reference-simulator"),
        connector_version=values.get("ANZMA_SIMULATOR_CONNECTOR_VERSION", "0.1.0"),
        seed=seed,
        scenario=scenario,
        seed_building=seed_building,
        batch_records=integer("ANZMA_SIMULATOR_BATCH_RECORDS", "500", 1, 5000),
        interval_seconds=integer("ANZMA_SIMULATOR_INTERVAL_SECONDS", "300", 1, 86400),
        state_path=state_path,
        clock_anchor=_utc_anchor(values.get("ANZMA_SIMULATOR_CLOCK_ANCHOR")),
        api_key=api_key,
    )
