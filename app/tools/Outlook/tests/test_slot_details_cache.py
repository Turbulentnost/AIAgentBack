from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.tools.Outlook.outlook_config import OutlookConfig
from app.tools.Outlook.slot_search.api import build_slot_participant_details


def _config() -> OutlookConfig:
    return OutlookConfig(
        email="postagent@turbo-don.ru",
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


def _company_item(
    *,
    subject: str,
    start: datetime,
    end: datetime,
    attendees: list[str],
    attendee_names: list[str] | None = None,
) -> SimpleNamespace:
    def attendee(email: str, name: str | None = None) -> SimpleNamespace:
        mailbox = SimpleNamespace(
            email_address=email,
            name=name or email.split("@", 1)[0],
        )
        return SimpleNamespace(mailbox=mailbox)

    required = []
    for index, email in enumerate(attendees):
        display_name = (attendee_names or [None] * len(attendees))[index]
        required.append(attendee(email, display_name))

    return SimpleNamespace(
        subject=subject,
        start=start,
        end=end,
        is_cancelled=False,
        legacy_free_busy_status="Busy",
        required_attendees=required,
        optional_attendees=[],
        organizer=SimpleNamespace(email_address="boss@turbo-don.ru"),
    )


def _patch_freebusy(
    busy_by_attendee: dict[str, list[tuple[datetime, datetime]]],
    *,
    events_by_attendee: dict[str, list] | None = None,
):
    def _mock(*_args, **_kwargs):
        return busy_by_attendee, events_by_attendee or {}

    return patch(
        "app.tools.Outlook.slot_search.api.busy_intervals_and_events_from_freebusy",
        _mock,
    )


def test_build_slot_participant_details_all_free_refreshes_freebusy() -> None:
    config = _config()
    tz = ZoneInfo("Europe/Moscow")
    slot_start = datetime(2026, 7, 20, 11, 0, tzinfo=tz)
    slot_end = datetime(2026, 7, 20, 11, 30, tzinfo=tz)
    freebusy_called = False
    calendar_called = False

    def _freebusy(*_args, **_kwargs):
        nonlocal freebusy_called
        freebusy_called = True
        return {"a@turbo-don.ru": [], "b@turbo-don.ru": []}, {}

    def _read_calendar(_config, mailbox, **_kwargs):
        nonlocal calendar_called
        calendar_called = True
        assert mailbox == config.company_calendar
        return []

    with patch(
        "app.tools.Outlook.slot_search.api.busy_intervals_and_events_from_freebusy",
        _freebusy,
    ):
        with patch(
            "app.tools.Outlook.slot_search.api.read_calendar_items_in_range",
            _read_calendar,
        ):
            result = build_slot_participant_details(
                config=config,
                attendees=[
                    {"fio": "A", "email": "a@turbo-don.ru", "role": "initiator"},
                    {"fio": "B", "email": "b@turbo-don.ru", "role": "participant"},
                ],
                slot_start=slot_start,
                slot_end=slot_end,
                include_company_calendar=True,
                cached_busy_by_attendee={
                    "a@turbo-don.ru": [],
                    "b@turbo-don.ru": [],
                },
            )

    assert freebusy_called is True
    assert calendar_called is True
    assert all(item["status"] == "free" for item in result["participants"])


def test_build_slot_participant_details_cached_busy_reads_company_calendar_for_slot_only() -> None:
    config = _config()
    tz = ZoneInfo("Europe/Moscow")
    slot_start = datetime(2026, 7, 20, 11, 0, tzinfo=tz)
    slot_end = datetime(2026, 7, 20, 11, 30, tzinfo=tz)
    busy_block = (
        datetime(2026, 7, 20, 10, 0, tzinfo=tz),
        datetime(2026, 7, 20, 12, 0, tzinfo=tz),
    )
    company_item = _company_item(
        subject="Совещание по проекту",
        start=datetime(2026, 7, 20, 10, 30, tzinfo=tz),
        end=datetime(2026, 7, 20, 12, 0, tzinfo=tz),
        attendees=["a@turbo-don.ru"],
    )
    freebusy_called = False
    calendar_calls: list[tuple[datetime, datetime]] = []

    def _freebusy(_config, emails, *_args, **_kwargs):
        nonlocal freebusy_called
        freebusy_called = True
        return {
            "a@turbo-don.ru": [busy_block],
            "b@turbo-don.ru": [],
        }, {}

    def _read_company_calendar(_config, mailbox, *, range_start, range_end, **_kwargs):
        assert mailbox == config.company_calendar
        assert _kwargs.get("load_attendees") is False
        calendar_calls.append((range_start, range_end))
        return [company_item]

    with patch(
        "app.tools.Outlook.slot_search.api.busy_intervals_and_events_from_freebusy",
        _freebusy,
    ):
        with patch(
            "app.tools.Outlook.slot_search.api.read_calendar_items_in_range",
            _read_company_calendar,
        ):
            with patch(
                "app.tools.Outlook.slot_search.api.hydrate_company_calendar_items_for_slot",
                return_value=1,
            ) as hydrate_mock:
                result = build_slot_participant_details(
                    config=config,
                    attendees=[
                        {"fio": "A", "email": "a@turbo-don.ru", "role": "initiator"},
                        {"fio": "B", "email": "b@turbo-don.ru", "role": "participant"},
                    ],
                    slot_start=slot_start,
                    slot_end=slot_end,
                    include_company_calendar=True,
                    cached_busy_by_attendee={
                        "a@turbo-don.ru": [busy_block],
                        "b@turbo-don.ru": [],
                    },
                )

    hydrate_mock.assert_called_once()
    assert freebusy_called is True
    assert len(calendar_calls) == 1
    read_start, read_end = calendar_calls[0]
    assert read_start == slot_start - timedelta(minutes=15)
    assert read_end == slot_end + timedelta(minutes=15)
    by_email = {item["email"]: item for item in result["participants"]}
    assert by_email["a@turbo-don.ru"]["status"] == "busy"
    assert by_email["a@turbo-don.ru"]["blocking_events"][0]["event_subject"] == (
        "Совещание по проекту"
    )
    assert by_email["b@turbo-don.ru"]["status"] == "free"


def test_build_slot_participant_details_cached_busy_ignores_unrelated_company_events() -> None:
    config = _config()
    tz = ZoneInfo("Europe/Moscow")
    slot_start = datetime(2026, 7, 15, 15, 0, tzinfo=tz)
    slot_end = datetime(2026, 7, 15, 15, 30, tzinfo=tz)
    busy_block = (
        datetime(2026, 7, 15, 8, 0, tzinfo=tz),
        datetime(2026, 7, 15, 18, 0, tzinfo=tz),
    )
    unrelated = _company_item(
        subject="Weekly RG meeting",
        start=datetime(2026, 7, 15, 15, 0, tzinfo=tz),
        end=datetime(2026, 7, 15, 15, 30, tzinfo=tz),
        attendees=["other@turbo-don.ru"],
        attendee_names=["Ошмарин А.Ю."],
    )
    related = _company_item(
        subject="РГ по проекту Мангасаряна",
        start=datetime(2026, 7, 15, 15, 0, tzinfo=tz),
        end=datetime(2026, 7, 15, 16, 0, tzinfo=tz),
        attendees=["sktb_razvitie9@turbo-don.ru"],
        attendee_names=["Мангасарян Д.К."],
    )

    with _patch_freebusy({"sktb_razvitie9@turbo-don.ru": [busy_block]}):
        with patch(
            "app.tools.Outlook.slot_search.api.read_calendar_items_in_range",
            return_value=[unrelated, related],
        ):
            result = build_slot_participant_details(
                config=config,
                attendees=[
                    {
                        "fio": "Мангасарян Давид Каренович",
                        "email": "sktb_razvitie9@turbo-don.ru",
                        "role": "participant",
                    }
                ],
                slot_start=slot_start,
                slot_end=slot_end,
                include_company_calendar=True,
                cached_busy_by_attendee={"sktb_razvitie9@turbo-don.ru": [busy_block]},
            )

    participant = result["participants"][0]
    assert participant["status"] == "busy"
    assert len(participant["blocking_events"]) == 1
    assert participant["blocking_events"][0]["event_subject"] == "РГ по проекту Мангасаряна"


def test_build_slot_participant_details_cached_busy_stays_busy_when_company_calendar_fails() -> None:
    config = _config()
    tz = ZoneInfo("Europe/Moscow")
    slot_start = datetime(2026, 7, 15, 10, 0, tzinfo=tz)
    slot_end = datetime(2026, 7, 15, 11, 0, tzinfo=tz)
    busy_block = (
        datetime(2026, 7, 15, 9, 0, tzinfo=tz),
        datetime(2026, 7, 15, 12, 0, tzinfo=tz),
    )

    with _patch_freebusy(
        {
            "uk_omto12@turbo-don.ru": [busy_block],
            "sktb_std1@turbo-don.ru": [busy_block],
            "npo_razvitie3@turbo-don.ru": [busy_block],
            "free@turbo-don.ru": [],
        }
    ):
        with patch(
            "app.tools.Outlook.slot_search.api.read_calendar_items_in_range",
            side_effect=TimeoutError("Превышено время ожидания для запроса."),
        ):
            result = build_slot_participant_details(
                config=config,
                attendees=[
                    {"fio": "A", "email": "uk_omto12@turbo-don.ru", "role": "manager"},
                    {"fio": "B", "email": "sktb_std1@turbo-don.ru", "role": "director"},
                    {"fio": "C", "email": "npo_razvitie3@turbo-don.ru", "role": "participant"},
                    {"fio": "D", "email": "free@turbo-don.ru", "role": "participant"},
                ],
                slot_start=slot_start,
                slot_end=slot_end,
                include_company_calendar=True,
                cached_busy_by_attendee={
                    "uk_omto12@turbo-don.ru": [busy_block],
                    "sktb_std1@turbo-don.ru": [busy_block],
                    "npo_razvitie3@turbo-don.ru": [busy_block],
                },
            )

    by_email = {item["email"]: item for item in result["participants"]}
    assert by_email["uk_omto12@turbo-don.ru"]["status"] == "busy"
    assert by_email["sktb_std1@turbo-don.ru"]["status"] == "busy"
    assert by_email["npo_razvitie3@turbo-don.ru"]["status"] == "busy"
    assert by_email["free@turbo-don.ru"]["status"] == "free"
    assert by_email["uk_omto12@turbo-don.ru"]["blocking_events"][0]["source"] == "interval"
    assert not all(item["status"] == "free" for item in result["participants"])


def test_build_slot_participant_details_cached_busy_treated_free_without_company_match() -> None:
    config = _config()
    tz = ZoneInfo("Europe/Moscow")
    slot_start = datetime(2026, 7, 14, 12, 0, tzinfo=tz)
    slot_end = datetime(2026, 7, 14, 12, 30, tzinfo=tz)
    busy_block = (
        datetime(2026, 7, 14, 8, 0, tzinfo=tz),
        datetime(2026, 7, 14, 17, 0, tzinfo=tz),
    )
    company_item = _company_item(
        subject="Чужое совещание",
        start=datetime(2026, 7, 14, 12, 0, tzinfo=tz),
        end=datetime(2026, 7, 14, 13, 0, tzinfo=tz),
        attendees=["other@turbo-don.ru"],
    )

    with _patch_freebusy({"sktb_otp2@turbo-don.ru": [busy_block]}):
        with patch(
            "app.tools.Outlook.slot_search.api.read_calendar_items_in_range",
            return_value=[company_item],
        ):
            result = build_slot_participant_details(
                config=config,
                attendees=[
                    {"fio": "A", "email": "sktb_otp2@turbo-don.ru", "role": "initiator"},
                ],
                slot_start=slot_start,
                slot_end=slot_end,
                include_company_calendar=True,
                cached_busy_by_attendee={"sktb_otp2@turbo-don.ru": [busy_block]},
            )

    participant = result["participants"][0]
    assert participant["status"] == "free"
    assert participant["blocking_events"] == []


def test_build_slot_participant_details_cached_busy_includes_reschedule_hints() -> None:
    config = _config()
    tz = ZoneInfo("Europe/Moscow")
    slot_start = datetime(2026, 7, 16, 16, 0, tzinfo=tz)
    slot_end = datetime(2026, 7, 16, 17, 0, tzinfo=tz)
    busy_block = (
        datetime(2026, 7, 16, 16, 0, tzinfo=tz),
        datetime(2026, 7, 16, 17, 0, tzinfo=tz),
    )
    company_item = _company_item(
        subject="Тендерная комиссия",
        start=datetime(2026, 7, 16, 16, 0, tzinfo=tz),
        end=datetime(2026, 7, 16, 17, 0, tzinfo=tz),
        attendees=[
            "uk_omto12@turbo-don.ru",
            "sktb_std1@turbo-don.ru",
            "a@turbo-don.ru",
        ],
    )
    group_busy = {
        "uk_omto12@turbo-don.ru": [busy_block],
        "sktb_std1@turbo-don.ru": [busy_block],
        "a@turbo-don.ru": [],
    }

    with _patch_freebusy(group_busy):
        with patch(
            "app.tools.Outlook.slot_search.api.read_calendar_items_in_range",
            return_value=[company_item],
        ):
            with patch(
                "app.tools.Outlook.slot_search.conflicts.fetch_busy_intervals_freebusy",
                return_value=group_busy,
            ):
                result = build_slot_participant_details(
                    config=config,
                    attendees=[
                        {
                            "fio": "Донцова Анна Егоровна",
                            "email": "uk_omto12@turbo-don.ru",
                            "role": "manager",
                        }
                    ],
                    slot_start=slot_start,
                    slot_end=slot_end,
                    include_company_calendar=True,
                    light_reschedule_hints=True,
                    cached_busy_by_attendee={"uk_omto12@turbo-don.ru": [busy_block]},
                )

    participant = result["participants"][0]
    assert participant["status"] == "busy"
    event = participant["blocking_events"][0]
    assert event["event_subject"] == "Тендерная комиссия"
    assert event.get("reschedule_hint_start")
    assert event.get("reschedule_hint_end")


def test_build_slot_participant_details_cached_busy_treated_free_when_only_personal_meeting() -> None:
    config = _config()
    tz = ZoneInfo("Europe/Moscow")
    slot_start = datetime(2026, 7, 14, 15, 30, tzinfo=tz)
    slot_end = datetime(2026, 7, 14, 16, 0, tzinfo=tz)
    busy_block = (
        datetime(2026, 7, 14, 15, 48, tzinfo=tz),
        datetime(2026, 7, 14, 16, 18, tzinfo=tz),
    )
    unrelated_company = _company_item(
        subject="Чужое совещание",
        start=datetime(2026, 7, 14, 15, 30, tzinfo=tz),
        end=datetime(2026, 7, 14, 16, 0, tzinfo=tz),
        attendees=["other@turbo-don.ru"],
    )
    calendar_calls: list[str] = []

    def _read_calendar(_config, mailbox, *, range_start, range_end, **_kwargs):
        calendar_calls.append(mailbox)
        if mailbox == config.company_calendar:
            return [unrelated_company]
        raise AssertionError(f"unexpected calendar read: {mailbox}")

    with _patch_freebusy({"sktb_razvitie10@turbo-don.ru": [busy_block]}):
        with patch(
            "app.tools.Outlook.slot_search.api.read_calendar_items_in_range",
            _read_calendar,
        ):
            result = build_slot_participant_details(
                config=config,
                attendees=[
                    {
                        "fio": "Комарькова Анастасия Эдуардовна",
                        "email": "sktb_razvitie10@turbo-don.ru",
                        "role": "initiator",
                    }
                ],
                slot_start=slot_start,
                slot_end=slot_end,
                include_company_calendar=True,
                cached_busy_by_attendee={"sktb_razvitie10@turbo-don.ru": [busy_block]},
            )

    assert calendar_calls == [config.company_calendar]
    participant = result["participants"][0]
    assert participant["status"] == "free"
    assert participant["blocking_events"] == []


def test_build_slot_participant_details_freebusy_and_company_calendar_mark_busy() -> None:
    """Слот 17.07 15:20: free/busy занят + совещание в общем календаре."""
    config = _config()
    tz = ZoneInfo("Europe/Moscow")
    slot_start = datetime(2026, 7, 17, 15, 20, tzinfo=tz)
    slot_end = datetime(2026, 7, 17, 16, 20, tzinfo=tz)
    busy_block = (
        datetime(2026, 7, 17, 13, 0, tzinfo=tz),
        datetime(2026, 7, 17, 16, 30, tzinfo=tz),
    )
    company_item = _company_item(
        subject="Тема 1",
        start=datetime(2026, 7, 17, 15, 19, tzinfo=tz),
        end=datetime(2026, 7, 17, 16, 19, tzinfo=tz),
        attendees=["sktb_razvitie2@turbo-don.ru"],
        attendee_names=["Соломичева Светлана Викторовна"],
    )

    with _patch_freebusy({"sktb_razvitie2@turbo-don.ru": [busy_block]}):
        with patch(
            "app.tools.Outlook.slot_search.api.read_calendar_items_in_range",
            return_value=[company_item],
        ):
            with patch(
                "app.tools.Outlook.slot_search.api.hydrate_company_calendar_items_for_slot",
                return_value=1,
            ):
                result = build_slot_participant_details(
                    config=config,
                    attendees=[
                        {
                            "fio": "Соломичева Светлана Викторовна",
                            "email": "sktb_razvitie2@turbo-don.ru",
                            "role": "manager",
                        }
                    ],
                    slot_start=slot_start,
                    slot_end=slot_end,
                    include_company_calendar=True,
                )

    participant = result["participants"][0]
    assert participant["status"] == "busy"
    assert participant["blocking_events"][0]["event_subject"] == "Тема 1"
    assert participant["blocking_events"][0]["source"] == "company_calendar"


def test_build_slot_participant_details_manual_check_includes_reschedule_hints() -> None:
    """Ручная проверка слота: тема из calendar@ и подсказка куда перенести."""
    config = _config()
    tz = ZoneInfo("Europe/Moscow")
    slot_start = datetime(2026, 7, 17, 15, 19, tzinfo=tz)
    slot_end = datetime(2026, 7, 17, 16, 19, tzinfo=tz)
    busy_block = (
        datetime(2026, 7, 17, 15, 19, tzinfo=tz),
        datetime(2026, 7, 17, 16, 19, tzinfo=tz),
    )
    company_item = _company_item(
        subject="Тема 1",
        start=slot_start,
        end=slot_end,
        attendees=[
            "sktb_razvitie2@turbo-don.ru",
            "sktb_razvitie9@turbo-don.ru",
            "npo_razvitie9@turbo-don.ru",
        ],
    )
    group_busy = {
        "sktb_razvitie2@turbo-don.ru": [busy_block],
        "sktb_razvitie9@turbo-don.ru": [busy_block],
        "npo_razvitie9@turbo-don.ru": [busy_block],
    }

    with _patch_freebusy({"sktb_razvitie2@turbo-don.ru": [busy_block]}):
        with patch(
            "app.tools.Outlook.slot_search.api.read_calendar_items_in_range",
            return_value=[company_item],
        ):
            with patch(
                "app.tools.Outlook.slot_search.conflicts.fetch_busy_intervals_freebusy_events",
                return_value=group_busy,
            ):
                result = build_slot_participant_details(
                    config=config,
                    attendees=[
                        {
                            "fio": "Соломичева Светлана Викторовна",
                            "email": "sktb_razvitie2@turbo-don.ru",
                            "role": "manager",
                        }
                    ],
                    slot_start=slot_start,
                    slot_end=slot_end,
                    include_company_calendar=True,
                    manual_slot_check=True,
                    light_reschedule_hints=False,
                )

    participant = result["participants"][0]
    assert participant["status"] == "busy"
    event = participant["blocking_events"][0]
    assert event["event_subject"] == "Тема 1"
    assert event.get("reschedule_hint_start")
    assert event.get("reschedule_hint_end")


def test_build_slot_participant_details_enriches_subject_from_company_calendar() -> None:
    config = _config()
    tz = ZoneInfo("Europe/Moscow")
    slot_start = datetime(2026, 7, 17, 15, 20, tzinfo=tz)
    slot_end = datetime(2026, 7, 17, 16, 20, tzinfo=tz)
    busy_block = (
        datetime(2026, 7, 17, 13, 0, tzinfo=tz),
        datetime(2026, 7, 17, 16, 30, tzinfo=tz),
    )
    company_item = _company_item(
        subject="Тема 1",
        start=datetime(2026, 7, 17, 15, 19, tzinfo=tz),
        end=datetime(2026, 7, 17, 16, 19, tzinfo=tz),
        attendees=["sktb_razvitie2@turbo-don.ru"],
    )

    def _read_calendar(_config, mailbox, *, range_start, range_end, **_kwargs):
        if mailbox == config.company_calendar:
            return [company_item]
        raise AssertionError(f"unexpected calendar read: {mailbox}")

    with _patch_freebusy({"sktb_razvitie2@turbo-don.ru": [busy_block]}):
        with patch(
            "app.tools.Outlook.slot_search.api.read_calendar_items_in_range",
            _read_calendar,
        ):
            result = build_slot_participant_details(
                config=config,
                attendees=[
                    {
                        "fio": "Соломичева Светлана Викторовна",
                        "email": "sktb_razvitie2@turbo-don.ru",
                        "role": "manager",
                    }
                ],
                slot_start=slot_start,
                slot_end=slot_end,
                include_company_calendar=True,
            )

    participant = result["participants"][0]
    assert participant["status"] == "busy"
    assert participant["blocking_events"][0]["event_subject"] == "Тема 1"
    assert participant["blocking_events"][0]["source"] == "company_calendar"


def test_build_slot_participant_details_ignores_verify_personal_calendars_flag() -> None:
    config = _config()
    tz = ZoneInfo("Europe/Moscow")
    slot_start = datetime(2026, 7, 17, 15, 20, tzinfo=tz)
    slot_end = datetime(2026, 7, 17, 16, 20, tzinfo=tz)
    company_meeting = _company_item(
        subject="Тема 1",
        start=datetime(2026, 7, 17, 15, 19, tzinfo=tz),
        end=datetime(2026, 7, 17, 16, 19, tzinfo=tz),
        attendees=["sktb_razvitie2@turbo-don.ru"],
        attendee_names=["Соломичева Светлана Викторовна"],
    )

    def _read_calendar(_config, mailbox, *, range_start, range_end, **_kwargs):
        assert mailbox == config.company_calendar
        return [company_meeting]

    with _patch_freebusy({}):
        with patch(
            "app.tools.Outlook.slot_search.api.read_calendar_items_in_range",
            side_effect=_read_calendar,
        ) as read_calendar_mock:
            result = build_slot_participant_details(
                config=config,
                attendees=[
                    {
                        "fio": "Соломичева Светлана Викторовна",
                        "email": "sktb_razvitie2@turbo-don.ru",
                        "role": "manager",
                    }
                ],
                slot_start=slot_start,
                slot_end=slot_end,
                include_company_calendar=True,
                verify_personal_calendars=True,
            )

    read_calendar_mock.assert_called_once()
    participant = result["participants"][0]
    assert participant["status"] == "busy"
    assert participant["blocking_events"][0]["event_subject"] == "Тема 1"
