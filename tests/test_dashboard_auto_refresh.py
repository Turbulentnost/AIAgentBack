from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest

from app.agents.document_analysis_agent.excel_service import UploadedWorkbook
from app.api.v1.endpoints import agents


def _refresh_inputs() -> dict[str, object]:
    return {
        "version": 1,
        "files": [
            {
                "file_name": "production.xlsx",
                "file_base64": base64.b64encode(b"production").decode("ascii"),
                "file_sha256": "unused",
                "size": 10,
            }
        ],
    }


def test_refresh_inputs_skip_shipment_master_data() -> None:
    payload = agents._build_dashboard_refresh_inputs(
        [
            UploadedWorkbook(filename="production.xlsx", content=b"production"),
            UploadedWorkbook(filename="merged_schedule.xlsx", content=b"merged"),
            UploadedWorkbook(filename="график отгрузок.xlsx", content=b"shipment"),
        ]
    )

    files = payload["files"]
    assert isinstance(files, list)
    assert [item["file_name"] for item in files] == ["production.xlsx"]


@pytest.mark.asyncio
async def test_auto_refresh_rebuilds_stale_snapshot_once(monkeypatch: pytest.MonkeyPatch) -> None:
    today = agents.today_msk_iso()
    saved: dict[str, object] = {}
    snapshot = {
        "analyzed_at": "2026-08-11T08:00:00+03:00",
        "dashboard_date_msk": "2026-08-11",
        "refresh_inputs": _refresh_inputs(),
        "logistics_risks": {"as_of": None, "stages": []},
    }

    async def fake_prepare(uploaded, db):
        assert [item.filename for item in uploaded] == ["production.xlsx"]
        return uploaded, {"stats": {"google_sheets": {"included": True}}}

    async def fake_analyze(uploaded, db=None, user_id=None):
        return SimpleNamespace(
            logistics_risks=None,
            shift_assignment_xlsx_bytes=None,
            shift_assignment_values=[],
            shift_assignment_row_priorities=[],
            shift_assignment_row_kinds=[],
            shift_assignment_meta={},
            shift_assignment_file_name=None,
            coverage_dashboard={"today": True},
            source="test",
            stock_files=[],
            shipment_files=["merged_schedule.xlsx"],
            merged_nomenclatures=[],
        )

    def fake_save(user_id, **kwargs):
        saved.update(kwargs)
        return {
            "dashboard_date_msk": kwargs["dashboard_date_msk"],
            "refresh_status": kwargs["refresh_status"],
            "coverage_dashboard": kwargs["coverage_dashboard"],
        }

    monkeypatch.setattr(agents, "_prepare_aveon_uploaded_with_server_shipment", fake_prepare)
    monkeypatch.setattr(agents, "analyze_aveon_excel_files", fake_analyze)
    monkeypatch.setattr(agents, "load_dashboard_snapshot", lambda user_id: snapshot)
    monkeypatch.setattr(agents, "save_dashboard_snapshot", fake_save)
    monkeypatch.setattr(agents, "_auto_refresh_inputs_error", lambda uploaded: None)

    refreshed = await agents._auto_refresh_dashboard_snapshot(
        user_id="user-1",
        db=None,
        snapshot=snapshot,
    )

    assert refreshed["dashboard_date_msk"] == today
    assert refreshed["refresh_status"] == "auto_refreshed"
    assert saved["dashboard_date_msk"] == today
    assert isinstance(saved["refresh_inputs"], dict)
    assert saved["refresh_source_analyzed_at"] == snapshot["analyzed_at"]


@pytest.mark.asyncio
async def test_dashboard_latest_does_not_refresh_current_day(monkeypatch: pytest.MonkeyPatch) -> None:
    today_snapshot = {
        "dashboard_date_msk": agents.today_msk_iso(),
        "logistics_risks": {"as_of": None, "stages": []},
    }

    async def fail_refresh(**kwargs):
        raise AssertionError("current-day snapshot should not be refreshed")

    monkeypatch.setattr(agents, "load_dashboard_snapshot", lambda user_id: today_snapshot)
    monkeypatch.setattr(agents, "_auto_refresh_dashboard_snapshot", fail_refresh)

    response = await agents.get_document_analysis_dashboard_latest(None, None)

    assert response == {"ok": True, "snapshot": today_snapshot}


@pytest.mark.asyncio
async def test_dashboard_latest_does_not_retry_failed_refresh_same_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale_after_failed_refresh = {
        "dashboard_date_msk": "2026-08-11",
        "refresh_attempted_date_msk": agents.today_msk_iso(),
        "refresh_status": "error",
        "refresh_error": "Google Sheets unavailable",
        "logistics_risks": {"as_of": None, "stages": []},
    }

    async def fail_refresh(**kwargs):
        raise AssertionError("failed refresh should not be retried on the same day")

    monkeypatch.setattr(agents, "load_dashboard_snapshot", lambda user_id: stale_after_failed_refresh)
    monkeypatch.setattr(agents, "_auto_refresh_dashboard_snapshot", fail_refresh)

    response = await agents.get_document_analysis_dashboard_latest(None, None)

    assert response == {"ok": True, "snapshot": stale_after_failed_refresh}


@pytest.mark.asyncio
async def test_dashboard_latest_retries_missing_inputs_same_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale_missing_inputs = {
        "dashboard_date_msk": "2026-08-11",
        "refresh_attempted_date_msk": agents.today_msk_iso(),
        "refresh_status": "missing_inputs",
        "refresh_error": "Нет сохранённых входных файлов для автопересчёта.",
        "logistics_risks": {"as_of": "2026-08-11", "stages": []},
    }
    refreshed = {
        **stale_missing_inputs,
        "dashboard_date_msk": agents.today_msk_iso(),
        "refresh_status": "auto_refreshed",
    }

    async def fake_refresh(**kwargs):
        return refreshed

    monkeypatch.setattr(agents, "load_dashboard_snapshot", lambda user_id: stale_missing_inputs)
    monkeypatch.setattr(agents, "_auto_refresh_dashboard_snapshot", fake_refresh)

    response = await agents.get_document_analysis_dashboard_latest(None, None)

    assert response["snapshot"]["dashboard_date_msk"] == agents.today_msk_iso()


@pytest.mark.asyncio
async def test_auto_refresh_missing_inputs_marks_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot = {
        "dashboard_date_msk": "2026-08-11",
        "logistics_risks": {"as_of": None, "stages": []},
    }

    def fake_update(user_id, **kwargs):
        return {**snapshot, **kwargs}

    monkeypatch.setattr(agents, "_restore_dashboard_refresh_inputs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(agents, "update_dashboard_refresh_state", fake_update)

    refreshed = await agents._auto_refresh_dashboard_snapshot(
        user_id="user-1",
        db=None,
        snapshot=snapshot,
    )

    assert refreshed["refresh_status"] == "missing_inputs"
    assert "входных файлов" in refreshed["refresh_error"]


@pytest.mark.asyncio
async def test_auto_refresh_google_error_marks_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot = {
        "dashboard_date_msk": "2026-08-11",
        "refresh_inputs": _refresh_inputs(),
        "logistics_risks": {"as_of": None, "stages": []},
    }

    async def fake_prepare(uploaded, db):
        raise RuntimeError("Google Sheets unavailable")

    def fake_update(user_id, **kwargs):
        return {**snapshot, **kwargs}

    monkeypatch.setattr(agents, "_prepare_aveon_uploaded_with_server_shipment", fake_prepare)
    monkeypatch.setattr(agents, "load_dashboard_snapshot", lambda user_id: snapshot)
    monkeypatch.setattr(agents, "update_dashboard_refresh_state", fake_update)
    monkeypatch.setattr(agents, "_auto_refresh_inputs_error", lambda uploaded: None)

    refreshed = await agents._auto_refresh_dashboard_snapshot(
        user_id="user-1",
        db=None,
        snapshot=snapshot,
    )

    assert refreshed["refresh_status"] == "error"
    assert refreshed["refresh_error"] == "Google Sheets unavailable"


def test_restore_dashboard_refresh_inputs_merges_snapshot_and_schedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        agents,
        "_restore_dashboard_refresh_inputs_from_schedule_snapshot",
        lambda user_id: [UploadedWorkbook(filename="detailed.xlsx", content=b"detailed")],
    )

    restored = agents._restore_dashboard_refresh_inputs(
        {
            "refresh_inputs": {
                "version": 1,
                "files": [
                    {
                        "file_name": "production.xlsx",
                        "file_base64": base64.b64encode(b"production").decode("ascii"),
                    }
                ],
            }
        },
        "user-1",
    )

    assert restored is not None
    assert {item.filename for item in restored} == {"production.xlsx", "detailed.xlsx"}


@pytest.mark.asyncio
async def test_auto_refresh_requires_detailed_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot = {
        "dashboard_date_msk": "2026-08-12",
        "logistics_risks": {"as_of": "2026-08-12", "stages": []},
        "coverage_dashboard": {"periods": {"day": {"products": {"tiles": {"all": 0}}}}},
    }

    async def fake_inputs_error(uploaded):
        return "need detailed"

    def fake_update(user_id, **kwargs):
        return {**snapshot, **kwargs}

    monkeypatch.setattr(
        agents,
        "_restore_dashboard_refresh_inputs",
        lambda *_args, **_kwargs: [
            UploadedWorkbook(filename="production.xlsx", content=b"production")
        ],
    )
    monkeypatch.setattr(agents, "_auto_refresh_inputs_error", fake_inputs_error)
    monkeypatch.setattr(agents, "update_dashboard_refresh_state", fake_update)

    refreshed = await agents._auto_refresh_dashboard_snapshot(
        user_id="user-1",
        db=None,
        snapshot=snapshot,
    )

    assert refreshed["refresh_status"] == "missing_detailed_schedule"
    assert refreshed["refresh_error"] == "need detailed"
