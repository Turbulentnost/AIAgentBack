"""Приоритет и очередь регистрации по таблице G.1 типового справочника."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from agent_pochta.config import PROJECT_ROOT
from agent_pochta.routing.normalize import contains_claim_marker, normalize_text
from agent_pochta.schemas import Priority, SenderIdentity

_DEFAULT_RULES_PATH = PROJECT_ROOT / "data" / "document_priority_rules.json"

_IMMEDIATE_MARKERS = (
    "немедленн",
    "безотлагательн",
    "в срочном порядке",
    "крайне срочн",
)


@dataclass(frozen=True)
class DocumentKindRule:
    id: str
    label: str
    keywords: tuple[str, ...]
    primary_department_codes: tuple[str, ...]
    copy_department_codes: tuple[str, ...]
    priority: Priority
    queue_tier: int
    register_erp: bool
    sla_note: str = ""
    is_default: bool = False


@dataclass(frozen=True)
class PriorityDecision:
    document_kind: str
    document_kind_label: str
    priority: Priority
    queue_tier: int
    register_erp: bool
    primary_department_codes: tuple[str, ...] = ()
    copy_department_codes: tuple[str, ...] = ()
    has_obligation: bool = False
    sla_note: str = ""
    reasoning: str = ""
    elevated_by_obligation: bool = False


@dataclass
class _PriorityRulesData:
    obligation_patterns: tuple[re.Pattern[str], ...]
    kinds: tuple[DocumentKindRule, ...]
    default_kind: DocumentKindRule | None = None
    keyword_patterns: dict[str, tuple[re.Pattern[str], ...]] = field(default_factory=dict)


def _parse_priority(raw: str) -> Priority:
    value = (raw or "").strip().lower()
    try:
        return Priority(value)
    except ValueError:
        return Priority.NORMAL


def _compile_marker(marker: str) -> re.Pattern[str]:
    marker = marker.strip()
    if not marker:
        return re.compile(r"(?!)")
    if any(ch in marker for ch in ".*+?[](){}|^$\\"):
        return re.compile(marker, re.IGNORECASE)
    return re.compile(re.escape(marker), re.IGNORECASE)


@lru_cache(maxsize=4)
def load_priority_rules(path: str | None = None) -> _PriorityRulesData:
    rules_path = Path(path) if path else _DEFAULT_RULES_PATH
    with rules_path.open(encoding="utf-8") as fh:
        raw = json.load(fh)

    obligation_patterns = tuple(
        _compile_marker(str(m)) for m in raw.get("obligation_markers", []) if str(m).strip()
    )
    kinds: list[DocumentKindRule] = []
    keyword_patterns: dict[str, tuple[re.Pattern[str], ...]] = {}
    default_kind: DocumentKindRule | None = None

    for item in raw.get("kinds", []):
        kind = DocumentKindRule(
            id=str(item["id"]),
            label=str(item.get("label") or item["id"]),
            keywords=tuple(str(k) for k in item.get("keywords") or []),
            primary_department_codes=tuple(
                str(c) for c in item.get("primary_department_codes") or []
            ),
            copy_department_codes=tuple(str(c) for c in item.get("copy_department_codes") or []),
            priority=_parse_priority(str(item.get("priority") or "normal")),
            queue_tier=int(item.get("queue_tier") or 1),
            register_erp=bool(item.get("register_erp", True)),
            sla_note=str(item.get("sla_note") or ""),
            is_default=bool(item.get("is_default")),
        )
        kinds.append(kind)
        keyword_patterns[kind.id] = tuple(_compile_marker(k) for k in kind.keywords)
        if kind.is_default:
            default_kind = kind

    return _PriorityRulesData(
        obligation_patterns=obligation_patterns,
        kinds=tuple(kinds),
        default_kind=default_kind,
        keyword_patterns=keyword_patterns,
    )


def has_response_obligation(text: str, *, claim: bool = False) -> bool:
    """Срок ответа / требование / поручение / обязательство (G.1, нижняя строка)."""
    if claim or contains_claim_marker(text):
        return True
    normalized = normalize_text(text)
    if not normalized:
        return False
    rules = load_priority_rules()
    for pat in rules.obligation_patterns:
        for match in pat.finditer(normalized):
            start = max(0, match.start() - 48)
            window = normalized[start : match.start()]
            if re.search(r"(?:без|отсутств\w*)(?:\s+\w+){0,4}\s*$", window):
                continue
            return True
    return False


def _kind_matches(kind: DocumentKindRule, text: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    if kind.is_default or not patterns:
        return False
    return any(pat.search(text) for pat in patterns)


def classify_document_kind(
    subject: str = "",
    body: str = "",
    *,
    rules: _PriorityRulesData | None = None,
) -> DocumentKindRule:
    rules = rules or load_priority_rules()
    text = normalize_text(f"{subject} {body}")
    for kind in rules.kinds:
        patterns = rules.keyword_patterns.get(kind.id, ())
        if _kind_matches(kind, text, patterns):
            return kind
    if rules.default_kind is not None:
        return rules.default_kind
    return DocumentKindRule(
        id="general_correspondence",
        label="Общая переписка",
        keywords=(),
        primary_department_codes=("00-000066",),
        copy_department_codes=(),
        priority=Priority.NORMAL,
        queue_tier=1,
        register_erp=True,
        is_default=True,
    )


def select_priority(
    *,
    subject: str = "",
    body: str = "",
    claim: bool = False,
    sender: SenderIdentity | None = None,
) -> PriorityDecision:
    """Выбор приоритета, очереди и флага регистрации в 1С по таблице G.1."""
    rules = load_priority_rules()
    combined = f"{subject} {body}"
    kind = classify_document_kind(subject, body, rules=rules)
    obligation = has_response_obligation(combined, claim=claim)
    priority = kind.priority
    queue_tier = kind.queue_tier
    register_erp = kind.register_erp
    elevated = False
    reasons: list[str] = [f"вид={kind.id}"]

    sender_type = ""
    if sender and sender.contractor and sender.contractor.contractor_type:
        sender_type = sender.contractor.contractor_type.strip().lower()
    if sender_type == "госорган":
        priority = Priority.URGENT
        queue_tier = 1
        register_erp = True
        reasons.append("contractor_type=госорган")

    if obligation and (queue_tier > 1 or not register_erp or priority == Priority.NORMAL):
        # G.1: при сроке/требовании/поручении/обязательстве — 1-я очередь и регистрация в 1С.
        elevated = True
        queue_tier = 1
        register_erp = True
        if priority == Priority.NORMAL:
            priority = Priority.HIGH
        reasons.append("obligation→1-я очередь")

    if claim and priority == Priority.NORMAL:
        priority = Priority.HIGH
        queue_tier = 1
        register_erp = True
        reasons.append("claim→high")

    text_l = normalize_text(combined)
    if any(m in text_l for m in _IMMEDIATE_MARKERS) and priority != Priority.URGENT:
        if obligation or kind.id in {"gov_and_courts", "supervisory_organs", "claims_conflict"}:
            priority = Priority.URGENT
            reasons.append("immediate_marker")

    return PriorityDecision(
        document_kind=kind.id,
        document_kind_label=kind.label,
        priority=priority,
        queue_tier=queue_tier,
        register_erp=register_erp,
        primary_department_codes=kind.primary_department_codes,
        copy_department_codes=kind.copy_department_codes,
        has_obligation=obligation,
        sla_note=kind.sla_note,
        reasoning="; ".join(reasons),
        elevated_by_obligation=elevated,
    )


def clear_priority_rules_cache() -> None:
    load_priority_rules.cache_clear()
