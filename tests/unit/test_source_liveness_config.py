"""Deterministic configuration tests for E4b source liveness thresholds."""
import os
import unittest
from unittest.mock import patch

from app.config.source_liveness import SourceLivenessConfig


class SourceLivenessConfigTests(unittest.TestCase):
    def test_valid_environment_overrides_are_used(self):
        values = {"ANZMA_SOURCE_SILENCE_MULTIPLIER": "4", "ANZMA_MASS_STALE_MIN_SERIES": "5",
                  "ANZMA_MASS_STALE_PERCENT": "75"}
        with patch.dict(os.environ, values, clear=False):
            self.assertEqual(SourceLivenessConfig.from_env(), SourceLivenessConfig(4, 5, 75))

    def test_defaults_and_invalid_values_fail_closed_to_documented_defaults(self):
        names = ("ANZMA_SOURCE_SILENCE_MULTIPLIER", "ANZMA_MASS_STALE_MIN_SERIES", "ANZMA_MASS_STALE_PERCENT")
        with patch.dict(os.environ, {}, clear=False):
            for name in names:
                os.environ.pop(name, None)
            self.assertEqual(SourceLivenessConfig.from_env(), SourceLivenessConfig(3, 3, 50))
            os.environ.update({names[0]: "0", names[1]: "not-a-number", names[2]: "101"})
            with self.assertLogs("anzma.freshness", level="WARNING") as logged:
                self.assertEqual(SourceLivenessConfig.from_env(), SourceLivenessConfig(3, 3, 50))
                self.assertEqual(SourceLivenessConfig.from_env(), SourceLivenessConfig(3, 3, 50))
            self.assertEqual(len(logged.records), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
