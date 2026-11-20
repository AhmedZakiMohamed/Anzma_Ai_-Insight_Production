"""Approved E1/A6 building-schedule defaults used when nullable E2 fields are absent."""
from datetime import time

BUILDING_TIMEZONE = "Etc/GMT-2"
WORK_START_LOCAL = time(8, 0)
WORK_END_LOCAL = time(18, 0)
NON_WORKING_WEEKDAYS: tuple[int, ...] = ()
