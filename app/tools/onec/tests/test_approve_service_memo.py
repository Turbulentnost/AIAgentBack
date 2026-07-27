from unittest.mock import MagicMock, patch

import pytest

from app.tools.onec.approve_service_memo import (
    APPROVED_STATUS,
    UNAPPROVED_STATUS,
    ServiceMemoApprovalError,
    approve_service_memo,
    build_approval_patch,
    ensure_meeting_memo,
    resolve_memo_ref_key,
)


def test_resolve_memo_ref_key_requires_identifier() -> None:
    with pytest.raises(ServiceMemoApprovalError, match="ref_key или number"):
        resolve_memo_ref_key(MagicMock(), MagicMock(), ref_key=None, number=None)


def test_resolve_memo_ref_key_by_number() -> None:
    session = MagicMock()
    config = MagicMock()
    with patch(
        "app.tools.onec.approve_service_memo.fetch_meeting_memo_rows",
        return_value=[{"Ref_Key": "abc-123"}],
    ):
        ref = resolve_memo_ref_key(session, config, ref_key=None, number="000010430")
    assert ref == "abc-123"


def test_ensure_meeting_memo_rejects_wrong_theme() -> None:
    session = MagicMock()
    config = MagicMock()
    with patch(
        "app.tools.onec.approve_service_memo.theme_matches",
        return_value=False,
    ):
        with pytest.raises(ServiceMemoApprovalError, match="не относится к теме"):
            ensure_meeting_memo(session, config, {"Ref_Key": "x"}, metadata=MagicMock())


def test_build_approval_patch_sets_status_and_executor() -> None:
    session = MagicMock()
    config = MagicMock()
    with patch(
        "app.tools.onec.approve_service_memo.resolve_user_by_fio",
        return_value=("user-ref", "Иванов И. И.", None),
    ):
        payload = build_approval_patch(
            session,
            config,
            approver_fio="Иванов И. И.",
            comment="Ок",
        )
    assert payload["Статус"] == APPROVED_STATUS
    assert payload["ИсполнительУД_Key"] == "user-ref"
    assert payload["Комментарий"] == "Ок"
    assert "ДатаИсполненияУД" in payload


def test_approve_service_memo_idempotent_when_already_approved() -> None:
    session = MagicMock()
    config = MagicMock()
    header = {
        "Ref_Key": "abc",
        "Number": "000010430",
        "Date": "2026-06-29T11:57:31",
        "Posted": True,
        "Статус": APPROVED_STATUS,
    }
    with patch("app.tools.onec.approve_service_memo.create_session", return_value=session):
        with patch("app.tools.onec.approve_service_memo.load_metadata_xml", return_value=MagicMock()):
            with patch(
                "app.tools.onec.approve_service_memo.resolve_memo_ref_key",
                return_value="abc",
            ):
                with patch(
                    "app.tools.onec.approve_service_memo.fetch_document_header",
                    return_value=header,
                ):
                    with patch(
                        "app.tools.onec.approve_service_memo.ensure_meeting_memo",
                    ):
                        with patch(
                            "app.tools.onec.approve_service_memo.patch_service_memo_status",
                        ) as patch_status:
                            result = approve_service_memo(number="000010430", config=config)

    patch_status.assert_not_called()
    assert result["already_approved"] is True
    assert result["changed"] is False
    assert result["status"] == APPROVED_STATUS


def test_approve_service_memo_patches_unapproved() -> None:
    session = MagicMock()
    config = MagicMock()
    before = {
        "Ref_Key": "abc",
        "Number": "000010430",
        "Date": "2026-06-29T11:57:31",
        "Posted": True,
        "Статус": UNAPPROVED_STATUS,
    }
    after = {**before, "Статус": APPROVED_STATUS}
    with patch("app.tools.onec.approve_service_memo.create_session", return_value=session):
        with patch("app.tools.onec.approve_service_memo.load_metadata_xml", return_value=MagicMock()):
            with patch(
                "app.tools.onec.approve_service_memo.resolve_memo_ref_key",
                return_value="abc",
            ):
                with patch(
                    "app.tools.onec.approve_service_memo.fetch_document_header",
                    side_effect=[before, after],
                ):
                    with patch(
                        "app.tools.onec.approve_service_memo.ensure_meeting_memo",
                    ):
                        with patch(
                            "app.tools.onec.approve_service_memo.build_approval_patch",
                            return_value={"Статус": APPROVED_STATUS},
                        ) as build_patch:
                            with patch(
                                "app.tools.onec.approve_service_memo.patch_service_memo_status",
                                return_value=after,
                            ) as patch_status:
                                result = approve_service_memo(
                                    number="000010430",
                                    approver_fio="Комарькова Анастасия Эдуардовна",
                                    config=config,
                                )

    build_patch.assert_called_once()
    patch_status.assert_called_once()
    assert result["changed"] is True
    assert result["status"] == APPROVED_STATUS
    assert result["previous_status"] == UNAPPROVED_STATUS


def test_approve_service_memo_rejects_unexpected_status() -> None:
    session = MagicMock()
    config = MagicMock()
    before = {"Ref_Key": "abc", "Number": "000010430", "Статус": "Отменена"}
    with patch("app.tools.onec.approve_service_memo.create_session", return_value=session):
        with patch("app.tools.onec.approve_service_memo.load_metadata_xml", return_value=MagicMock()):
            with patch(
                "app.tools.onec.approve_service_memo.resolve_memo_ref_key",
                return_value="abc",
            ):
                with patch(
                    "app.tools.onec.approve_service_memo.fetch_document_header",
                    return_value=before,
                ):
                    with patch(
                        "app.tools.onec.approve_service_memo.ensure_meeting_memo",
                    ):
                        with pytest.raises(ServiceMemoApprovalError, match="Отменена"):
                            approve_service_memo(number="000010430", config=config)
