"""Проверка синхронизации совещания Postagent -> calendar@turbo-don.ru."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.tools.Outlook.cancel_meeting import dispatch_cancel_meeting
from app.tools.Outlook.company_calendar_sync import get_company_calendar_item
from app.tools.Outlook.read_calendars import read_calendar_items_in_range
from app.tools.Outlook.send_meeting_invite import dispatch_meeting_invite, load_config


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    config = load_config()
    tz = ZoneInfo(config.timezone)
    start = (datetime.now(tz) + timedelta(days=7)).replace(
        hour=10,
        minute=0,
        second=0,
        microsecond=0,
    )
    start_str = start.strftime("%Y-%m-%d %H:%M")
    subject = f"[TEST] Company calendar sync {start.strftime('%Y%m%d')}"
    attendee = config.email.strip()

    print("=== Step 1: create test meeting ===")
    print(f"Postagent calendar: {config.mailbox or config.email}")
    print(f"Company calendar: {config.company_calendar}")
    print(f"Subject: {subject}")
    print(f"Start: {start_str}")
    print(f"Attendee: {attendee}")

    result = dispatch_meeting_invite(
        attendee=attendee,
        subject=subject,
        start=start_str,
        duration_minutes=30,
        body="Test meeting for company calendar sync verification.",
    )

    print("Create status:", result.get("status"))
    print("Postagent item_id:", result.get("outlook_item_id"))
    print("company_calendar_synced:", result.get("company_calendar_synced"))
    print("company_calendar_item_id:", result.get("company_calendar_item_id"))
    if result.get("company_calendar_error"):
        print("company_calendar_error:", result.get("company_calendar_error"))

    print()
    print("=== Step 2: verify copy in company calendar ===")
    company_item = None
    company_id = result.get("company_calendar_item_id")
    if company_id:
        company_item = get_company_calendar_item(
            config,
            item_id=company_id,
            changekey=result.get("company_calendar_changekey") or "",
        )

    end = start + timedelta(minutes=30)
    items = read_calendar_items_in_range(
        config,
        config.company_calendar,
        range_start=start - timedelta(minutes=1),
        range_end=end + timedelta(minutes=1),
        max_items=50,
    )
    found = [
        item
        for item in items
        if subject.lower() in (getattr(item, "subject", "") or "").lower()
    ]

    verified = False
    if company_item is not None:
        verified = True
        print("OK: copy found by company_calendar_item_id")
        print("  subject:", getattr(company_item, "subject", ""))
        print("  start:", getattr(company_item, "start", ""))
        print("  end:", getattr(company_item, "end", ""))
    elif found:
        verified = True
        print(f"OK: found by subject in company calendar ({len(found)} item(s))")
        for item in found:
            print("  subject:", getattr(item, "subject", ""))
            print("  start:", getattr(item, "start", ""))
            print("  end:", getattr(item, "end", ""))
    else:
        print("FAIL: event not found in company calendar")
        print("Items in interval:", len(items))

    print()
    print("=== Step 3: cleanup test meeting ===")
    if result.get("outlook_item_id"):
        cleanup = dispatch_cancel_meeting(
            item_id=result["outlook_item_id"],
            changekey=result.get("outlook_changekey") or "",
            company_calendar_item_id=result.get("company_calendar_item_id"),
            company_calendar_changekey=result.get("company_calendar_changekey"),
        )
        print("Cancel Postagent:", cleanup.get("status"))
        print("Cancel company calendar:", cleanup.get("company_calendar_synced"))
    else:
        print("Skip cleanup: no outlook_item_id")

    return 0 if verified else 1


if __name__ == "__main__":
    raise SystemExit(main())
