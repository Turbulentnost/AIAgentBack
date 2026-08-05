"""Детерминированные правила продукта/продаж до LLM (subject+body+вложения)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from agent_pochta.config import PROJECT_ROOT
from agent_pochta.routing.organizations import (
    DIRECTION_COMMERCIAL,
    DIRECTION_DEFAULT,
    DIRECTION_UNCLEAR,
    leadership_department_allowed,
)
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


def ved_deterministic_routing_enabled(rules: dict | None = None) -> bool:
    """Жёсткая маршрутизация в ВЭД (det_foreign_domain / det_sales_foreign)."""
    cfg = rules if rules is not None else load_deterministic_sales_rules()
    return bool(cfg.get("ved_deterministic_routing_enabled", False))


def _hits_in_text(markers: list[str], text: str) -> list[str]:
    found: list[str] = []
    for marker in markers:
        m = (marker or "").strip().lower()
        if not m:
            continue
        if keyword_in_text(m, text):
            found.append(m)
    return found


_EMAIL_DOMAIN_RE = re.compile(r"[\w.+-]+@([\w.-]+\.[\w.-]+)", re.IGNORECASE)
_URL_HOST_RE = re.compile(r"https?://([\w.-]+(?:\.[\w.-]+)*|\[[\da-f:.]+\])", re.IGNORECASE)
_WWW_HOST_RE = re.compile(r"\bwww\.([\w.-]+\.[\w.-]+)", re.IGNORECASE)
_MAILTO_RE = re.compile(r"mailto:([\w.+-]+@[\w.-]+)", re.IGNORECASE)

# Домены/TLD, которые НЕ считаются зарубежными для маршрутизации в ВЭД.
_DOMESTIC_DOMAIN_SUFFIXES = (
    ".com.ru",
    ".net.ru",
    ".org.ru",
    ".pp.ru",
    ".ru",
    ".рф",
    ".su",
    ".by",
    ".kz",
    ".uz",
)


def _normalize_domain(raw: str) -> str:
    host = raw.strip().lower().rstrip(".,;:)>\"'")
    if host.startswith("www."):
        host = host[4:]
    if "@" in host:
        host = host.rsplit("@", 1)[-1]
    return host


def _is_domestic_domain(domain: str, exclude: set[str]) -> bool:
    if not domain or domain in exclude:
        return True
    for suffix in _DOMESTIC_DOMAIN_SUFFIXES:
        if domain == suffix.lstrip(".") or domain.endswith(suffix):
            return True
    return False


def _extract_domains_from_text(
    text: str,
    sender_email: str,
    *,
    to_addresses: list[str] | None = None,
    cc_addresses: list[str] | None = None,
    reply_to: str | None = None,
) -> set[str]:
    domains: set[str] = set()
    for address in [sender_email, reply_to, *(to_addresses or []), *(cc_addresses or [])]:
        value = (address or "").strip()
        if "@" in value:
            domains.add(_normalize_domain(value))

    for pattern in (_EMAIL_DOMAIN_RE, _MAILTO_RE):
        for match in pattern.finditer(text):
            domains.add(_normalize_domain(match.group(1)))

    for pattern in (_URL_HOST_RE, _WWW_HOST_RE):
        for match in pattern.finditer(text):
            domains.add(_normalize_domain(match.group(1)))

    return {domain for domain in domains if domain and "." in domain}


def _is_foreign(
    text: str,
    sender_email: str,
    rules: dict,
    *,
    to_addresses: list[str] | None = None,
    cc_addresses: list[str] | None = None,
    reply_to: str | None = None,
) -> tuple[bool, list[str]]:
    """ВЭД только при явных зарубежных доменах в адресах/URL, не по ключевым словам."""
    exclude = {d.lower() for d in (rules.get("foreign_exclude_domains") or [])}
    foreign_domains: list[str] = []
    for domain in sorted(
        _extract_domains_from_text(
            text,
            sender_email,
            to_addresses=to_addresses,
            cc_addresses=cc_addresses,
            reply_to=reply_to,
        )
    ):
        if not _is_domestic_domain(domain, exclude):
            foreign_domains.append(domain)
        elif _has_hard_foreign_tld(domain, rules) and domain not in exclude:
            # TLD из справочника, если домен ошибочно попал в domestic.
            foreign_domains.append(domain)
    if foreign_domains:
        return True, foreign_domains
    return False, []


def _has_hard_foreign_tld(domain: str, rules: dict) -> bool:
    """Проверка TLD из foreign_hard_tlds (например .de, .cn) — hard foreign."""
    tlds = [str(t).lower().lstrip(".") for t in (rules.get("foreign_hard_tlds") or []) if str(t).strip()]
    if not tlds or not domain:
        return False
    host = domain.lower().strip(".")
    for tld in tlds:
        if host == tld or host.endswith("." + tld):
            # Не считать .com.ru и т.п. зарубежными — они уже в domestic suffixes.
            if _is_domestic_domain(host, set()):
                return False
            return True
    return False


def foreign_confirm_markers_in_text(text: str, rules: dict | None = None) -> list[str]:
    """Маркеры foreign_markers — только подтверждение hard foreign, не самостоятельный route."""
    cfg = rules if rules is not None else load_deterministic_sales_rules()
    if not cfg:
        return []
    return _hits_in_text(list(cfg.get("foreign_markers") or []), normalize_text(text))


def is_domestic_sender_domain(sender_email: str, rules: dict | None = None) -> bool:
    cfg = rules if rules is not None else load_deterministic_sales_rules()
    exclude = {d.lower() for d in (cfg.get("foreign_exclude_domains") or [])}
    if "@" not in (sender_email or ""):
        return True
    domain = _normalize_domain(sender_email)
    return _is_domestic_domain(domain, exclude)


def commercial_markers_in_text(text: str, rules: dict | None = None) -> list[str]:
    cfg = rules if rules is not None else load_deterministic_sales_rules()
    if not cfg:
        return []
    markers = list(cfg.get("sales_context_markers") or [])
    markers.extend(list(cfg.get("dealer_markers") or [])[:20])
    return _hits_in_text(markers, normalize_text(text))


def is_commercial_ru_context(
    *,
    subject: str,
    body: str,
    sender_email: str = "",
    rules: dict | None = None,
) -> bool:
    """Коммерческие маркеры при РФ/СНГ домене отправителя — штраф для ВЭД."""
    cfg = rules if rules is not None else load_deterministic_sales_rules()
    if not is_domestic_sender_domain(sender_email, cfg):
        return False
    return bool(commercial_markers_in_text(f"{subject} {body}", cfg))


def match_foreign_domain_route(
    *,
    subject: str,
    body: str,
    sender_email: str = "",
    to_addresses: list[str] | None = None,
    cc_addresses: list[str] | None = None,
    reply_to: str | None = None,
    rules: dict | None = None,
) -> DeterministicHit | None:
    """Маршрут в ВЭД по зарубежному домену без sales-context и ключевых слов."""
    cfg = rules if rules is not None else load_deterministic_sales_rules()
    if not cfg or not ved_deterministic_routing_enabled(cfg):
        return None
    text = normalize_text(f"{subject} {body}")
    sender = (sender_email or "").lower().strip()
    is_foreign, foreign_hits = _is_foreign(
        text,
        sender,
        cfg,
        to_addresses=to_addresses,
        cc_addresses=cc_addresses,
        reply_to=reply_to,
    )
    if not is_foreign:
        return None
    return DeterministicHit(
        code=str(cfg["foreign_department_id"]),
        name=str(cfg.get("foreign_department_name") or "ВЭД"),
        direction=DIRECTION_COMMERCIAL,
        source="det_foreign_domain",
        reasoning="Зарубежный домен в from/to/cc/URL → ВЭД",
        matched_keywords=foreign_hits,
    )


def match_deterministic_sales(
    *,
    subject: str,
    body: str,
    sender_email: str = "",
    partner: str | None = None,
    rules: dict | None = None,
    recipient: str = "",
    email_aliases: dict | None = None,
) -> DeterministicHit | None:
    """Возвращает первое сработавшее жёсткое правило или None."""
    cfg = rules if rules is not None else load_deterministic_sales_rules()
    if not cfg:
        return None

    def _accept(hit: DeterministicHit) -> DeterministicHit | None:
        if leadership_department_allowed(
            recipient=recipient,
            department_code=hit.code,
            match_source=hit.source,
            email_aliases=email_aliases,
        ):
            return hit
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
            return _accept(
                DeterministicHit(
                    code="00-000128",
                    name="Отдел продаж БМИ",
                    direction="БМ",
                    source="det_product_bmi_equipment_commercial",
                    reasoning="Коммерческий запрос на оборудование БМИ",
                    matched_keywords=hits,
                    organization="БМ",
                )
            )
        return _accept(
            DeterministicHit(
                code=str(rule["department_id"]),
                name=str(rule.get("department_name") or rule["department_id"]),
                direction=str(rule.get("direction") or DIRECTION_DEFAULT),
                source=f"det_product_{rule.get('id') or 'x'}",
                reasoning=str(rule.get("reasoning") or rule.get("id") or "product"),
                matched_keywords=hits,
                organization=rule.get("organization"),
            )
        )

    # 2) Продажи: нужен sales-context ИЛИ уже industrial/dealer/gazprom/orkk маркер
    sales_hits = _hits_in_text(list(cfg.get("sales_context_markers") or []), text)
    dealer_hits = _hits_in_text(list(cfg.get("dealer_markers") or []), text)
    industrial_hits = _hits_in_text(list(cfg.get("industrial_markers") or []), text)
    gazprom_hits = _hits_in_text(list(cfg.get("gazprom_markers") or []), text)
    orkk_hits = _hits_in_text(list(cfg.get("orkk_holdings") or []), text)
    orkk_request_hits = _hits_in_text(list(cfg.get("orkk_request_markers") or []), text)
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
        or dealer_hits
        or gazprom_hits
        or orkk_hits
        or orkk_request_hits
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
        chairman_hit = _accept(
            DeterministicHit(
                code=str(cfg["chairman_department_id"]),
                name=str(cfg.get("chairman_department_name") or "Председатель Совета Директоров"),
                direction=DIRECTION_UNCLEAR,
                source="det_chairman",
                reasoning="Амураль / Игорь Борисович / Председатель СД",
                matched_keywords=chairman_keywords,
            )
        )
        if chairman_hit is not None:
            return chairman_hit

    is_foreign, foreign_hits = _is_foreign(text, sender, cfg)
    if is_foreign and ved_deterministic_routing_enabled(cfg):
        return _accept(
            DeterministicHit(
                code=str(cfg["foreign_department_id"]),
                name=str(cfg.get("foreign_department_name") or "ВЭД"),
                direction=DIRECTION_COMMERCIAL,
                source="det_sales_foreign",
                reasoning="Зарубежный/экспортный контур → ВЭД",
                matched_keywords=foreign_hits or sales_hits,
            )
        )

    # Российские продажи
    if spu_hits:
        return _accept(
            DeterministicHit(
                code="00-000074",
                name="Отдел продаж эталонного оборудования и услуг",
                direction=DIRECTION_COMMERCIAL,
                source="det_sales_spu",
                reasoning="СПУ → ОПЭ",
                matched_keywords=spu_hits,
            )
        )

    if dealer_hits:
        return _accept(
            DeterministicHit(
                code=str(cfg["dealer_department_id"]),
                name=str(cfg.get("dealer_department_name") or "ОДП"),
                direction=DIRECTION_COMMERCIAL,
                source="det_sales_dealer",
                reasoning="Гранд / UFG-H → ОДП",
                matched_keywords=dealer_hits,
            )
        )

    if orkk_request_hits:
        return _accept(
            DeterministicHit(
                code=str(cfg["orkk_department_id"]),
                name=str(cfg.get("orkk_department_name") or "ОРКК"),
                direction=DIRECTION_COMMERCIAL,
                source="det_sales_orkk_request",
                reasoning="ТКП / запрос поставки → ОРКК",
                matched_keywords=orkk_request_hits,
            )
        )

    if gazprom_hits:
        return _accept(
            DeterministicHit(
                code=str(cfg["gazprom_department_id"]),
                name=str(cfg.get("gazprom_department_name") or "ОПГ"),
                direction=DIRECTION_COMMERCIAL,
                source="det_sales_gazprom",
                reasoning="Газпром / дочерние → ОПГ",
                matched_keywords=gazprom_hits,
            )
        )

    if orkk_hits:
        return _accept(
            DeterministicHit(
                code=str(cfg["orkk_department_id"]),
                name=str(cfg.get("orkk_department_name") or "ОРКК"),
                direction=DIRECTION_COMMERCIAL,
                source="det_sales_orkk",
                reasoning="Ключевой холдинг → ОРКК",
                matched_keywords=orkk_hits,
            )
        )

    # Промышленные без явного холдинга → ОРКК (базовый catch-all)
    if industrial_hits and sales_hits:
        return _accept(
            DeterministicHit(
                code=str(cfg["orkk_department_id"]),
                name=str(cfg.get("orkk_department_name") or "ОРКК"),
                direction=DIRECTION_COMMERCIAL,
                source="det_sales_industrial",
                reasoning="Промышленная тематика без холдинга → ОРКК",
                matched_keywords=industrial_hits,
            )
        )

    return None
