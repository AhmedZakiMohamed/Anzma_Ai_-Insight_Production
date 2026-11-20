import logging
import os
import unittest
from unittest.mock import patch

from app.config import freshness_api


class FreshnessApiConfigTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {}, clear=False)
        self.env.start()
        self.old_value = os.environ.pop("ANZMA_FRESHNESS_SNAPSHOT_MAX_AGE", None)
        self.old_warned = freshness_api._WARNED_INVALID
        freshness_api._WARNED_INVALID = False
        self.addCleanup(self.restore)

    def restore(self):
        self.env.stop()
        if self.old_value is not None:
            os.environ["ANZMA_FRESHNESS_SNAPSHOT_MAX_AGE"] = self.old_value
        freshness_api._WARNED_INVALID = self.old_warned

    def test_snapshot_age_default_and_valid_override(self):
        self.assertEqual(freshness_api.snapshot_max_age_seconds(), 180)
        os.environ["ANZMA_FRESHNESS_SNAPSHOT_MAX_AGE"] = "90"
        self.assertEqual(freshness_api.snapshot_max_age_seconds(), 90)

    def test_invalid_snapshot_age_fails_to_default_with_one_warning(self):
        os.environ["ANZMA_FRESHNESS_SNAPSHOT_MAX_AGE"] = "0"
        with self.assertLogs("anzma.freshness", level=logging.WARNING) as captured:
            self.assertEqual(freshness_api.snapshot_max_age_seconds(), 180)
            self.assertEqual(freshness_api.snapshot_max_age_seconds(), 180)
        self.assertEqual(sum("Invalid ANZMA_FRESHNESS_SNAPSHOT_MAX_AGE" in line for line in captured.output), 1)
