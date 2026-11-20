"""Configuration for evidence-only source liveness evaluation."""
from __future__ import annotations

from dataclasses import dataclass
import logging
import os

LOGGER = logging.getLogger("anzma.freshness")
_WARNED: set[str] = set()


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
        if value > 0:
            return value
    except (TypeError, ValueError):
        pass
    if name not in _WARNED:
        LOGGER.warning("Invalid %s; using safe default %d", name, default)
        _WARNED.add(name)
    return default


def _percent(name: str, default: int) -> int:
    value = _positive_int(name, default)
    if value <= 100:
        return value
    if name not in _WARNED:
        LOGGER.warning("Invalid %s; using safe default %d", name, default)
        _WARNED.add(name)
    return default


@dataclass(frozen=True)
class SourceLivenessConfig:
    silence_multiplier: int = 3
    mass_stale_min_series: int = 3
    mass_stale_percent: int = 50

    @classmethod
    def from_env(cls) -> "SourceLivenessConfig":
        return cls(
            silence_multiplier=_positive_int("ANZMA_SOURCE_SILENCE_MULTIPLIER", 3),
            mass_stale_min_series=_positive_int("ANZMA_MASS_STALE_MIN_SERIES", 3),
            mass_stale_percent=_percent("ANZMA_MASS_STALE_PERCENT", 50),
        )
