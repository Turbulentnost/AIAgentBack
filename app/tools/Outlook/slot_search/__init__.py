"""Поиск слотов Outlook/EWS."""

from __future__ import annotations

from .constants import AvailabilitySource
from .timing import setup_logging
from .rules import combine
from .rules import is_workday
from .rules import next_workday_start
from .rules import align_preferred
from .rules import not_before_now
from .rules import intervals_overlap
from .rules import slot_respects_rules
from .rules import advance_candidate
from .busy import event_interval
from .busy import freebusy_event_interval
from .busy import parse_freebusy_events
from .busy import freebusy_busy_intervals
from .busy import freebusy_events_busy_intervals
from .busy import fetch_all_busy_intervals
from .busy import coalesce_intervals
from .busy import merge_busy_intervals
from .availability import is_free_for_attendee
from .availability import is_free_for_all
from .availability import partition_attendees_at_slot
from .availability import union_busy_for_all
from .availability import verify_slot_with_calendar
from .attendees import normalize_calendar_email
from .attendees import calendar_item_attendee_emails
from .conflicts import movability_score
from .conflicts import conflicting_events_at_slot
from .conflicts import conflicting_intervals_at_slot
from .conflicts import suggest_reschedule_window
from .conflicts import build_conflict_records
from .conflicts import movability_reason
from .conflicts import conflicting_calendar_items_at_slot
from .conflicts import dedupe_conflict_records
from .conflicts import attach_reschedule_hints
from .iteration import iterate_slot_candidates
from .iteration import quorum_search_start
from .iteration import first_valid_slot_in_window
from .iteration import find_slot_via_busy_gaps
from .scoring import quorum_confidence
from .scoring import coverage_ratios
from .scoring import preliminary_slot_impact
from .scoring import slot_impact_score
from .search import find_quorum_slots
from .search import find_company_calendar_reschedule_candidates
from .search import find_nearest_slot
from .search import find_nearest_slots_per_attendee
from .api import build_slot_participant_details
from .api import format_slot
from .api import attach_room_status
from .api import dispatch_find_quorum_meeting_slots
from .api import dispatch_find_meeting_slot
from .api import dispatch_find_attendee_nearest_slots
from .api import build_parser
from .api import main

__all__ = [
    "AvailabilitySource",
    "setup_logging",
    "combine",
    "is_workday",
    "next_workday_start",
    "align_preferred",
    "not_before_now",
    "intervals_overlap",
    "slot_respects_rules",
    "advance_candidate",
    "event_interval",
    "freebusy_event_interval",
    "parse_freebusy_events",
    "freebusy_busy_intervals",
    "freebusy_events_busy_intervals",
    "fetch_all_busy_intervals",
    "coalesce_intervals",
    "merge_busy_intervals",
    "is_free_for_attendee",
    "is_free_for_all",
    "partition_attendees_at_slot",
    "union_busy_for_all",
    "verify_slot_with_calendar",
    "normalize_calendar_email",
    "calendar_item_attendee_emails",
    "movability_score",
    "conflicting_events_at_slot",
    "conflicting_intervals_at_slot",
    "suggest_reschedule_window",
    "build_conflict_records",
    "movability_reason",
    "conflicting_calendar_items_at_slot",
    "dedupe_conflict_records",
    "attach_reschedule_hints",
    "iterate_slot_candidates",
    "quorum_search_start",
    "first_valid_slot_in_window",
    "find_slot_via_busy_gaps",
    "quorum_confidence",
    "coverage_ratios",
    "preliminary_slot_impact",
    "slot_impact_score",
    "find_quorum_slots",
    "find_company_calendar_reschedule_candidates",
    "find_nearest_slot",
    "find_nearest_slots_per_attendee",
    "build_slot_participant_details",
    "format_slot",
    "attach_room_status",
    "dispatch_find_quorum_meeting_slots",
    "dispatch_find_meeting_slot",
    "dispatch_find_attendee_nearest_slots",
    "build_parser",
    "main",
]
