"""Safe defaults for freshness API snapshot metadata."""
from __future__ import annotations

import logging
import os

_LOGGER = logging.getLogger("anzma.freshness")
_WARNED_INVALID = False


def snapshot_max_age_seconds() -> int:
    """Return the maximum age for a job snapshot (three default 60-second passes)."""
    global _WARNED_INVALID
    raw = os.getenv("ANZMA_FRESHNESS_SNAPSHOT_MAX_AGE")
    if raw is None:
        return 180
    try:
        value = int(raw)
        if value > 0:
            return value
    except (TypeError, ValueError):
        pass
    if not _WARNED_INVALID:
        _LOGGER.warning("Invalid ANZMA_FRESHNESS_SNAPSHOT_MAX_AGE; using safe default 180 seconds.")
        _WARNED_INVALID = True
    return 180
