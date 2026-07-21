"""Категория «Диалог»: переписка без поручения, XML dormant → активация в 1С."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from agent_pochta.config import PROJECT_ROOT, get_settings
from agent_pochta.routing.normalize import normalize_text
from agent_pochta.routing.process_type import (
    PROCESS_ISPOLNENIYE,
    PROCESS_OZNAKOMLENIYE,
    PROCESS_RASSMOTRENIYE,
)

_DEFAULT_PATH = PROJECT_ROOT / "data" / "dialog_rules.json"


class DialogMode(StrEnum):
    DORMANT = "dormant"
    ACTIVATED = "activated"


@dataclass(frozen=True)
class DialogClassification:
    """Результат классификации переписки."""

    is_dialog: bool
    mode: DialogMode | None = None
    document_kind: str = "dialog"
    register_erp: bool = False
    queue_tier: int = 2
    process_type: str = PROCESS_OZNAKOMLENIYE
    theme_action: str = "Диалог"
    thread_markers: list[str] = field(default_factory=list)
    dormant_markers: list[str] = field(default_factory=list)
    activation_markers: list[str] = field(default_factory=list)
    reasoning: str = ""


def _rules_path(path: str = "") -> Path:
    if path:
        return Path(path)
    settings = get_settings()
    configured = (settings.dialog_rules_path or "").strip()
    if configured:
        return Path(configured)
    return _DEFAULT_PATH


@lru_cache(maxsize=1)
def load_dialog_rules(path: str = "") -> dict:
    file_path = _rules_path(path)
    if not file_path.exists():
        return {}
    return json.loads(file_path.read_text(encoding="utf-8"))


def reset_dialog_rules_cache() -> None:
    load_dialog_rules.cache_clear()


def _marker_hits(markers: list[str], text: str) -> list[str]:
    found: list[str] = []
    for marker in markers:
        m = (marker or "").strip().lower()
        if not m:
            continue
        if m in text:
            found.append(m)
    return found


def _thread_signals(subject: str, body: str, cfg: dict) -> list[str]:
    thread_cfg = cfg.get("thread_markers") or {}
    hits: list[str] = []
    subj = (subject or "").strip().lower()
    body_l = (body or "").lower()

    for prefix in thread_cfg.get("subject_prefixes") or []:
        p = (prefix or "").strip().lower()
        if p and subj.startswith(p):
            hits.append(p)

    for pattern in thread_cfg.get("body_patterns") or []:
        p = (pattern or "").strip().lower()
        if not p:
            continue
        if p in body_l or p in subj:
            hits.append(p)

    min_signals = int(thread_cfg.get("min_thread_signals") or 1)
    if len(hits) < min_signals:
        return []
    return hits


def _has_reply_exchange(body: str, cfg: dict, sender_email: str) -> bool:
    exchange = cfg.get("exchange_markers") or {}
    body_norm = (body or "").replace("\r\n", "\n")
    sender = (sender_email or "").lower().strip()
    if exchange.get("require_external_sender", True):
        company_domains = [d.lower() for d in (exchange.get("company_domains") or [])]
        if sender and "@" in sender:
            domain = sender.rsplit("@", 1)[-1]
            if domain in company_domains:
                return False

    quoted_lines = [line for line in body_norm.split("\n") if line.strip().startswith(">")]
    min_lines = int(exchange.get("quoted_block_min_lines") or 2)
    if len(quoted_lines) >= min_lines:
        return True

    company_domains = [d.lower() for d in (exchange.get("company_domains") or [])]
    body_l = body_norm.lower()
    if company_domains and any(domain in body_l for domain in company_domains):
        if any(
            marker in body_l
            for marker in ("пишет:", " wrote:", "отправлено:", "original message", "исходное сообщение")
        ):
            return True
    return False


def _resolve_activation_process_type(cfg: dict, activation_hits: list[str]) -> str:
    mapping = cfg.get("activation_process_type_map") or {}
    combined = " ".join(activation_hits)
    if any(m in combined for m in ("оплат", "выстав", "счёт", "счет")):
        return str(mapping.get("payment") or PROCESS_ISPOLNENIYE)
    if any(m in combined for m in ("претенз", "требуем", "иск")):
        return str(mapping.get("claim") or PROCESS_RASSMOTRENIYE)
    return str(mapping.get("default") or PROCESS_RASSMOTRENIYE)


def _resolve_activation_theme_action(cfg: dict, activation_hits: list[str]) -> str:
    mapping = cfg.get("activation_theme_actions") or {}
    combined = " ".join(activation_hits)
    if any(m in combined for m in ("оплат", "выстав")):
        return str(mapping.get("payment") or "Оплатить")
    if any(m in combined for m in ("претенз", "требуем")):
        return str(mapping.get("claim") or "Решить")
    if any(m in combined for m in ("просим", "запрос", "направ", "предостав", "присл")):
        return str(mapping.get("request") or "Запрос")
    if any(m in combined for m in ("соглас", "рассмотр", "провер")):
        return str(mapping.get("review") or "Рассмотреть")
    return str(mapping.get("default") or "Рассмотреть")


def classify_dialog(
    *,
    subject: str,
    body: str,
    sender_email: str = "",
    claim: bool = False,
    process_type: str = "",
    rules: dict | None = None,
) -> DialogClassification:
    """Определяет, является ли письмо диалогом, и dormant vs activated."""
    cfg = rules if rules is not None else load_dialog_rules()
    if not cfg or not cfg.get("enabled", True):
        return DialogClassification(is_dialog=False)

    text = normalize_text(f"{subject}\n{body}")
    document_kind = str(cfg.get("document_kind") or "dialog")

    if claim and cfg.get("exclude_if_claim", True):
        return DialogClassification(is_dialog=False)

    if cfg.get("exclude_if_obligation", True):
        obligation_hits = _marker_hits(list(cfg.get("obligation_markers") or []), text)
        if obligation_hits:
            return DialogClassification(is_dialog=False)

    thread_hits = _thread_signals(subject, body, cfg)
    if not thread_hits:
        return DialogClassification(is_dialog=False)

    activation_hits = _marker_hits(list(cfg.get("activation_markers") or []), text)
    if activation_hits:
        process = _resolve_activation_process_type(cfg, activation_hits)
        theme_action = _resolve_activation_theme_action(cfg, activation_hits)
        return DialogClassification(
            is_dialog=True,
            mode=DialogMode.ACTIVATED,
            document_kind=document_kind,
            register_erp=bool(cfg.get("register_erp_activated", True)),
            queue_tier=int(cfg.get("queue_tier_activated") or 1),
            process_type=process,
            theme_action=theme_action,
            activation_markers=activation_hits,
            reasoning=f"dialog_activated: {', '.join(activation_hits[:3])}",
        )

    excluded_process = {p.lower() for p in (cfg.get("exclude_process_types") or [])}
    if (process_type or "").strip().lower() in excluded_process:
        return DialogClassification(is_dialog=False)

    dormant_hits = _marker_hits(list(cfg.get("dormant_markers") or []), text)
    has_exchange = _has_reply_exchange(body, cfg, sender_email)
    if not dormant_hits and not has_exchange:
        return DialogClassification(is_dialog=False)

    return DialogClassification(
        is_dialog=True,
        mode=DialogMode.DORMANT,
        document_kind=document_kind,
        register_erp=bool(cfg.get("register_erp_dormant", False)),
        queue_tier=int(cfg.get("queue_tier_dormant") or 2),
        process_type=str(cfg.get("dormant_process_type") or PROCESS_OZNAKOMLENIYE),
        theme_action=str(cfg.get("dormant_theme_action") or "Диалог"),
        thread_markers=thread_hits,
        dormant_markers=dormant_hits,
        reasoning="dialog_dormant: переписка без явного поручения",
    )


def build_dialog_dormant_theme(subject: str, *, theme_action: str = "Диалог") -> str:
    """XML theme для dormant-диалога: «Диалог: тема»."""
    from agent_pochta.routing.xml_builder import format_action_theme, sanitize_theme

    cleaned = sanitize_theme(subject)
    if cleaned == "Без темы":
        return f"{theme_action}: переписка"
    action = (theme_action or "Диалог").strip()
    prefix_re = re.compile(rf"^{re.escape(action)}\s*:\s*", re.IGNORECASE)
    if prefix_re.match(cleaned):
        return sanitize_theme(cleaned)
    return format_action_theme(action, subject)


def apply_dialog_classification(
    dialog_cls: DialogClassification,
    *,
    subject: str,
) -> tuple[str, str, str]:
    """Возвращает (process_type, xml_theme, trace_tag) для dormant или activated."""
    if dialog_cls.mode == DialogMode.ACTIVATED:
        from agent_pochta.routing.xml_builder import format_action_theme

        return (
            dialog_cls.process_type,
            format_action_theme(dialog_cls.theme_action, subject),
            "dialog_activated",
        )
    return (
        dialog_cls.process_type,
        build_dialog_dormant_theme(subject, theme_action=dialog_cls.theme_action),
        "dialog_dormant",
    )
