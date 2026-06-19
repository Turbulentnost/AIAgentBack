from __future__ import annotations

import re
from dataclasses import dataclass

from app.eskd.designation import (
    ASSEMBLY_SUFFIX_RE,
    ELECTRIC_SUFFIX_RE,
    ESKD_DESIGNATION_CHARS_RE,
    ESKD_STANDARD_DESIGNATION_RE,
    normalize_designation,
    parse_designation,
)
from app.eskd.validation.schemas import EskdCheckResult
from app.models.enums import DocumentType, EskdDocumentKind, TextExtractStatus

MAIN_INSCRIPTION_KEYWORDS = (
    "обозначение",
    "наименование",
    "лист",
    "листов",
    "масштаб",
    "разработ",
    "провер",
    "утвержд",
)

DRAWING_SCALE_RE = re.compile(r"масштаб\s*[:\-]?\s*(\d+\s*:\s*\d+|б/м|б\.?\s*м\.?)", re.IGNORECASE)
SHEET_RE = re.compile(r"лист\s*\d+\s*/?\s*\d*", re.IGNORECASE)


@dataclass
class EskdValidationContext:
    designation: str | None
    document_kind: EskdDocumentKind
    document_title: str | None
    original_filename: str | None
    document_type: DocumentType | None
    text_extract_status: TextExtractStatus | None
    document_text: str
    qms_document_code: str | None
    owner_department: str | None


def run_all_checks(context: EskdValidationContext) -> list[EskdCheckResult]:
    return [
        check_document_type_kd(context),
        check_designation_present(context),
        check_designation_characters(context),
        check_designation_gost201_format(context),
        check_designation_filename_consistency(context),
        check_document_kind_designation_consistency(context),
        check_qms_card_code_consistency(context),
        check_text_extracted(context),
        check_designation_in_content(context),
        check_title_in_content(context),
        check_main_inscription(context),
        check_drawing_scale(context),
        check_specification_markers(context),
    ]


def check_document_type_kd(context: EskdValidationContext) -> EskdCheckResult:
    passed = context.document_type == DocumentType.KD
    return EskdCheckResult(
        code="document_type_kd",
        title="Тип документа — конструкторская документация",
        passed=passed,
        severity="error",
        message="Документ зарегистрирован как КД (конструкторская документация)."
        if passed
        else f"Тип документа «{context.document_type}» не является КД.",
        gost_reference="ЕСКД / внутренний классификатор КД",
    )


def check_designation_present(context: EskdValidationContext) -> EskdCheckResult:
    passed = bool(normalize_designation(context.designation))
    return EskdCheckResult(
        code="designation_present",
        title="Наличие обозначения документа",
        passed=passed,
        severity="error",
        message="Обозначение документа указано."
        if passed
        else "Обозначение документа не задано — обязательный реквизит по ЕСКД.",
        gost_reference="ГОСТ 2.201",
    )


def check_designation_characters(context: EskdValidationContext) -> EskdCheckResult:
    designation = normalize_designation(context.designation)
    if not designation:
        return EskdCheckResult(
            code="designation_characters",
            title="Допустимые символы обозначения",
            passed=False,
            severity="error",
            message="Нет обозначения для проверки символов.",
            gost_reference="ГОСТ 2.201",
        )
    passed = bool(ESKD_DESIGNATION_CHARS_RE.match(designation))
    return EskdCheckResult(
        code="designation_characters",
        title="Допустимые символы обозначения",
        passed=passed,
        severity="error",
        message="Состав обозначения допустим."
        if passed
        else "Обозначение содержит недопустимые символы (разрешены буквы, цифры, «.», «-», «/»).",
        gost_reference="ГОСТ 2.201",
        details={"designation": designation},
    )


def check_designation_gost201_format(context: EskdValidationContext) -> EskdCheckResult:
    designation = normalize_designation(context.designation)
    if not designation:
        return EskdCheckResult(
            code="designation_gost201",
            title="Формат обозначения по ГОСТ 2.201",
            passed=False,
            severity="error",
            message="Нет обозначения для проверки формата.",
            gost_reference="ГОСТ 2.201",
        )
    parsed = parse_designation(designation)
    passed = parsed is not None
    return EskdCheckResult(
        code="designation_gost201",
        title="Формат обозначения по ГОСТ 2.201",
        passed=passed,
        severity="error",
        message="Обозначение соответствует структуре «код_организации.номер[.лист][суффикс]»."
        if passed
        else (
            "Обозначение не соответствует структуре ГОСТ 2.201 "
            "(ожидается, например: ABVG.123456.001 или ABVG.123456.001СБ)."
        ),
        gost_reference="ГОСТ 2.201",
        details={"parsed": parsed or {}, "designation": designation},
    )


def check_designation_filename_consistency(context: EskdValidationContext) -> EskdCheckResult:
    designation = normalize_designation(context.designation)
    filename = (context.original_filename or "").rsplit(".", 1)[0].strip().upper()
    if not designation or not filename:
        return EskdCheckResult(
            code="designation_filename",
            title="Согласованность обозначения и имени файла",
            passed=True,
            severity="info",
            message="Недостаточно данных для сравнения обозначения с именем файла.",
            gost_reference="ГОСТ 2.201",
        )
    passed = designation == filename or designation in filename or filename in designation
    return EskdCheckResult(
        code="designation_filename",
        title="Согласованность обозначения и имени файла",
        passed=passed,
        severity="warning",
        message="Обозначение согласовано с именем файла."
        if passed
        else f"Обозначение «{designation}» не совпадает с именем файла «{filename}».",
        gost_reference="ГОСТ 2.201",
    )


def check_document_kind_designation_consistency(context: EskdValidationContext) -> EskdCheckResult:
    designation = normalize_designation(context.designation) or ""
    kind = context.document_kind
    title_lower = (context.document_title or "").lower()

    if kind == EskdDocumentKind.ASSEMBLY_DRAWING:
        passed = bool(ASSEMBLY_SUFFIX_RE.search(designation)) or "сбороч" in title_lower
        message = (
            "Сборочный чертёж: обозначение или наименование содержит признак сборочного документа."
            if passed
            else "Для сборочного чертежа ожидается суффикс «СБ» в обозначении или «сбороч» в наименовании."
        )
    elif kind == EskdDocumentKind.ELECTRIC_SCHEME:
        passed = bool(ELECTRIC_SUFFIX_RE.search(designation)) or "элект" in title_lower or "схем" in title_lower
        message = (
            "Электрическая схема: обозначение или наименование содержит признак схемы."
            if passed
            else "Для электрической схемы ожидается суффикс «Э1»…«Э9» или указание схемы в наименовании."
        )
    elif kind == EskdDocumentKind.SPECIFICATION:
        passed = "специфика" in title_lower or "специфика" in designation.lower()
        message = (
            "Спецификация: наименование содержит признак спецификации."
            if passed
            else "Для спецификации ожидается слово «спецификация» в наименовании документа."
        )
    elif kind == EskdDocumentKind.DRAWING:
        parsed = parse_designation(designation)
        has_sb = bool(parsed and parsed.get("suffix") == "СБ")
        passed = not has_sb
        message = (
            "Чертёж детали: обозначение не содержит сборочный суффикс «СБ»."
            if passed
            else "Чертёж детали не должен иметь сборочный суффикс «СБ» — используйте тип «assembly_drawing»."
        )
    else:
        return EskdCheckResult(
            code="document_kind_consistency",
            title="Согласованность вида документа и обозначения",
            passed=True,
            severity="info",
            message="Вид документа «other/text_document» — расширенная проверка суффикса не выполняется.",
            gost_reference="ГОСТ 2.102 / ГОСТ 2.201",
        )

    return EskdCheckResult(
        code="document_kind_consistency",
        title="Согласованность вида документа и обозначения",
        passed=passed,
        severity="warning" if kind != EskdDocumentKind.DRAWING else "error",
        message=message,
        gost_reference="ГОСТ 2.102 / ГОСТ 2.201",
        details={"document_kind": kind.value, "designation": designation},
    )


def check_qms_card_code_consistency(context: EskdValidationContext) -> EskdCheckResult:
    designation = normalize_designation(context.designation)
    card_code = normalize_designation(context.qms_document_code)
    if not designation or not card_code:
        return EskdCheckResult(
            code="qms_card_code",
            title="Согласованность карточки документа и обозначения",
            passed=True,
            severity="info",
            message="Карточка документа или обозначение отсутствуют — проверка пропущена.",
        )
    passed = designation == card_code
    return EskdCheckResult(
        code="qms_card_code",
        title="Согласованность карточки документа и обозначения",
        passed=passed,
        severity="warning",
        message="Код в карточке документа совпадает с обозначением ЕСКД."
        if passed
        else f"Код карточки «{card_code}» не совпадает с обозначением «{designation}».",
    )


def check_text_extracted(context: EskdValidationContext) -> EskdCheckResult:
    has_text = bool(context.document_text.strip())
    status_ok = context.text_extract_status == TextExtractStatus.EXTRACTED
    passed = has_text or status_ok
    return EskdCheckResult(
        code="text_extracted",
        title="Извлечённый текст документа",
        passed=passed,
        severity="warning" if not passed else "info",
        message="Текст документа доступен для проверки основной надписи."
        if passed
        else "Текст не извлечён — запустите обработку документа (process_document) и повторите проверку.",
        gost_reference="ГОСТ 2.104",
    )


def check_designation_in_content(context: EskdValidationContext) -> EskdCheckResult:
    designation = normalize_designation(context.designation)
    text = context.document_text
    if not designation:
        return EskdCheckResult(
            code="designation_in_content",
            title="Обозначение в содержимом документа",
            passed=False,
            severity="error",
            message="Нет обозначения для поиска в тексте.",
            gost_reference="ГОСТ 2.104",
        )
    if not text.strip():
        return EskdCheckResult(
            code="designation_in_content",
            title="Обозначение в содержимом документа",
            passed=False,
            severity="warning",
            message="Текст недоступен — невозможно подтвердить обозначение в основной надписи.",
            gost_reference="ГОСТ 2.104",
        )
    passed = designation in text.upper()
    return EskdCheckResult(
        code="designation_in_content",
        title="Обозначение в содержимом документа",
        passed=passed,
        severity="error",
        message="Обозначение найдено в тексте документа (основная надпись)."
        if passed
        else f"Обозначение «{designation}» не найдено в тексте документа.",
        gost_reference="ГОСТ 2.104",
    )


def check_title_in_content(context: EskdValidationContext) -> EskdCheckResult:
    title = (context.document_title or "").strip()
    text = context.document_text
    if not title or not text.strip():
        return EskdCheckResult(
            code="title_in_content",
            title="Наименование в содержимом документа",
            passed=True,
            severity="info",
            message="Недостаточно данных для проверки наименования в тексте.",
            gost_reference="ГОСТ 2.104",
        )
    normalized_title = re.sub(r"\s+", " ", title.lower())
    normalized_text = re.sub(r"\s+", " ", text.lower())
    passed = normalized_title[:40] in normalized_text
    return EskdCheckResult(
        code="title_in_content",
        title="Наименование в содержимом документа",
        passed=passed,
        severity="warning",
        message="Наименование документа найдено в тексте."
        if passed
        else "Наименование документа не найдено в извлечённом тексте.",
        gost_reference="ГОСТ 2.104",
    )


def check_main_inscription(context: EskdValidationContext) -> EskdCheckResult:
    text = context.document_text.lower()
    if not text.strip():
        return EskdCheckResult(
            code="main_inscription",
            title="Основная надпись (реквизиты)",
            passed=False,
            severity="warning",
            message="Текст недоступен — проверка основной надписи по ГОСТ 2.104 невозможна.",
            gost_reference="ГОСТ 2.104",
        )
    found = [keyword for keyword in MAIN_INSCRIPTION_KEYWORDS if keyword in text]
    required = {"обозначение", "наименование"}
    passed = required.issubset(set(found))
    return EskdCheckResult(
        code="main_inscription",
        title="Основная надпись (реквизиты)",
        passed=passed,
        severity="error" if not passed else "info",
        message="В тексте найдены обязательные реквизиты основной надписи (обозначение, наименование)."
        if passed
        else "В тексте не найдены обязательные реквизиты основной надписи: «обозначение» и «наименование».",
        gost_reference="ГОСТ 2.104",
        details={"found_keywords": found},
    )


def check_drawing_scale(context: EskdValidationContext) -> EskdCheckResult:
    if context.document_kind not in {
        EskdDocumentKind.DRAWING,
        EskdDocumentKind.ASSEMBLY_DRAWING,
        EskdDocumentKind.ELECTRIC_SCHEME,
    }:
        return EskdCheckResult(
            code="drawing_scale",
            title="Масштаб чертежа",
            passed=True,
            severity="info",
            message="Проверка масштаба не требуется для выбранного вида документа.",
            gost_reference="ГОСТ 2.302",
        )
    text = context.document_text
    if not text.strip():
        return EskdCheckResult(
            code="drawing_scale",
            title="Масштаб чертежа",
            passed=False,
            severity="warning",
            message="Текст недоступен — масштаб не проверен.",
            gost_reference="ГОСТ 2.302",
        )
    has_scale_word = "масштаб" in text.lower()
    has_scale_value = bool(DRAWING_SCALE_RE.search(text))
    passed = has_scale_word or has_scale_value
    return EskdCheckResult(
        code="drawing_scale",
        title="Масштаб чертежа",
        passed=passed,
        severity="warning",
        message="Масштаб указан в документе." if passed else "Масштаб не найден в тексте чертежа.",
        gost_reference="ГОСТ 2.302",
    )


def check_specification_markers(context: EskdValidationContext) -> EskdCheckResult:
    if context.document_kind != EskdDocumentKind.SPECIFICATION:
        return EskdCheckResult(
            code="specification_markers",
            title="Признаки спецификации",
            passed=True,
            severity="info",
            message="Документ не является спецификацией — проверка пропущена.",
            gost_reference="ГОСТ 2.106",
        )
    text = context.document_text.lower()
    if not text.strip():
        return EskdCheckResult(
            code="specification_markers",
            title="Признаки спецификации",
            passed=False,
            severity="warning",
            message="Текст недоступен — структура спецификации не проверена.",
            gost_reference="ГОСТ 2.106",
        )
    markers = ("поз", "обозначение", "наименование", "кол", "примеч")
    found = [item for item in markers if item in text]
    passed = len(found) >= 3
    return EskdCheckResult(
        code="specification_markers",
        title="Признаки спецификации",
        passed=passed,
        severity="warning",
        message="Обнаружены типовые графы спецификации."
        if passed
        else "В тексте мало признаков табличной спецификации (поз., обозначение, наименование, кол.).",
        gost_reference="ГОСТ 2.106",
        details={"found_markers": found},
    )
