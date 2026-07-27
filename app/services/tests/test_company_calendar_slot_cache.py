from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.company_calendar_slot_cache import (
    CompanyCalendarSlotEvent,
    CompanyCalendarSlotSnapshot,
    get_company_calendar_snapshot,
    slots_match_snapshot,
    store_company_calendar_snapshot,
)


def test_company_calendar_slot_cache_roundtrip() -> None:
    tz = ZoneInfo("Europe/Moscow")
    slot_start = datetime(2026, 7, 17, 15, 19, tzinfo=tz)
    slot_end = datetime(2026, 7, 17, 16, 19, tzinfo=tz)
    event = CompanyCalendarSlotEvent(
        event_start=datetime(2026, 7, 17, 15, 0, tzinfo=tz),
        event_end=datetime(2026, 7, 17, 16, 0, tzinfo=tz),
        event_subject="Совещание команды",
        busy_type="Busy",
        organizer="organizer@turbo-don.ru",
        event_attendees=("a@turbo-don.ru",),
        event_attendee_names=("Иванов И.",),
    )
    snapshot = CompanyCalendarSlotSnapshot(
        calendar="calendar@turbo-don.ru",
        slot_start=slot_start,
        slot_end=slot_end,
        events=(event,),
    )
    cache_id = store_company_calendar_snapshot(snapshot)
    loaded = get_company_calendar_snapshot(cache_id)
    assert loaded is not None
    assert slots_match_snapshot(loaded, slot_start=slot_start, slot_end=slot_end)
    assert loaded.events[0].event_subject == "Совещание команды"


def test_conflicting_records_from_cached_snapshots() -> None:
    from app.tools.Outlook.outlook_config import OutlookConfig
    from app.tools.Outlook.slot_search.conflicts import (
        conflicting_company_calendar_records_from_snapshots,
    )

    tz = ZoneInfo("Europe/Moscow")
    config = OutlookConfig(
        email="svc@turbo-don.ru",
        password="secret",
        server="mail.turbo-don.ru",
        web_app_url="",
        mailbox="postagent@turbo-don.ru",
        timezone="Europe/Moscow",
        smtp_host="mail.turbo-don.ru",
        smtp_port=587,
        smtp_use_tls=True,
        smtp_from="postagent@turbo-don.ru",
        company_calendar="calendar@turbo-don.ru",
    )
    slot_start = datetime(2026, 7, 17, 15, 19, tzinfo=tz)
    slot_end = datetime(2026, 7, 17, 16, 19, tzinfo=tz)
    duration = slot_end - slot_start
    event = CompanyCalendarSlotEvent(
        event_start=datetime(2026, 7, 17, 15, 0, tzinfo=tz),
        event_end=datetime(2026, 7, 17, 16, 0, tzinfo=tz),
        event_subject="Согласование бюджета",
        busy_type="Busy",
        organizer=None,
        event_attendees=("sktb_razvitie2@turbo-don.ru",),
        event_attendee_names=("Соломичева С.",),
    )
    records = conflicting_company_calendar_records_from_snapshots(
        [event],
        slot_start,
        duration,
        config,
        attendee_email="sktb_razvitie2@turbo-don.ru",
        attendee_fio="Соломичева Светлана Викторовна",
    )
    assert len(records) == 1
    assert records[0]["event_subject"] == "Согласование бюджета"
    assert records[0]["source"] == "company_calendar"
