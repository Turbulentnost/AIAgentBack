from __future__ import annotations

from datetime import time as dt_time
from typing import Literal

AvailabilitySource = Literal["freebusy", "calendar"]

WORK_START = dt_time(8, 0)

WORK_END = dt_time(17, 0)

FORBIDDEN_BLOCKS = (
    (dt_time(12, 0), dt_time(13, 0)),
)

BUSY_STATUSES = frozenset({"Busy", "Tentative", "OOF", "WorkingElsewhere"})

FREE_BUSY_MERGED_INTERVAL_MINUTES = 30

MERGED_FREE_CHARS = frozenset({"0", ""})

LOW_MOVABILITY_SUBJECT_KEYWORDS = (
    "совет",
    "комитет",
    "правление",
    "1с",
    "board",
    "committee",
)

RESOURCE_CALENDAR_PREFIXES = ("calendar@",)

MOVABILITY_RESCHEDULE_PENALTY: dict[str, float] = {
    "high": 0.5,
    "medium": 1.0,
    "low": 2.5,
}

IMPACT_COVERAGE_WEIGHT = 1.0

IMPACT_BUSY_ATTENDEE_WEIGHT = 0.2

IMPACT_LEADERSHIP_BUSY = 5.0

IMPACT_REQUIRED_FAIL = 15.0

IMPACT_CONFLICT_WEIGHT = 0.25

QUORUM_RANK_SHORTLIST_MULTIPLIER = 5

COMPANY_CALENDAR_CHUNK_HOURS = 4

COMPANY_CALENDAR_MAX_CANDIDATES = 10

COMPANY_CALENDAR_MAX_ITEMS_PER_CHUNK = 100

COMPANY_CALENDAR_STEP_MINUTES = 15

MOVABILITY_SORT_RANK = {"high": 0, "medium": 1, "low": 2}

