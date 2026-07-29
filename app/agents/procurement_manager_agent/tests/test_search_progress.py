"""Unit tests for in-memory supplier-search progress buffer."""

from __future__ import annotations

from app.agents.procurement_manager_agent.search_progress import (
    clear_progress,
    emit_progress,
    finish_progress,
    get_progress,
    get_progress_meta,
    progress_domain,
    progress_scope,
)


def test_progress_scope_emits_and_scopes_by_operation() -> None:
    op = "supplier-search-manual-test-1"
    clear_progress(op)
    with progress_scope(op, case_id="case-a"):
        emit_progress("Ищу в интернете: ремень")
        emit_progress("Нашёл 5 ссылок")
        emit_progress("Qwen выбирает сайты для проверки…")
    finish_progress(op, status="completed")
    lines = get_progress(op)
    assert lines[0].startswith("Ищу в интернете")
    assert "Нашёл 5 ссылок" in lines
    assert any("Qwen выбирает" in line for line in lines)
    meta = get_progress_meta(op)
    assert meta is not None
    assert meta["case_id"] == "case-a"
    assert meta["status"] == "completed"
    clear_progress(op)


def test_emit_without_scope_is_noop() -> None:
    before = get_progress("missing-op")
    emit_progress("не должно сохраниться")
    assert get_progress("missing-op") == before


def test_progress_domain_strips_www() -> None:
    assert progress_domain("https://www.example.com/path") == "example.com"
    assert progress_domain("") == "сайт"


def test_supplier_search_progress_case_mismatch() -> None:
    from app.agents.procurement_manager_agent.service import ProcurementManagerService

    op = "supplier-search-manual-test-2"
    clear_progress(op)
    with progress_scope(op, case_id="case-a"):
        emit_progress("Открываю example.com…")
    payload = ProcurementManagerService.supplier_search_progress(
        case_id="case-b",
        operation_id=op,
    )
    assert payload["thoughts"] == []
    assert payload["status"] == "unknown"
    ok = ProcurementManagerService.supplier_search_progress(
        case_id="case-a",
        operation_id=op,
    )
    assert ok["thoughts"]
    clear_progress(op)
