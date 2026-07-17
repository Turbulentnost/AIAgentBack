"""Дублирует «Тема 1» 17.07.2026 15:19–16:19 в calendar@turbo-don.ru."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from exchangelib import Attendee, Mailbox

from app.tools.Outlook.cancel_meeting import to_local
from app.tools.Outlook.company_calendar_sync import (
    find_company_calendar_item,
    sync_meeting_to_company_calendar,
)
from app.tools.Outlook.read_calendars import connect_as_owner, read_calendar_items_in_range
from app.tools.Outlook.send_meeting_invite import load_config, primary_smtp_address


SUBJECT = "Тема 1"
ATTENDEES = [
    "sktb_razvitie10@turbo-don.ru",  # Комарькова
    "sktb_razvitie2@turbo-don.ru",   # Соломичева
    "sktb_razvitie9@turbo-don.ru",   # Мангасарян
    "npo_razvitie9@turbo-don.ru",    # Азарова
]


def _attendee_list(emails: list[str]) -> list[Attendee]:
    return [Attendee(mailbox=Mailbox(email_address=email)) for email in emails]


def _find_source_meeting(config, mailbox: str, start: datetime, end: datetime):
    window_start = start - timedelta(minutes=30)
    window_end = end + timedelta(minutes=30)
    try:
        items = read_calendar_items_in_range(
            config,
            mailbox,
            range_start=window_start,
            range_end=window_end,
            max_items=50,
            load_attendees=True,
        )
    except Exception as exc:
        print(f"  {mailbox}: read failed — {exc}")
        return None
    start_local = to_local(start, config)
    for item in items:
        if getattr(item, "is_cancelled", False):
            continue
        subj = (getattr(item, "subject", "") or "").strip()
        if subj.lower() != SUBJECT.lower():
            continue
        if not getattr(item, "start", None):
            continue
        delta = abs((to_local(item.start, config) - start_local).total_seconds())
        if delta <= 5 * 60:
            print(f"  found on {mailbox}: {subj} @ {item.start}")
            return item
    return None


def main() -> None:
    config = load_config()
    tz = ZoneInfo(config.timezone)
    start = datetime(2026, 7, 17, 15, 19, tzinfo=tz)
    end = datetime(2026, 7, 17, 16, 19, tzinfo=tz)
    company = (config.company_calendar or "").strip()
    postagent = primary_smtp_address(config)

    print(f"company_calendar={company!r}")
    print(f"slot: {start.isoformat()} — {end.isoformat()}")

    existing = find_company_calendar_item(config, subject=SUBJECT, start=start, tolerance_minutes=5)
    if existing is not None:
        print("Already on company calendar:", getattr(existing, "subject", ""), existing.id)
        return

    source = None
    for mailbox in (postagent, "sktb_razvitie2@turbo-don.ru"):
        print(f"Searching {mailbox}...")
        source = _find_source_meeting(config, mailbox, start, end)
        if source is not None:
            break

    if source is None:
        print("Source not found — creating from known participants.")
        source = SimpleNamespace(
            subject=SUBJECT,
            body=None,
            start=start,
            end=end,
            location="",
            required_attendees=_attendee_list(ATTENDEES),
            optional_attendees=[],
            resources=[],
            recurrence=None,
        )

    meta = sync_meeting_to_company_calendar(source, config=config)
    print("Sync result:", meta)
    if meta.get("company_calendar_synced"):
        verify = find_company_calendar_item(config, subject=SUBJECT, start=start, tolerance_minutes=5)
        if verify is not None:
            account = connect_as_owner(config, company)
            local = to_local(verify.start, config)
            print(f"Verified on {company}: {verify.subject} @ {local.strftime('%d.%m.%Y %H:%M')}")
            from app.tools.Outlook.slot_search.attendees import calendar_item_attendee_emails

            print("Attendees:", calendar_item_attendee_emails(verify))
        else:
            print("Warning: sync reported OK but verify failed")
    else:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
