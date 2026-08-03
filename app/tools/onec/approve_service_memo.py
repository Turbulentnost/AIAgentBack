"""
Согласование служебной записки в 1С:ERP (OData).

Для СЗ по совещаниям переводит Статус из «НеСогласована» в «Согласована»
через PATCH Document_ТД_СлужебнаяЗаписка.

Автосогласование выполняется только если все условия СТО выполнены.
При невыполненных условиях инструмент возвращает рекомендацию для сотрудника УД
без изменения документа.

CLI:
  python -m app.tools.onec.approve_service_memo --number 000010430
  python -m app.tools.onec.approve_service_memo --ref-key 8f87f484-7398-11f1-9831-6cb31113810c
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import requests

from app.agents.meeting_agent.memo_validation import (
    AUTO_APPROVE_SERVICE_MEMO,
    MemoValidationIssue,
    assess_sto_readiness,
)
from app.tools.onec.connection import CONFIG, ODataConfig
from app.tools.onec.get_meetings import fetch_document_header
from app.tools.onec.service_memo_shared import (
    APPROVED_STATUS,
    UNAPPROVED_STATUS,
    ServiceMemoWorkflowError,
    apply_executor_fields,
    ensure_meeting_memo,
    load_memo_header,
    now_ud_timestamp,
    patch_service_memo,
    resolve_memo_ref_key,
)

ServiceMemoApprovalError = ServiceMemoWorkflowError
patch_service_memo_status = patch_service_memo


def build_approval_patch(
    session,
    config: ODataConfig,
    *,
    approver_fio: str | None,
    comment: str | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "Статус": APPROVED_STATUS,
        "ДатаИсполненияУД": now_ud_timestamp(),
    }
    apply_executor_fields(session, config, payload, executor_fio=approver_fio)
    if comment is not None and comment.strip():
        payload["Комментарий"] = comment.strip()
    return payload


def _sto_document(header: dict[str, Any]) -> dict[str, Any]:
    return {"memo": header, "header": header}


def build_approval_result_message(
    *,
    number: str | None,
    changed: bool,
    already_approved: bool,
    sto_ready: bool,
    perform_approval: bool,
) -> str:
    memo_number = (number or "").strip() or "?"
    if already_approved:
        return f"Служебная записка №{memo_number} уже согласована."
    if changed:
        return f"Служебная записка №{memo_number} согласована."
    if perform_approval and not sto_ready:
        return f"Служебная записка №{memo_number} не согласована: не выполнены условия СТО."
    if not sto_ready:
        return (
            f"Служебная записка №{memo_number} не согласована автоматически: "
            "не выполнены условия СТО."
        )
    return f"Служебная записка №{memo_number} готова к согласованию сотрудником УД."


def _base_result(
    *,
    resolved_ref: str,
    header: dict[str, Any],
    previous_status: str,
    sto_assessment: dict[str, Any],
) -> dict[str, Any]:
    return {
        "ref_key": resolved_ref,
        "number": header.get("Number"),
        "date": header.get("Date"),
        "posted": header.get("Posted"),
        "status": header.get("Статус"),
        "previous_status": previous_status,
        "sto_ready": sto_assessment["sto_ready"],
        "sto_issues": sto_assessment["sto_issues"],
        "ud_recommendation": sto_assessment["ud_recommendation"],
        "auto_approve_allowed": sto_assessment["auto_approve_allowed"],
    }


def approve_service_memo(
    *,
    ref_key: str | None = None,
    number: str | None = None,
    approver_fio: str | None = None,
    comment: str | None = None,
    require_unapproved: bool = True,
    validate_meeting_theme: bool = True,
    check_sto: bool = True,
    perform_approval: bool = False,
    config: ODataConfig = CONFIG,
) -> dict[str, Any]:
    session, resolved_ref, before, metadata = load_memo_header(
        ref_key=ref_key,
        number=number,
        config=config,
    )

    if validate_meeting_theme:
        ensure_meeting_memo(session, config, before, metadata=metadata)

    previous_status = str(before.get("Статус") or "")
    memo_number = str(before.get("Number") or number or "")
    sto_assessment = (
        assess_sto_readiness(_sto_document(before))
        if check_sto
        else {
            "sto_ready": True,
            "sto_issues": [],
            "ud_recommendation": None,
            "auto_approve_allowed": True,
        }
    )

    def _unchanged_result(*, auto_approved: bool = False) -> dict[str, Any]:
        return {
            **_base_result(
                resolved_ref=resolved_ref,
                header=before,
                previous_status=previous_status,
                sto_assessment=sto_assessment,
            ),
            "already_approved": previous_status == APPROVED_STATUS,
            "changed": False,
            "auto_approved": auto_approved,
            "approver_fio": approver_fio,
            "comment": comment,
            "message": build_approval_result_message(
                number=memo_number,
                changed=False,
                already_approved=previous_status == APPROVED_STATUS,
                sto_ready=bool(sto_assessment["sto_ready"]),
                perform_approval=perform_approval,
            ),
        }

    if previous_status == APPROVED_STATUS:
        return _unchanged_result()

    if require_unapproved and previous_status != UNAPPROVED_STATUS:
        raise ServiceMemoApprovalError(
            f"Согласование недоступно: текущий статус «{previous_status or 'не указан'}»"
        )

    should_patch = perform_approval or (
        AUTO_APPROVE_SERVICE_MEMO
        and (not check_sto or sto_assessment["sto_ready"])
    )
    if not should_patch:
        return _unchanged_result()

    patch_payload = build_approval_patch(
        session,
        config,
        approver_fio=approver_fio,
        comment=comment,
    )
    patch_service_memo(
        session,
        config,
        resolved_ref,
        patch_payload,
        action_label="согласования",
    )
    after = fetch_document_header(session, config, resolved_ref)

    return {
        **_base_result(
            resolved_ref=resolved_ref,
            header=after,
            previous_status=previous_status,
            sto_assessment=sto_assessment,
        ),
        "already_approved": False,
        "changed": True,
        "auto_approved": not perform_approval,
        "approver_fio": approver_fio,
        "comment": comment,
        "message": build_approval_result_message(
            number=str(after.get("Number") or memo_number),
            changed=True,
            already_approved=False,
            sto_ready=bool(sto_assessment["sto_ready"]),
            perform_approval=perform_approval,
        ),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Согласовать служебную записку по совещанию в 1С:ERP (OData).",
    )
    parser.add_argument("--ref-key", help="Ref_Key документа")
    parser.add_argument("--number", help="Номер документа, например 000010430")
    parser.add_argument(
        "--approver-fio",
        help="ФИО согласующего (Catalog_Пользователи) для поля ИсполнительУД",
    )
    parser.add_argument("--comment", help="Комментарий к согласованию")
    parser.add_argument(
        "--allow-any-status",
        action="store_true",
        help="Разрешить согласование при статусе, отличном от «НеСогласована»",
    )
    parser.add_argument(
        "--skip-theme-check",
        action="store_true",
        help="Не проверять тему «Организация совещаний»",
    )
    parser.add_argument(
        "--skip-sto-check",
        action="store_true",
        help="Не проверять условия СТО (согласовать без автопроверки)",
    )
    parser.add_argument(
        "--perform-approval",
        action="store_true",
        help="Выполнить PATCH в 1С (ручное согласование УД, без проверки AUTO_APPROVE)",
    )
    parser.add_argument("-o", "--output", help="Путь к JSON-файлу результата")
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = build_parser().parse_args(argv)
    try:
        result = approve_service_memo(
            ref_key=args.ref_key,
            number=args.number,
            approver_fio=args.approver_fio,
            comment=args.comment,
            require_unapproved=not args.allow_any_status,
            validate_meeting_theme=not args.skip_theme_check,
            check_sto=not args.skip_sto_check,
            perform_approval=args.perform_approval,
        )
    except (ServiceMemoApprovalError, RuntimeError, requests.RequestException) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1

    text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as file:
            file.write(text)
        print(f"Сохранено: {args.output}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
