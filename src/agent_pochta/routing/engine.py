"""RuleRouter — детерминированная маршрутизация (ТЗ §8, §10)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from agent_pochta.config import PROJECT_ROOT
from agent_pochta.routing.corrections import find_correction_match
from agent_pochta.routing.deterministic_sales import (
    foreign_confirm_markers_in_text,
    is_commercial_ru_context,
    load_deterministic_sales_rules,
    match_deterministic_sales,
    match_foreign_domain_route,
)
from agent_pochta.routing.evidence import evaluate_route_confidence
from agent_pochta.routing.models import RoutingDecision, ServiceRoute
from agent_pochta.routing.normalize import (
    contains_claim_marker,
    keyword_in_text,
    normalize_email_address,
    normalize_text,
)
from agent_pochta.routing.onec_corrections import find_onec_correction_match
from agent_pochta.routing.organizations import (
    COMMERCIAL_DEPARTMENT_CODES,
    DIRECTION_COMMERCIAL,
    DIRECTION_DEFAULT,
    DIRECTION_UNCLEAR,
    KS_PAYER_DIRECTION_DEPARTMENT_CODES,
    PRODUCTION_DIRECTION_DEPARTMENT_CODES,
    leadership_department_allowed,
    normalize_organization_code,
    resolve_direction_for_department,
)
from agent_pochta.routing.process_type import infer_process_type_heuristic
from agent_pochta.routing.recipients import build_routing_search_text
from agent_pochta.routing.reply_routing import match_gazprom_np_reply
from agent_pochta.routing.xml_builder import (
    build_xml_document,
    sanitize_theme,
    strip_forbidden_tags,
    validate_xml_document,
)
from agent_pochta.schemas import EmailMessage, SenderIdentity

_PACKAGE_RULES_PATH = Path(__file__).resolve().parent / "data" / "routing_rules.json"
_PROJECT_RULES_PATH = PROJECT_ROOT / "data" / "routing_rules.json"

_ORG_FROM_RECIPIENT = (
    ("almaz", "АЛ"),
    ("mgs_", "МГ"),
    ("mgs@", "МГ"),
)

# Коды org, для которых direction = код организации (как в detect_direction).
_ORG_AS_DIRECTION = frozenset({"АЛ", "МГ", "АМ", "МИ", "БМ"})


@dataclass
class _Candidate:
    code: str
    name: str
    direction: str
    source: str
    reasoning: str
    topic_hits: int = 0
    content_hits: int = 0
    organization: str | None = None
    matched_keywords: list[str] = field(default_factory=list)


@dataclass
class RouteEngine:
    rules: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> RouteEngine:
        if path is not None:
            rules_path = path
        elif _PROJECT_RULES_PATH.is_file():
            # Канонический файл проекта (монтируется в Docker) важнее bundled-копии.
            rules_path = _PROJECT_RULES_PATH
        else:
            rules_path = _PACKAGE_RULES_PATH
        with rules_path.open(encoding="utf-8") as fh:
            return cls(rules=json.load(fh))

    def _dept_name(self, code: str, fallback: str = "") -> str:
        names = self.rules.get("department_names", {})
        return names.get(code, fallback or code)

    def detect_organization(self, text: str, *, recipient: str = "") -> str:
        recipient = recipient.lower()
        for marker, org in _ORG_FROM_RECIPIENT:
            if marker in recipient:
                return org

        normalized = normalize_text(text)
        org_keywords = self.rules.get("organization_keywords", {})
        scored: list[tuple[int, str]] = []
        for org, keywords in org_keywords.items():
            active = [str(kw).strip() for kw in (keywords or []) if str(kw).strip()]
            if not active:
                continue
            hits = [kw for kw in active if keyword_in_text(kw, normalized)]
            if hits:
                # Предпочитаем более длинные (специфичные) совпадения.
                scored.append((max(len(h) for h in hits), org))
        if scored:
            scored.sort(key=lambda item: (-item[0], item[1]))
            return scored[0][1]
        return "НП"

    def detect_direction(self, organization: str, candidate_direction: str | None = None) -> str:
        if organization in _ORG_AS_DIRECTION:
            return organization
        return candidate_direction or DIRECTION_UNCLEAR

    def _exact_email_match(self, recipient: str, subject: str, body: str) -> list[_Candidate]:
        recipient = normalize_email_address(recipient, self.rules.get("email_aliases"))
        text = normalize_text(f"{subject} {body}")
        local = recipient.split("@", 1)[0]
        found: list[_Candidate] = []
        for rule in self.rules.get("exact_email_rules", []):
            if rule.get("is_fallback_mailbox"):
                continue
            if recipient != rule["email"].lower():
                continue
            about = rule.get("about", "")
            about_tokens = [token for token in about.split() if token]
            topic_hits = sum(1 for token in about_tokens if keyword_in_text(token, text)) if about_tokens else 0
            matched = [local]
            matched.extend(token for token in about_tokens if keyword_in_text(token, text))
            found.append(
                _Candidate(
                    code=rule["code"],
                    name=rule.get("name") or self._dept_name(rule["code"]),
                    direction=rule.get("direction", DIRECTION_DEFAULT),
                    source="exact_email",
                    reasoning=f"Точное совпадение email получателя {recipient}",
                    topic_hits=topic_hits,
                    matched_keywords=matched,
                )
            )
        return found

    def _email_keyword_match(self, recipient: str) -> list[_Candidate]:
        local = recipient.split("@", 1)[0].lower()
        found: list[_Candidate] = []
        for rule in self.rules.get("email_keyword_rules", []):
            keyword = rule["keyword"].lower()
            if keyword in local:
                found.append(
                    _Candidate(
                        code=rule["code"],
                        name=rule.get("name") or self._dept_name(rule["code"]),
                        direction=rule.get("direction", DIRECTION_DEFAULT),
                        source="email_keyword",
                        reasoning=f"Ключевое слово «{keyword}» в адресе {recipient}",
                        matched_keywords=[keyword],
                    )
                )
        return found

    def _content_match(self, recipient: str, subject: str, body: str) -> list[_Candidate]:
        text = normalize_text(
            build_routing_search_text(recipient=recipient, subject=subject, body=body)
        )
        found: list[_Candidate] = []
        for rule in self.rules.get("content_rules", []):
            hits = [kw for kw in rule.get("keywords", []) if keyword_in_text(kw, text)]
            if not hits:
                continue
            found.append(
                _Candidate(
                    code=rule["code"],
                    name=rule.get("name") or self._dept_name(rule["code"]),
                    direction=rule.get("direction", DIRECTION_DEFAULT),
                    source="content",
                    reasoning=f"Совпадение по содержимому: {', '.join(hits[:5])}",
                    content_hits=len(hits),
                    topic_hits=1 if rule.get("about") else 0,
                    organization=rule.get("organization"),
                    matched_keywords=hits,
                )
            )
        return found

    def _sales_rules(self, subject: str, body: str, partner: str | None) -> list[_Candidate]:
        """Legacy soft sales — fallback, если детерминированный каскад не сработал."""
        text = normalize_text(f"{subject} {body} {partner or ''}")
        found: list[_Candidate] = []
        for marker in self.rules.get("sales_gazprom", []):
            if marker in text:
                found.append(
                    _Candidate(
                        code="00-000076",
                        name=self._dept_name("00-000076", "Отдел по работе с ПАО Газпром"),
                        direction=DIRECTION_COMMERCIAL,
                        source="sales_gazprom",
                        reasoning="ПАО Газпром / дочерние общества",
                        matched_keywords=[marker],
                    )
                )
                return found
        for holding in self.rules.get("sales_orkk_holdings", []):
            if holding in text:
                found.append(
                    _Candidate(
                        code="00-000042",
                        name=self._dept_name("00-000042", "ОРКК"),
                        direction=DIRECTION_COMMERCIAL,
                        source="sales_orkk",
                        reasoning="Холдинг/ВИНК → ОРКК",
                        matched_keywords=[holding],
                    )
                )
                return found
        for marker in self.rules.get("sales_odp", []):
            if marker in text:
                found.append(
                    _Candidate(
                        code="00-000155",
                        name=self._dept_name("00-000155", "ОДП"),
                        direction=DIRECTION_COMMERCIAL,
                        source="sales_odp",
                        reasoning="Дилерский/региональный контур → ОДП",
                        matched_keywords=[marker],
                    )
                )
                return found
        return found

    def _deterministic_candidate(
        self,
        subject: str,
        body: str,
        partner: str | None,
        sender_email: str = "",
        *,
        recipient: str = "",
    ) -> _Candidate | None:
        hit = match_deterministic_sales(
            subject=subject,
            body=body,
            sender_email=sender_email,
            partner=partner,
            recipient=recipient,
            email_aliases=self.rules.get("email_aliases"),
        )
        if hit is None:
            return None
        keywords = list(hit.matched_keywords)
        if hit.organization:
            keywords.append(f"org:{hit.organization}")
        candidate = _Candidate(
            code=hit.code,
            name=self._dept_name(hit.code, hit.name),
            direction=hit.direction,
            source=hit.source,
            reasoning=hit.reasoning,
            topic_hits=max(2, len(hit.matched_keywords)),
            content_hits=max(1, len(hit.matched_keywords)),
            organization=hit.organization,
            matched_keywords=keywords,
        )
        return self._accept_leadership_candidate(recipient, candidate)

    def _foreign_domain_match(
        self,
        subject: str,
        body: str,
        *,
        sender_email: str = "",
        to_addresses: list[str] | None = None,
        cc_addresses: list[str] | None = None,
        reply_to: str | None = None,
        recipient: str = "",
    ) -> _Candidate | None:
        """Зарубежный домен в from/to/cc/теле → ВЭД (до keyword/content)."""
        hit = match_foreign_domain_route(
            subject=subject,
            body=body,
            sender_email=sender_email,
            to_addresses=to_addresses,
            cc_addresses=cc_addresses,
            reply_to=reply_to,
        )
        if hit is None:
            return None
        candidate = _Candidate(
            code=hit.code,
            name=self._dept_name(hit.code, hit.name),
            direction=hit.direction,
            source=hit.source,
            reasoning=hit.reasoning,
            topic_hits=max(2, len(hit.matched_keywords)),
            content_hits=max(1, len(hit.matched_keywords)),
            matched_keywords=list(hit.matched_keywords),
        )
        return self._accept_leadership_candidate(recipient, candidate)

    def _reserve_route(self) -> _Candidate:
        code = self.rules.get("reserve_code", "00-000066")
        return _Candidate(
            code=code,
            name=self.rules.get("reserve_name") or self._dept_name(code),
            direction=DIRECTION_UNCLEAR,
            source="reserve",
            reasoning="Резервный маршрут при отсутствии однозначного правила",
        )

    def _leadership_allowed(self, recipient: str, candidate: _Candidate) -> bool:
        return leadership_department_allowed(
            recipient=recipient,
            department_code=candidate.code,
            match_source=candidate.source,
            email_aliases=self.rules.get("email_aliases"),
        )

    def _filter_leadership_candidates(
        self,
        recipient: str,
        candidates: list[_Candidate],
    ) -> list[_Candidate]:
        return [c for c in candidates if self._leadership_allowed(recipient, c)]

    def _accept_leadership_candidate(
        self,
        recipient: str,
        candidate: _Candidate | None,
    ) -> _Candidate | None:
        if candidate is None:
            return None
        if self._leadership_allowed(recipient, candidate):
            return candidate
        return None

    def _info_strict_mailbox(self) -> str:
        cfg = self.rules.get("info_strict_rules") or {}
        mailbox = str(cfg.get("mailbox") or "info@turbo-don.ru")
        return normalize_email_address(mailbox, self.rules.get("email_aliases"))

    def _info_strict_candidate(
        self,
        *,
        rule: dict,
        source: str,
        hits: list[str],
        reasoning: str,
    ) -> _Candidate:
        code = str(rule["code"])
        return _Candidate(
            code=code,
            name=rule.get("name") or self._dept_name(code),
            direction=rule.get("direction", DIRECTION_DEFAULT),
            source=source,
            reasoning=reasoning,
            topic_hits=max(2, len(hits)),
            content_hits=max(1, len(hits)),
            matched_keywords=hits,
        )

    def _info_strict_match(
        self,
        recipient: str,
        subject: str,
        body: str,
        sender_email: str = "",
    ) -> _Candidate | None:
        """Жёсткие правила только для info@turbo-don.ru (до keyword/RAG)."""
        cfg = self.rules.get("info_strict_rules")
        if not cfg:
            return None
        recipient = normalize_email_address(recipient, self.rules.get("email_aliases"))
        if recipient != self._info_strict_mailbox():
            return None

        text = normalize_text(f"{subject} {body}")
        sender_norm = (sender_email or "").lower().strip()

        rule1 = cfg.get("ilchenko_ud") or {}
        name_hits: list[str] = []
        for pattern in rule1.get("name_patterns") or []:
            if keyword_in_text(str(pattern), text):
                name_hits.append(str(pattern))
        org_hits: list[str] = []
        for pattern in rule1.get("org_patterns") or []:
            if keyword_in_text(str(pattern), text):
                org_hits.append(str(pattern))
        domain_hits: list[str] = []
        if name_hits or org_hits:
            for domain in rule1.get("sender_domain_patterns") or []:
                marker = str(domain).lower().strip()
                if marker and marker in sender_norm:
                    domain_hits.append(marker)
        hits = name_hits + org_hits + domain_hits
        if hits and rule1.get("code") and not name_hits:
            exclude_patterns = rule1.get("exclude_content_patterns") or []
            if any(keyword_in_text(str(pattern), text) for pattern in exclude_patterns):
                hits = []
        if hits and rule1.get("code"):
            rule_code = rule1.get("rule_code", "INFO_STRICT_ILCHENKO")
            return self._info_strict_candidate(
                rule=rule1,
                source="info_strict",
                hits=hits,
                reasoning=(
                    f"info@ strict {rule_code}: Амураль/Газпром/Водоканал → "
                    f"Председатель Совета Директоров; {', '.join(hits[:5])}"
                ),
            )

        rule2 = cfg.get("ministry_od") or {}
        ministry_patterns = self.rules.get("ministry_content_patterns") or rule2.get(
            "content_patterns"
        ) or []
        ministry_hits = [
            str(pattern)
            for pattern in ministry_patterns
            if keyword_in_text(str(pattern), text)
        ]
        if ministry_hits and rule2.get("code"):
            rule_code = rule2.get("rule_code", "INFO_STRICT_MINISTRY")
            return self._info_strict_candidate(
                rule=rule2,
                source="info_strict",
                hits=ministry_hits,
                reasoning=(
                    f"info@ strict {rule_code}: министерство → "
                    f"Операционный директор; {', '.join(ministry_hits[:5])}"
                ),
            )
        return None

    def _institution_chairman_match(
        self,
        subject: str,
        body: str,
        partner: str | None = None,
        *,
        recipient: str = "",
    ) -> _Candidate | None:
        """ТПП / АПГО и аналоги → Председатель СД (только info@, до keyword/RAG)."""
        cfg = self.rules.get("institution_chairman_rules")
        if not cfg or not cfg.get("code"):
            return None
        text = normalize_text(f"{subject} {body} {partner or ''}")
        hits = [
            str(pattern)
            for pattern in (cfg.get("content_patterns") or [])
            if keyword_in_text(str(pattern), text)
        ]
        if not hits:
            return None
        rule_code = cfg.get("rule_code", "INSTITUTION_CHAIRMAN")
        candidate = _Candidate(
            code=str(cfg["code"]),
            name=cfg.get("name") or self._dept_name(str(cfg["code"])),
            direction=cfg.get("direction", DIRECTION_DEFAULT),
            source="institution_chairman",
            reasoning=(
                f"{rule_code}: ТПП/АПГО → Председатель Совета Директоров; "
                f"{', '.join(hits[:5])}"
            ),
            topic_hits=max(2, len(hits)),
            content_hits=max(1, len(hits)),
            matched_keywords=hits,
        )
        return self._accept_leadership_candidate(recipient, candidate)

    def _institution_operational_director_match(
        self,
        subject: str,
        body: str,
        partner: str | None = None,
        sender_email: str = "",
        *,
        recipient: str = "",
    ) -> _Candidate | None:
        """Министерство / администрация → Операционный директор (только info@)."""
        cfg = self.rules.get("institution_operational_director_rules")
        if not cfg or not cfg.get("code"):
            return None
        text = normalize_text(f"{subject} {body} {partner or ''}")
        hits = [
            str(pattern)
            for pattern in (cfg.get("content_patterns") or [])
            if keyword_in_text(str(pattern), text)
        ]
        for pattern in cfg.get("sender_domain_patterns") or []:
            marker = str(pattern).lower().strip()
            sender_norm = (sender_email or "").lower().strip()
            if marker and marker in sender_norm:
                hits.append(marker)
        if not hits:
            return None
        rule_code = cfg.get("rule_code", "INSTITUTION_MINISTRY_ADMIN")
        candidate = _Candidate(
            code=str(cfg["code"]),
            name=cfg.get("name") or self._dept_name(str(cfg["code"])),
            direction=cfg.get("direction", DIRECTION_DEFAULT),
            source="institution_operational_director",
            reasoning=(
                f"{rule_code}: министерство/администрация → Операционный директор; "
                f"{', '.join(hits[:5])}"
            ),
            topic_hits=max(2, len(hits)),
            content_hits=max(1, len(hits)),
            matched_keywords=hits,
        )
        return self._accept_leadership_candidate(recipient, candidate)

    def _gazprom_np_reply_match(
        self,
        subject: str,
        body: str,
        sender_email: str = "",
        *,
        recipient: str = "",
    ) -> _Candidate | None:
        """Ответ в переписке по Газпрому: НП в теле → ОПГ, иначе → Операционный директор."""
        cfg = self.rules.get("gazprom_np_reply_rules")
        if not cfg or not cfg.get("code"):
            return None
        branch, hits = match_gazprom_np_reply(
            subject=subject,
            body=body,
            sender_email=sender_email,
            rules=cfg,
        )
        if not branch:
            return None
        rule_code = cfg.get("rule_code", "GAZPROM_NP_REPLY")
        marker = str(cfg.get("marker") or "НП")
        if branch == "opg":
            code = str(cfg["code"])
            name = cfg.get("name") or self._dept_name(code)
            reasoning = (
                f"{rule_code}: ответ в переписке по Газпрому, "
                f"пометка {marker} в теле → Отдел по работе с ПАО Газпром"
            )
        else:
            code = str(cfg.get("operational_director_code") or "00-000152")
            name = cfg.get("operational_director_name") or self._dept_name(
                code, "ОПЕРАЦИОННЫЙ ДИРЕКТОР"
            )
            reasoning = (
                f"{rule_code}: ответ в переписке по Газпрому без пометки {marker} "
                f"в теле → Операционный директор"
            )
        candidate = _Candidate(
            code=code,
            name=name,
            direction=cfg.get("direction", DIRECTION_COMMERCIAL)
            if branch == "opg"
            else cfg.get("operational_director_direction", DIRECTION_DEFAULT),
            source="gazprom_np_reply",
            reasoning=reasoning,
            topic_hits=max(2, len(hits)),
            content_hits=max(1, len(hits)),
            matched_keywords=hits,
        )
        return self._accept_leadership_candidate(recipient, candidate)

    def _ud_transfer_match(
        self,
        subject: str,
        body: str,
        partner: str | None = None,
        *,
        recipient: str = "",
    ) -> _Candidate | None:
        """Управление делами → помощник зам. операционного директора."""
        cfg = self.rules.get("ud_transfer_rules")
        if not cfg or not cfg.get("code"):
            return None
        text = normalize_text(f"{subject} {body} {partner or ''}")
        hits = [
            str(pattern)
            for pattern in (cfg.get("content_patterns") or [])
            if keyword_in_text(str(pattern), text)
        ]
        if not hits:
            return None
        rule_code = cfg.get("rule_code", "UD_TRANSFER_DEPUTY_OD_ASSISTANT")
        code = str(cfg["code"])
        candidate = _Candidate(
            code=code,
            name=cfg.get("name") or self._dept_name(code),
            direction=cfg.get("direction", DIRECTION_DEFAULT),
            source="ud_transfer",
            reasoning=(
                f"{rule_code}: Управление делами → "
                f"помощник зам. операционного директора; {', '.join(hits[:5])}"
            ),
            topic_hits=max(2, len(hits)),
            content_hits=max(1, len(hits)),
            matched_keywords=hits,
        )
        return self._accept_leadership_candidate(recipient, candidate)

    def _info_unclear_route(self) -> _Candidate:
        cfg = (self.rules.get("info_strict_rules") or {}).get("unclear") or {}
        code = str(cfg.get("code") or self.rules.get("reserve_code", "00-000066"))
        rule_code = cfg.get("rule_code", "INFO_STRICT_UNCLEAR")
        return _Candidate(
            code=code,
            name=cfg.get("name") or self._dept_name(code),
            direction=cfg.get("direction", DIRECTION_UNCLEAR),
            source="info_strict_unclear",
            reasoning=f"info@ strict {rule_code}: неясное письмо → УД / КС",
            topic_hits=1,
            content_hits=1,
            matched_keywords=[rule_code],
        )

    def _correction_match(
        self,
        recipient: str,
        sender_email: str,
        subject: str,
        body: str,
    ) -> _Candidate | None:
        from agent_pochta.config import get_settings

        if get_settings().bge_department_routing_enabled:
            return None
        entry = find_correction_match(
            recipient=recipient,
            sender_email=sender_email,
            subject=subject,
            body=body,
        )
        if entry is None:
            return None
        code = str(entry["department_id"])
        keywords = [str(kw).strip() for kw in (entry.get("keywords") or []) if str(kw).strip()]
        return _Candidate(
            code=code,
            name=entry.get("department_name") or self._dept_name(code),
            direction=DIRECTION_DEFAULT,
            source="human_correction",
            reasoning="Коррекция оператора (human-in-the-loop)",
            topic_hits=len(keywords),
            content_hits=len(keywords),
            matched_keywords=keywords,
        )

    def _pick_candidates(
        self,
        recipient: str,
        subject: str,
        body: str,
        partner: str | None,
        sender_email: str = "",
        *,
        to_addresses: list[str] | None = None,
        cc_addresses: list[str] | None = None,
        reply_to: str | None = None,
    ) -> list[_Candidate]:
        recipient = normalize_email_address(recipient, self.rules.get("email_aliases"))
        correction = self._correction_match(recipient, sender_email, subject, body)
        if correction is not None and self._leadership_allowed(recipient, correction):
            return [correction]
        foreign = self._foreign_domain_match(
            subject,
            body,
            sender_email=sender_email,
            to_addresses=to_addresses,
            cc_addresses=cc_addresses,
            reply_to=reply_to,
            recipient=recipient,
        )
        if foreign is not None:
            return [foreign]
        gazprom_np_reply = self._gazprom_np_reply_match(
            subject, body, sender_email, recipient=recipient
        )
        if gazprom_np_reply is not None:
            return [gazprom_np_reply]
        info_strict = self._info_strict_match(recipient, subject, body, sender_email)
        if info_strict is not None:
            return [info_strict]
        institution = self._institution_chairman_match(
            subject, body, partner, recipient=recipient
        )
        if institution is not None:
            return [institution]
        institution_od = self._institution_operational_director_match(
            subject, body, partner, sender_email, recipient=recipient
        )
        if institution_od is not None:
            return [institution_od]
        ud_transfer = self._ud_transfer_match(subject, body, partner, recipient=recipient)
        if ud_transfer is not None:
            return [ud_transfer]
        det = self._deterministic_candidate(
            subject, body, partner, sender_email, recipient=recipient
        )
        if det is not None:
            return [det]
        routes = self._exact_email_match(recipient, subject, body)
        if not routes:
            routes = self._email_keyword_match(recipient)
        if not routes:
            routes = self._content_match(recipient, subject, body)
        routes = self._filter_leadership_candidates(recipient, routes)
        commercial_markers = ("ткп", "коммерческ", "кп ", "запрос цен", "счет", "счёт")
        text = normalize_text(f"{subject} {body}")
        if any(m in text for m in commercial_markers):
            sales = self._sales_rules(subject, body, partner)
            if sales:
                sales = self._filter_leadership_candidates(recipient, sales)
                routes = sales + routes
        if not routes:
            if recipient == self._info_strict_mailbox() and self.rules.get("info_strict_rules"):
                routes = [self._info_unclear_route()]
            else:
                routes = [self._reserve_route()]
        return routes

    def _collect_matching_keywords(self, candidates: list[_Candidate]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for candidate in candidates[:3]:
            for kw in candidate.matched_keywords:
                value = kw.strip()
                if not value or value in seen:
                    continue
                seen.add(value)
                result.append(value)
        return result[:10]

    def _resolve_partner(self, sender: SenderIdentity | None) -> str | None:
        if sender and sender.contractor and sender.contractor.name:
            name = sender.contractor.name.strip()
            return name or None
        return None

    def route(
        self,
        email: EmailMessage,
        *,
        combined_text: str,
        recipient: str | None = None,
        sender: SenderIdentity | None = None,
    ) -> RoutingDecision:
        recipient = normalize_email_address(
            recipient or email.routing_recipient or email.mailbox,
            self.rules.get("email_aliases"),
        )
        subject = email.subject or ""
        body = combined_text or email.body_text or ""
        partner = self._resolve_partner(sender)

        organization = self.detect_organization(f"{subject} {body}", recipient=recipient)
        candidates = self._pick_candidates(
            recipient,
            subject,
            body,
            partner,
            sender_email=email.sender_email,
            to_addresses=email.to,
            cc_addresses=email.cc,
            reply_to=email.reply_to,
        )

        unique_codes = {c.code for c in candidates}
        has_conflict = len(unique_codes) > 1
        primary = candidates[0]
        if has_conflict:
            exact = [c for c in candidates if c.source == "exact_email"]
            if exact:
                primary = max(exact, key=lambda c: c.topic_hits)
            elif len({c.code for c in candidates if c.source != "reserve"}) == 1:
                has_conflict = False

        if primary.organization:
            organization = primary.organization

        onec_match = find_onec_correction_match(
            recipient=recipient,
            sender_email=email.sender_email or "",
            subject=subject,
            body=body,
        )
        if onec_match:
            org_from_onec = normalize_organization_code(onec_match.get("organization"))
            if org_from_onec:
                organization = org_from_onec
            partner_from_onec = (onec_match.get("partner") or "").strip()
            if partner_from_onec:
                partner = partner_from_onec

        direction = self.detect_direction(organization, primary.direction)
        if primary.code in COMMERCIAL_DEPARTMENT_CODES:
            direction = DIRECTION_COMMERCIAL
        elif primary.code in PRODUCTION_DIRECTION_DEPARTMENT_CODES:
            direction = DIRECTION_DEFAULT
        elif primary.code in KS_PAYER_DIRECTION_DEPARTMENT_CODES:
            direction = DIRECTION_UNCLEAR
        claim = contains_claim_marker(f"{subject} {body}")

        info_no_topic = (
            recipient == "info@turbo-don.ru"
            and primary.source == "reserve"
            and not any(c.source == "content" for c in candidates)
        )

        det_rules = load_deterministic_sales_rules()
        foreign_confirms = foreign_confirm_markers_in_text(
            f"{subject} {body}", det_rules
        )
        commercial_ru = is_commercial_ru_context(
            subject=subject,
            body=body,
            sender_email=email.sender_email or "",
            rules=det_rules,
        )
        sender_domain_confirm = False
        if primary.source in {
            "info_strict",
            "institution_chairman",
            "institution_operational_director",
            "gazprom_np_reply",
        }:
            # Домен/орг уже в matched_keywords правила — считаем confirm.
            sender_domain_confirm = any(
                "." in (kw or "") or "gazprom" in (kw or "").lower()
                for kw in (primary.matched_keywords or [])
            )

        evidence = evaluate_route_confidence(
            match_source=primary.source,
            department_code=primary.code,
            topic_hits=primary.topic_hits,
            content_hits=primary.content_hits,
            matched_keywords=list(primary.matched_keywords or []),
            org_confirmed=organization != "НП",
            has_conflict=has_conflict and primary.source != "human_correction",
            info_mailbox_no_topic=info_no_topic,
            unknown_route=primary.source == "reserve",
            foreign_confirm_markers=foreign_confirms
            if primary.source == "det_foreign_domain" or primary.code == "00-000015"
            else None,
            sender_domain_confirm=sender_domain_confirm,
            commercial_ru_vs_ved=commercial_ru and primary.code == "00-000015",
        )
        score, level = evidence.score, evidence.level

        keywords = self._collect_matching_keywords(candidates)

        process = infer_process_type_heuristic(subject, body, claim=claim)

        services = [
            ServiceRoute(
                code=primary.code,
                name=primary.name,
                process=process,
                reasoning=primary.reasoning,
                direction=direction,
            )
        ]

        decision = RoutingDecision(
            organization=organization,
            direction=direction,
            process=process,
            services=services,
            confidence_level=level,
            confidence_score=score,
            matching_keywords=keywords,
            partner=partner,
            claim=claim,
            theme=sanitize_theme(subject),
            has_conflict=has_conflict,
            match_source=primary.source,
            hard_signal_count=evidence.hard_count,
            adaptive_signal_count=evidence.adaptive_count,
            hard_foreign=evidence.hard_foreign,
            evidence_notes=list(evidence.notes),
        )

        xml = build_xml_document(email, recipient=recipient, decision=decision)
        xml = strip_forbidden_tags(xml)
        if validate_xml_document(xml):
            decision.xml_document = xml
        return decision


_engine: RouteEngine | None = None


def get_route_engine() -> RouteEngine:
    global _engine
    if _engine is None:
        _engine = RouteEngine.load()
    return _engine


def reset_route_engine() -> None:
    global _engine
    _engine = None


def route_email(
    email: EmailMessage,
    *,
    combined_text: str = "",
    recipient: str | None = None,
    sender: SenderIdentity | None = None,
    engine: RouteEngine | None = None,
) -> RoutingDecision:
    return (engine or get_route_engine()).route(
        email,
        combined_text=combined_text,
        recipient=recipient,
        sender=sender,
    )


def rebuild_decision_xml(
    email: EmailMessage,
    decision: RoutingDecision,
    *,
    recipient: str,
    department_id: str | None = None,
    department_name: str | None = None,
    theme: str | None = None,
    partner: str | None = None,
    process: str | None = None,
) -> RoutingDecision:
    """Пересобирает XML после изменения отдела (RAG/LLM или human-in-the-loop)."""
    services = list(decision.services)
    resolved_process = process or decision.process or (services[0].process if services else "исполнение")
    direction = decision.direction
    previous_code = services[0].code if services else None
    if department_id:
        base = services[0] if services else ServiceRoute(code=department_id, name=department_name or department_id)
        if department_id != previous_code:
            direction = resolve_direction_for_department(
                department_id,
                decision.organization,
                rules=get_route_engine().rules,
                fallback_direction=decision.direction,
            )
        services = [
            ServiceRoute(
                code=department_id,
                name=department_name or base.name,
                process=resolved_process,
                direction=direction,
            )
        ]
    elif process is not None:
        services = [
            ServiceRoute(
                code=svc.code,
                name=svc.name,
                process=resolved_process,
                reasoning=svc.reasoning,
                direction=svc.direction,
            )
            for svc in services
        ]
    updates: dict = {"services": services, "process": resolved_process}
    if department_id and department_id != previous_code:
        updates["direction"] = direction
    if theme is not None:
        updates["theme"] = theme
    if partner is not None:
        updates["partner"] = partner
    updated = decision.model_copy(update=updates)
    xml = build_xml_document(
        email,
        recipient=recipient,
        decision=updated,
    )
    xml = strip_forbidden_tags(xml)
    if validate_xml_document(xml):
        return updated.model_copy(update={"xml_document": xml})
    return updated
