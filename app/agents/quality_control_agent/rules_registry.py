"""Deterministic Rule Registry (Прил. В) — versioned dict, not LLM-only."""

from __future__ import annotations

from typing import Any

from app.agents.quality_control_agent.schemas import (
    QualityDocumentRequirement,
    QualityFinding,
    QualitySampleRule,
)

RULES_VERSION = "1.0.0-sto-10-095"

# Mandatory documents by ТМЦ category (Прил. В.3 MVP subset).
CATEGORY_DOCUMENTS: dict[str, list[dict[str, Any]]] = {
    "electronics": [
        {"doc_type": "origin_confirmation", "label": "Подтверждение происхождения от официального поставщика"},
        {"doc_type": "certificate", "label": "Сертификат"},
        {"doc_type": "applicability", "label": "Сведения о применимости"},
    ],
    "metal": [
        {"doc_type": "certificate_or_passport", "label": "Сертификат или паспорт качества"},
    ],
    "fasteners": [
        {"doc_type": "certificate_or_passport", "label": "Сертификат или паспорт качества"},
    ],
    "cable": [
        {"doc_type": "passport", "label": "Паспорт"},
        {"doc_type": "marking", "label": "Маркировка / сечение / экраны"},
    ],
    "pipes": [
        {"doc_type": "certificate_or_passport", "label": "Сертификат или паспорт качества"},
    ],
    "flanges": [
        {"doc_type": "certificate_or_passport", "label": "Сертификат или паспорт качества"},
    ],
    "gaskets": [
        {"doc_type": "certificate_or_passport", "label": "Сертификат или паспорт качества"},
    ],
    "drawing_parts": [
        {"doc_type": "certificate_or_passport", "label": "Сертификат или паспорт качества"},
        {"doc_type": "kd_match", "label": "Соответствие КД"},
    ],
    "other": [
        {"doc_type": "certificate_or_passport", "label": "Сертификат или паспорт качества"},
    ],
}

# Industrial control deadlines in working days from presentation (Прил. В.2).
CATEGORY_DEADLINES_WD: dict[str, int] = {
    "metal": 2,
    "pipes": 1,
    "flanges": 1,
    "fasteners": 2,
    "gaskets": 2,
    "drawing_parts": 2,
    "cable": 2,
    "electronics": 3,
    "other": 3,
}

SCRAP_THRESHOLD_PCT = 15.0


def normalize_category(raw: str | None) -> str:
    if not raw:
        return "other"
    key = str(raw).strip().casefold().replace("ё", "е")
    aliases = {
        "электроника": "electronics",
        "радио": "electronics",
        "electronics": "electronics",
        "металл": "metal",
        "metal": "metal",
        "крепеж": "fasteners",
        "fasteners": "fasteners",
        "кабель": "cable",
        "cable": "cable",
        "трубы": "pipes",
        "pipes": "pipes",
        "фланцы": "flanges",
        "flanges": "flanges",
        "прокладки": "gaskets",
        "gaskets": "gaskets",
        "чертежные": "drawing_parts",
        "drawing_parts": "drawing_parts",
    }
    for marker, category in aliases.items():
        if marker in key:
            return category
    if key in CATEGORY_DOCUMENTS:
        return key
    return "other"


def build_mandatory_documents(
    category: str | None,
    present_docs: list[str] | None = None,
) -> list[QualityDocumentRequirement]:
    cat = normalize_category(category)
    present = {str(d).strip().casefold() for d in (present_docs or [])}
    result: list[QualityDocumentRequirement] = []
    for spec in CATEGORY_DOCUMENTS.get(cat, CATEGORY_DOCUMENTS["other"]):
        doc_type = str(spec["doc_type"])
        result.append(
            QualityDocumentRequirement(
                doc_type=doc_type,
                label=str(spec["label"]),
                mandatory=True,
                present=doc_type.casefold() in present
                or any(doc_type.casefold() in p or p in doc_type.casefold() for p in present),
            )
        )
    return result


def evaluate_document_completeness(
    category: str | None,
    present_docs: list[str] | None,
    record_id: str,
) -> list[QualityFinding]:
    docs = build_mandatory_documents(category, present_docs)
    findings: list[QualityFinding] = []
    for doc in docs:
        if doc.present:
            continue
        findings.append(
            QualityFinding(
                field=doc.doc_type,
                rule_id=f"QC.DOC.{normalize_category(category).upper()}.{doc.doc_type.upper()}",
                source_ref=f"presentation:{record_id}",
                message=f"Отсутствует обязательный документ: {doc.label}",
                severity="critical",
                suggested_fix=f"Приложить «{doc.label}»",
                current_value=None,
            )
        )
    return findings


def build_sample_rule(
    category: str | None,
    *,
    lot_qty: float | int | None = None,
    analog_in_nomenclature: bool | None = True,
) -> QualitySampleRule:
    cat = normalize_category(category)
    note_parts = [f"Правила выборки для группы «{cat}» (Прил. В)."]
    sample_size: int | None = None
    if cat == "fasteners":
        note_parts.append("Крепёж: выборка из каждой тары.")
        sample_size = None
    if analog_in_nomenclature is False:
        note_parts.append("Аналог отсутствует в номенклатуре → партия в брак.")
    if lot_qty is not None:
        try:
            qty = float(lot_qty)
            if qty > 0 and cat != "fasteners":
                sample_size = max(1, int(round(qty * 0.1)))
                note_parts.append(f"Базовая выборка ≈ 10% партии ({sample_size} шт.).")
        except (TypeError, ValueError):
            pass
    return QualitySampleRule(
        rule_id=f"QC.SAMPLE.{cat.upper()}",
        category=cat,
        sample_size=sample_size,
        sample_note=" ".join(note_parts),
        scrap_threshold_pct=SCRAP_THRESHOLD_PCT,
    )


def evaluate_scrap_decision(
    scrap_pct: float | None,
    *,
    analog_in_nomenclature: bool | None = True,
) -> dict[str, Any]:
    """Return disposition recommendation from scrap rules."""
    if analog_in_nomenclature is False:
        return {
            "disposition": "forbid",
            "rule_id": "QC.SCRAP.NO_ANALOG",
            "message": "Аналог отсутствует в номенклатуре — партия в брак.",
            "require_second_sample": False,
            "require_zdk": True,
        }
    if scrap_pct is None:
        return {
            "disposition": None,
            "rule_id": "QC.SCRAP.UNKNOWN",
            "message": "Процент брака не указан.",
            "require_second_sample": False,
            "require_zdk": False,
        }
    if scrap_pct >= SCRAP_THRESHOLD_PCT:
        return {
            "disposition": "forbid",
            "rule_id": "QC.SCRAP.GE15",
            "message": f"Брак {scrap_pct}% ≥ {SCRAP_THRESHOLD_PCT}% — браковать всю партию.",
            "require_second_sample": False,
            "require_zdk": True,
        }
    if scrap_pct > 0:
        return {
            "disposition": "commission",
            "rule_id": "QC.SCRAP.LT15",
            "message": (
                f"Брак {scrap_pct}% < {SCRAP_THRESHOLD_PCT}% — "
                "вторая выборка и решение ЗДК."
            ),
            "require_second_sample": True,
            "require_zdk": True,
        }
    return {
        "disposition": "post_and_use",
        "rule_id": "QC.SCRAP.ZERO",
        "message": "Брак 0% — разрешающий статус при полном комплекте доказательств.",
        "require_second_sample": False,
        "require_zdk": False,
    }


def control_deadline_wd(category: str | None) -> int:
    return CATEGORY_DEADLINES_WD.get(normalize_category(category), 3)


__all__ = [
    "CATEGORY_DEADLINES_WD",
    "CATEGORY_DOCUMENTS",
    "RULES_VERSION",
    "SCRAP_THRESHOLD_PCT",
    "build_mandatory_documents",
    "build_sample_rule",
    "control_deadline_wd",
    "evaluate_document_completeness",
    "evaluate_scrap_decision",
    "normalize_category",
]
