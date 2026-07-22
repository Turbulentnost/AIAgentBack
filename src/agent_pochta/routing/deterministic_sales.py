"""Детерминированные правила продукта/продаж до LLM (subject+body+вложения)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from agent_pochta.config import PROJECT_ROOT
from agent_pochta.routing.organizations import DIRECTION_COMMERCIAL, DIRECTION_DEFAULT, DIRECTION_UNCLEAR
from agent_pochta.routing.normalize import keyword_in_text, normalize_text

_DEFAULT_PATH = PROJECT_ROOT / "data" / "deterministic_sales_rules.json"


@dataclass(frozen=True)
class DeterministicHit:
    code: str
    name: str
    direction: str
    source: str
    reasoning: str
    matched_keywords: list[str]
    organization: str | None = None


@lru_cache(maxsize=1)
def load_deterministic_sales_rules(path: str = "") -> dict:
    file_path = Path(path) if path else _DEFAULT_PATH
    if not file_path.exists():
        return {}
    return json.loads(file_path.read_text(encoding="utf-8"))


def reset_deterministic_sales_rules_cache() -> None:
    load_deterministic_sales_rules.cache_clear()


def _hits_in_text(markers: list[str], text: str) -> list[str]:
    found: list[str] = []
    for marker in markers:
        m = (marker or "").strip().lower()
        if not m:
            continue
        if keyword_in_text(m, text):
            found.append(m)
    return found


def _is_foreign(text: str, sender_email: str, rules: dict) -> tuple[bool, list[str]]:
    hits = _hits_in_text(list(rules.get("foreign_markers") or []), text)
    # Domain TLD markers like ".com" — only count on sender domain, not random body noise
    sender = (sender_email or "").lower()
    domain = sender.rsplit("@", 1)[-1] if "@" in sender else ""
    exclude = {d.lower() for d in (rules.get("foreign_exclude_domains") or [])}
    foreign_tlds = [h for h in hits if h.startswith(".")]
    other_hits = [h for h in hits if not h.startswith(".")]
    tld_hit = ""
    if domain and domain not in exclude:
        for tld in (".com", ".de", ".cn", ".eu", ".uk", ".pl", ".kz", ".by", ".uz"):
            if domain.endswith(tld) and not domain.endswith(".ru"):
                tld_hit = tld
                break
        # non-ru domains that aren't free mail
        if not tld_hit and "." in domain and not domain.endswith(".ru"):
            tld_hit = domain
    matched = other_hits[:]
    if tld_hit:
        matched.append(tld_hit)
    # Need stronger signal than alone "евро" noise: foreign if TLD or >=1 lexical marker
    if tld_hit or other_hits:
        return True, matched
    return False, []


def match_deterministic_sales(
    *,
    subject: str,
    body: str,
    sender_email: str = "",
    partner: str | None = None,
    rules: dict | None = None,
) -> DeterministicHit | None:
    """Возвращает первое сработавшее жёсткое правило или None."""
    cfg = rules if rules is not None else load_deterministic_sales_rules()
    if not cfg:
        return None

    text = normalize_text(f"{subject} {body} {partner or ''}")
    sender = (sender_email or "").lower().strip()
    commercial_markers = (
        "ткп",
        "коммерческ",
        "кп ",
        "запрос цен",
        "счет",
        "счёт",
        "ценовое",
        "стоимость",
    )

    chair_hits = _hits_in_text(list(cfg.get("chairman_override_markers") or []), text)

    # 1) Продуктовые правила (БМИ, бытовые, СПУ, сервис)
    product_rules = sorted(
        cfg.get("product_rules") or [],
        key=lambda r: int(r.get("priority") or 100),
    )
    for rule in product_rules:
        hits = _hits_in_text(list(rule.get("keywords") or []), text)
        if not hits:
            continue
        if rule.get("id") == "bmi_equipment" and any(
            keyword_in_text(marker, text) for marker in commercial_markers
        ):
            return DeterministicHit(
                code="00-000128",
                name="Отдел продаж БМИ",
                direction="БМ",
                source="det_product_bmi_equipment_commercial",
                reasoning="Коммерческий запрос на оборудование БМИ",
                matched_keywords=hits,
                organization="БМ",
            )
        return DeterministicHit(
            code=str(rule["department_id"]),
            name=str(rule.get("department_name") or rule["department_id"]),
            direction=str(rule.get("direction") or DIRECTION_DEFAULT),
            source=f"det_product_{rule.get('id') or 'x'}",
            reasoning=str(rule.get("reasoning") or rule.get("id") or "product"),
            matched_keywords=hits,
            organization=rule.get("organization"),
        )

    # 2) Продажи: нужен sales-context ИЛИ уже industrial/dealer/gazprom/orkk маркер
    sales_hits = _hits_in_text(list(cfg.get("sales_context_markers") or []), text)
    foreign_marker_hits = _hits_in_text(list(cfg.get("foreign_markers") or []), text)
    dealer_hits = _hits_in_text(list(cfg.get("dealer_markers") or []), text)
    industrial_hits = _hits_in_text(list(cfg.get("industrial_markers") or []), text)
    gazprom_hits = _hits_in_text(list(cfg.get("gazprom_markers") or []), text)
    orkk_hits = _hits_in_text(list(cfg.get("orkk_holdings") or []), text)
    spu_hits = _hits_in_text(
        ["спу", "стационарная поверочная", "поверочная установка", "spu-5", "spu 5"],
        text,
    )

    igor_hits = _hits_in_text(["игорь борисович"], text)
    predsedatel_hits = _hits_in_text(
        [
            "председатель совета директоров",
            "председателю совета директоров",
        ],
        text,
    )
    sales_context = bool(
        sales_hits
        or foreign_marker_hits
        or dealer_hits
        or gazprom_hits
        or orkk_hits
        or spu_hits
        or chair_hits
    )
    if not sales_context:
        return None

    # Амураль / Игорь Борисович / Председатель СД — исключение из контура Газпром→ОПГ.
    if (
        (chair_hits and gazprom_hits)
        or (igor_hits and gazprom_hits)
        or (igor_hits and predsedatel_hits)
    ):
        chairman_keywords = igor_hits or predsedatel_hits or chair_hits
        return DeterministicHit(
            code=str(cfg["chairman_department_id"]),
            name=str(cfg.get("chairman_department_name") or "Председатель Совета Директоров"),
            direction=DIRECTION_UNCLEAR,
            source="det_chairman",
            reasoning="Амураль / Игорь Борисович / Председатель СД",
            matched_keywords=chairman_keywords,
        )

    is_foreign, foreign_hits = _is_foreign(text, sender, cfg)
    if is_foreign:
        return DeterministicHit(
            code=str(cfg["foreign_department_id"]),
            name=str(cfg.get("foreign_department_name") or "ВЭД"),
            direction=DIRECTION_COMMERCIAL,
            source="det_sales_foreign",
            reasoning="Зарубежный/экспортный контур → ВЭД",
            matched_keywords=foreign_hits or sales_hits,
        )

    # Российские продажи
    if spu_hits:
        return DeterministicHit(
            code="00-000074",
            name="Отдел продаж эталонного оборудования и услуг",
            direction=DIRECTION_COMMERCIAL,
            source="det_sales_spu",
            reasoning="СПУ → ОПЭ",
            matched_keywords=spu_hits,
        )

    if dealer_hits:
        return DeterministicHit(
            code=str(cfg["dealer_department_id"]),
            name=str(cfg.get("dealer_department_name") or "ОДП"),
            direction=DIRECTION_COMMERCIAL,
            source="det_sales_dealer",
            reasoning="Гранд / UFG-H → ОДП",
            matched_keywords=dealer_hits,
        )

    if gazprom_hits:
        return DeterministicHit(
            code=str(cfg["gazprom_department_id"]),
            name=str(cfg.get("gazprom_department_name") or "ОПГ"),
            direction=DIRECTION_COMMERCIAL,
            source="det_sales_gazprom",
            reasoning="Газпром / дочерние → ОПГ",
            matched_keywords=gazprom_hits,
        )

    if orkk_hits:
        return DeterministicHit(
            code=str(cfg["orkk_department_id"]),
            name=str(cfg.get("orkk_department_name") or "ОРКК"),
            direction=DIRECTION_COMMERCIAL,
            source="det_sales_orkk",
            reasoning="Ключевой холдинг → ОРКК",
            matched_keywords=orkk_hits,
        )

    # Промышленные без явного холдинга → ОРКК (базовый catch-all)
    if industrial_hits and sales_hits:
        return DeterministicHit(
            code=str(cfg["orkk_department_id"]),
            name=str(cfg.get("orkk_department_name") or "ОРКК"),
            direction=DIRECTION_COMMERCIAL,
            source="det_sales_industrial",
            reasoning="Промышленная тематика без холдинга → ОРКК",
            matched_keywords=industrial_hits,
        )

    return None
