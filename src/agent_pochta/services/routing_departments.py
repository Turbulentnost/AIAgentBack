"""Сборка RAG-каталога отделов из routing_rules.json и структуры 1С."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from agent_pochta.config import PROJECT_ROOT
from agent_pochta.schemas import Department, DepartmentRecord

_PACKAGE_RULES_PATH = Path(__file__).resolve().parent.parent / "routing" / "data" / "routing_rules.json"
_DEFAULT_RULES_PATH = PROJECT_ROOT / "data" / "routing_rules.json"
_DEFAULT_ENTERPRISE_PATH = PROJECT_ROOT / "data" / "enterprise_positions.json"
_DEFAULT_COMPARISON_PATH = PROJECT_ROOT / "data" / "departments_comparison_report.json"
_DEFAULT_TZ_TOPICS_PATH = PROJECT_ROOT / "data" / "tz_department_topics.json"

_SPECIAL_SKIP_CODES = frozenset({"00-999997", "00-999998", "00-999999"})
_LEADER_POSITION_MARKERS = ("начальник", "директор", "главный", "руководитель", "заместитель директора")
_PATH_TOKEN_STOPWORDS = frozenset({
    "ремонт",
    "ремонта",
    "сервис",
    "сервиса",
    "обслуживание",
    "обслуживания",
    "участок",
    "служба",
    "отдел",
    "цех",
    "производство",
})
_RECIPIENT_ONLY_STOPWORDS = frozenset({"info", "info@turbo-don.ru"})


def resolve_routing_rules_path(path: Path | str | None = None) -> Path:
    """Путь к routing_rules.json: явный → ROUTING_RULES_PATH → data/routing_rules.json."""
    if path:
        candidate = Path(path)
        if candidate.is_file():
            return candidate
        raise FileNotFoundError(f"routing_rules.json не найден: {candidate}")

    from agent_pochta.config import get_settings

    settings_path = get_settings().routing_rules_path.strip()
    if settings_path:
        candidate = Path(settings_path)
        if candidate.is_file():
            return candidate

    for fallback in (_DEFAULT_RULES_PATH, _PACKAGE_RULES_PATH):
        if fallback.is_file():
            return fallback

    raise FileNotFoundError(
        "routing_rules.json не найден. Задайте ROUTING_RULES_PATH или положите файл в data/"
    )


def load_routing_rules(path: Path | str | None = None) -> dict:
    rules_path = resolve_routing_rules_path(path)
    with rules_path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _normalize_keyword(value: str) -> str:
    return value.strip().lower()


def _add_keywords(bucket: dict[str, set[str]], code: str, *values: str) -> None:
    for value in values:
        normalized = _normalize_keyword(value)
        if normalized:
            bucket.setdefault(code, set()).add(normalized)


def _add_path_token(bucket: dict[str, set[str]], code: str, token: str) -> None:
    normalized = _normalize_keyword(token)
    if normalized and normalized not in _PATH_TOKEN_STOPWORDS and len(normalized) >= 4:
        bucket.setdefault(code, set()).add(normalized)


def _tokenize(text: str) -> list[str]:
    return [_normalize_keyword(token) for token in text.split() if _normalize_keyword(token)]


def build_departments_from_rules(rules: dict) -> list[Department]:
    """Агрегирует keywords по кодам 1С из всех правил RuleRouter (без spam-кода)."""
    spam_code = str(rules.get("spam_code", "00-999999"))
    dept_names: dict[str, str] = {
        str(code): str(name) for code, name in rules.get("department_names", {}).items()
    }

    keywords_by_code: dict[str, set[str]] = {}
    responsibility_by_code: dict[str, set[str]] = {}

    for rule in rules.get("email_keyword_rules", []):
        code = str(rule["code"])
        _add_keywords(keywords_by_code, code, str(rule.get("keyword", "")))
        if rule.get("name"):
            for token in _tokenize(str(rule["name"])):
                _add_keywords(keywords_by_code, code, token)

    for rule in rules.get("exact_email_rules", []):
        code = str(rule["code"])
        email = str(rule.get("email", "")).lower().strip()
        if "@" in email:
            local_part = email.split("@", 1)[0]
            _add_keywords(keywords_by_code, code, local_part)
        about = str(rule.get("about", ""))
        if about:
            _add_keywords(keywords_by_code, code, about)
            for token in _tokenize(about):
                _add_keywords(keywords_by_code, code, token)
            responsibility_by_code.setdefault(code, set()).add(about.strip())

    for rule in rules.get("content_rules", []):
        code = str(rule["code"])
        for keyword in rule.get("keywords", []):
            _add_keywords(keywords_by_code, code, str(keyword))
        about = str(rule.get("about", ""))
        if about:
            _add_keywords(keywords_by_code, code, about)
            responsibility_by_code.setdefault(code, set()).add(about.strip())

    for keyword in rules.get("sales_orkk_holdings", []):
        _add_keywords(keywords_by_code, "00-000076", str(keyword))
    for keyword in rules.get("sales_gazprom", []):
        _add_keywords(keywords_by_code, "00-000076", str(keyword))
    for keyword in rules.get("sales_odp", []):
        _add_keywords(keywords_by_code, "00-000075", str(keyword))

    departments: list[Department] = []
    for code, name in sorted(dept_names.items()):
        if code == spam_code:
            continue

        keywords = set(keywords_by_code.get(code, set()))
        for token in _tokenize(name):
            keywords.add(token)
        keywords.add(_normalize_keyword(name))

        responsibility = "; ".join(sorted(responsibility_by_code.get(code, set())))

        departments.append(
            Department(
                department_id=code,
                department_name=name,
                head_name="—",
                responsibility=responsibility,
                keywords=sorted(keywords),
            )
        )

    return departments


def resolve_enterprise_positions_path(path: Path | str | None = None) -> Path:
    if path:
        candidate = Path(path)
        if candidate.is_file():
            return candidate
        raise FileNotFoundError(f"enterprise_positions.json не найден: {candidate}")
    if _DEFAULT_ENTERPRISE_PATH.is_file():
        return _DEFAULT_ENTERPRISE_PATH
    raise FileNotFoundError(
        "enterprise_positions.json не найден. Запустите scripts/_compare_departments.py"
    )


def resolve_comparison_report_path(path: Path | str | None = None) -> Path | None:
    if path:
        candidate = Path(path)
        return candidate if candidate.is_file() else None
    return _DEFAULT_COMPARISON_PATH if _DEFAULT_COMPARISON_PATH.is_file() else None


def load_tz_department_topics(path: Path | str | None = None) -> dict[str, dict]:
    """Темы/наименования из ТЗ Прил. Д (data/tz_department_topics.json)."""
    file_path = Path(path) if path else _DEFAULT_TZ_TOPICS_PATH
    if not file_path.is_file():
        return {}
    data = json.loads(file_path.read_text(encoding="utf-8"))
    return {str(code): entry for code, entry in data.items()}


def _apply_tz_topics(keywords_by_code: dict[str, set[str]], topics: dict[str, dict]) -> None:
    for code, entry in topics.items():
        for topic in entry.get("topics") or []:
            _add_keywords(keywords_by_code, code, str(topic))
            for token in _tokenize(str(topic)):
                _add_keywords(keywords_by_code, code, token)
        for name in entry.get("names") or []:
            _add_keywords(keywords_by_code, code, str(name))
            for token in _tokenize(str(name)):
                if token not in _PATH_TOKEN_STOPWORDS:
                    _add_keywords(keywords_by_code, code, token)


def _append_rule_only_departments(
    departments: list[Department],
    rules: dict,
    rules_departments: dict[str, Department],
    *,
    tz_topics: dict[str, dict] | None = None,
    extra_keywords: dict[str, list[str]] | None = None,
) -> list[Department]:
    """Добавляет отделы из routing_rules, отсутствующие в структуре 1С (напр. 00-000037)."""
    spam_code = str(rules.get("spam_code", "00-999999"))
    present = {d.department_id for d in departments}
    keywords_by_code: dict[str, set[str]] = {}
    tz_topics = tz_topics or {}

    for code, name in rules.get("department_names", {}).items():
        code = str(code)
        if code == spam_code or code in present:
            continue
        if code in rules_departments:
            keywords_by_code[code] = set(rules_departments[code].keywords)
            responsibility = rules_departments[code].responsibility
            head_name = rules_departments[code].head_name
        else:
            keywords_by_code[code] = set()
            responsibility = ""
            head_name = "—"

        _add_keywords(keywords_by_code, code, str(name))
        for token in _tokenize(str(name)):
            _add_keywords(keywords_by_code, code, token)
        _apply_tz_topics(keywords_by_code, {code: tz_topics[code]} if code in tz_topics else {})

        if extra_keywords and code in extra_keywords:
            for kw in extra_keywords[code]:
                _add_keywords(keywords_by_code, code, str(kw))

        departments.append(
            Department(
                department_id=code,
                department_name=str(name),
                head_name=head_name,
                responsibility=responsibility,
                keywords=sorted(keywords_by_code.get(code, set())),
            )
        )

    return departments


def is_liquidated_department(
    name: str | None = None,
    path: str | None = None,
    *,
    deletion_mark: bool = False,
) -> bool:
    """Подразделение ликвидировано: пометка удаления, префикс (ликв.) или ветка _Ликвидированные."""
    if deletion_mark:
        return True
    name = (name or "").strip()
    path = (path or "").strip()
    if name.startswith("(ликв.)") or "(ликв.)" in name:
        return True
    if "_Ликвидированные" in path or name == "_Ликвидированные":
        return True
    return False


def _add_email_keywords(bucket: dict[str, set[str]], code: str, email: str) -> None:
    email = email.lower().strip()
    if not email or "@" not in email:
        return
    bucket.setdefault(code, set()).add(email)
    local_part = email.split("@", 1)[0]
    _add_keywords(bucket, code, local_part)


def _add_path_keywords(bucket: dict[str, set[str]], code: str, path: str) -> None:
    if not path:
        return
    for segment in path.split(" / "):
        segment = segment.strip()
        if not segment:
            continue
        _add_keywords(bucket, code, segment)
        for token in _tokenize(segment):
            _add_path_token(bucket, code, token)


def load_tz_emails_by_code(
    enterprise: dict,
    *,
    comparison: dict | None = None,
) -> dict[str, list[str]]:
    """Email-адреса из ТЗ (Прил. Д) по коду подразделения."""
    emails_by_code: dict[str, set[str]] = {}

    tz_extract = enterprise.get("tz_extract") or {}
    for dept in tz_extract.get("departments") or []:
        code = str(dept.get("code") or "")
        if code:
            emails_by_code.setdefault(code, set()).update(dept.get("emails") or [])

    for rule in tz_extract.get("email_rules") or []:
        code = str(rule.get("code") or "")
        if code:
            emails_by_code.setdefault(code, set()).update(rule.get("emails") or [])

    if comparison:
        for row in comparison.get("matching_codes") or []:
            code = str(row.get("code") or "")
            if code:
                emails_by_code.setdefault(code, set()).update(row.get("emails_tz") or [])
        for row in comparison.get("email_mismatches_tz_vs_rules") or []:
            code = str(row.get("code") or "")
            if code:
                emails_by_code.setdefault(code, set()).update(row.get("tz_emails") or [])
        for row in comparison.get("email_matching_tz_vs_rules") or []:
            code = str(row.get("code") or "")
            if code:
                emails_by_code.setdefault(code, set()).update(row.get("emails") or [])

    return {code: sorted(values) for code, values in sorted(emails_by_code.items())}


def _tz_routing_codes(enterprise: dict, rules: dict) -> set[str]:
    """Коды из ТЗ, для которых разрешены special-коды (spam/reserve)."""
    codes: set[str] = set()
    spam_code = str(rules.get("spam_code", "00-999999"))
    for code in rules.get("department_names", {}):
        if str(code) != spam_code:
            codes.add(str(code))
    tz_extract = enterprise.get("tz_extract") or {}
    for dept in tz_extract.get("departments") or []:
        code = str(dept.get("code") or "")
        if code:
            codes.add(code)
    for rule in tz_extract.get("email_rules") or []:
        code = str(rule.get("code") or "")
        if code:
            codes.add(code)
    return codes


def _find_head_name(path: str, name: str, assignments: list[dict]) -> str:
    name_norm = _normalize_keyword(name)
    path = (path or "").strip()
    candidates: list[tuple[str, str]] = []

    for row in assignments:
        dept = str(row.get("department") or "")
        dept_l = dept.lower()
        matched = False
        if path and path in dept:
            matched = True
        elif name_norm and (dept_l.endswith(name_norm) or f"/ {name_norm}" in dept_l or dept_l.endswith(f" {name_norm}")):
            matched = True
        if not matched:
            continue

        position = str(row.get("position") or "").lower()
        if not any(marker in position for marker in _LEADER_POSITION_MARKERS):
            continue
        employee = str(row.get("employee") or "").strip()
        if employee:
            candidates.append((str(row.get("period") or ""), employee))

    if not candidates:
        return "—"
    candidates.sort(reverse=True)
    return candidates[0][1]


def _should_skip_code(code: str, tz_routing_codes: set[str]) -> bool:
    if code in _SPECIAL_SKIP_CODES and code not in tz_routing_codes:
        return True
    return False


def build_departments_from_structure(
    rules: dict | None = None,
    *,
    enterprise_path: Path | str | None = None,
    comparison_path: Path | str | None = None,
    tz_topics_path: Path | str | None = None,
    extra_keywords_path: Path | str | None = None,
) -> list[Department]:
    """Собирает RAG-отделы из структуры 1С (~135), объединяя routing_rules и email ТЗ."""
    from agent_pochta.services.rag_import import load_department_keywords, merge_department_keywords

    rules = rules or load_routing_rules()
    enterprise_file = resolve_enterprise_positions_path(enterprise_path)
    enterprise = json.loads(enterprise_file.read_text(encoding="utf-8"))

    comparison: dict | None = None
    comparison_file = resolve_comparison_report_path(comparison_path)
    if comparison_file:
        comparison = json.loads(comparison_file.read_text(encoding="utf-8"))

    rules_departments = {d.department_id: d for d in build_departments_from_rules(rules)}
    tz_emails = load_tz_emails_by_code(enterprise, comparison=comparison)
    tz_topics = load_tz_department_topics(tz_topics_path)
    extra_keywords = load_department_keywords(
        Path(extra_keywords_path) if extra_keywords_path else None
    )
    tz_routing_codes = _tz_routing_codes(enterprise, rules)
    rules_names = {str(k): str(v) for k, v in rules.get("department_names", {}).items()}
    assignments = enterprise.get("assignments") or []

    keywords_by_code: dict[str, set[str]] = {}
    responsibility_by_code: dict[str, str] = {}

    structure_rows = enterprise.get("structure_departments_routing_codes") or []
    departments: list[Department] = []

    for row in structure_rows:
        code = str(row.get("code") or "").strip()
        if not code or not re.match(r"00-\d{6}", code):
            continue
        if _should_skip_code(code, tz_routing_codes):
            continue
        if is_liquidated_department(row.get("name"), row.get("path")):
            continue

        name_onec = str(row.get("name") or code).strip()
        path = str(row.get("path") or "").strip()
        department_name = rules_names.get(code) or name_onec

        keywords: set[str] = set()
        if code in rules_departments:
            keywords.update(rules_departments[code].keywords)
            responsibility_by_code[code] = rules_departments[code].responsibility

        _add_keywords(keywords_by_code, code, department_name)
        for token in _tokenize(department_name):
            _add_keywords(keywords_by_code, code, token)
        _add_keywords(keywords_by_code, code, name_onec)
        for token in _tokenize(name_onec):
            _add_keywords(keywords_by_code, code, token)
        _add_path_keywords(keywords_by_code, code, path)
        _apply_tz_topics(keywords_by_code, {code: tz_topics[code]} if code in tz_topics else {})

        for email in tz_emails.get(code, []):
            _add_email_keywords(keywords_by_code, code, email)
        for email in row.get("emails") or []:
            _add_email_keywords(keywords_by_code, code, str(email))

        keywords.update(keywords_by_code.get(code, set()))
        head_name = _find_head_name(path, name_onec, assignments)

        departments.append(
            Department(
                department_id=code,
                department_name=department_name,
                head_name=head_name,
                responsibility=responsibility_by_code.get(code, ""),
                keywords=sorted(keywords),
            )
        )

    departments = _append_rule_only_departments(
        departments,
        rules,
        rules_departments,
        tz_topics=tz_topics,
        extra_keywords=extra_keywords,
    )
    departments = merge_department_keywords(departments, extra_keywords)
    for dept in departments:
        filtered = [kw for kw in dept.keywords if kw not in _RECIPIENT_ONLY_STOPWORDS]
        if len(filtered) != len(dept.keywords):
            idx = next(i for i, d in enumerate(departments) if d.department_id == dept.department_id)
            departments[idx] = dept.model_copy(update={"keywords": filtered})

    return sorted(departments, key=lambda d: d.department_id)


def list_active_departments_for_ui(
    rules: dict | None = None,
    *,
    enterprise_path: Path | str | None = None,
) -> list[dict[str, str]]:
    """Активные отделы для UI: код 1С и полное имя из структуры (без spam/ликвид.)."""
    rules = rules or load_routing_rules()
    enterprise_file = resolve_enterprise_positions_path(enterprise_path)
    enterprise = json.loads(enterprise_file.read_text(encoding="utf-8"))
    tz_routing_codes = _tz_routing_codes(enterprise, rules)

    items: list[dict[str, str]] = []
    for row in enterprise.get("structure_departments_routing_codes") or []:
        code = str(row.get("code") or "").strip()
        if not code or not re.match(r"00-\d{6}", code):
            continue
        if _should_skip_code(code, tz_routing_codes):
            continue
        if is_liquidated_department(row.get("name"), row.get("path")):
            continue

        name = str(row.get("name") or code).strip()
        items.append({"id": code, "name": name})

    return sorted(items, key=lambda item: item["id"])


def directions_by_code_from_rules(rules: dict) -> dict[str, str]:
    """Наиболее частое направление по коду из правил RuleRouter."""
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for section in ("email_keyword_rules", "exact_email_rules", "content_rules"):
        for rule in rules.get(section, []):
            code = str(rule["code"])
            direction = str(rule.get("direction", "")).strip()
            if direction:
                counts[code][direction] += 1
    return {code: counter.most_common(1)[0][0] for code, counter in counts.items()}


def _primary_email_for_code(
    code: str,
    *,
    tz_topics: dict[str, dict],
    tz_emails: dict[str, list[str]],
) -> str | None:
    topic = tz_topics.get(code) or {}
    topic_emails = [str(email) for email in topic.get("emails") or [] if str(email).strip()]
    if topic_emails:
        return topic_emails[0]
    fallback = tz_emails.get(code) or []
    return str(fallback[0]) if fallback else None


def _all_emails_for_code(
    code: str,
    *,
    tz_topics: dict[str, dict],
    tz_emails: dict[str, list[str]],
) -> list[str]:
    topic = tz_topics.get(code) or {}
    merged = [str(email) for email in topic.get("emails") or []]
    merged.extend(str(email) for email in tz_emails.get(code, []))
    return list(dict.fromkeys(email for email in merged if email.strip()))


def build_department_records_for_db(
    rules: dict | None = None,
    *,
    enterprise_path: Path | str | None = None,
    comparison_path: Path | str | None = None,
    tz_topics_path: Path | str | None = None,
) -> list[DepartmentRecord]:
    """Собирает записи для таблицы departments из структуры 1С и routing_rules."""
    rules = rules or load_routing_rules()
    departments = build_departments_from_structure(
        rules,
        enterprise_path=enterprise_path,
        comparison_path=comparison_path,
        tz_topics_path=tz_topics_path,
    )
    directions = directions_by_code_from_rules(rules)

    enterprise_file = resolve_enterprise_positions_path(enterprise_path)
    enterprise = json.loads(enterprise_file.read_text(encoding="utf-8"))
    comparison_file = resolve_comparison_report_path(comparison_path)
    comparison = (
        json.loads(comparison_file.read_text(encoding="utf-8")) if comparison_file else None
    )
    tz_topics = load_tz_department_topics(tz_topics_path)
    tz_emails = load_tz_emails_by_code(enterprise, comparison=comparison)

    structure_by_code: dict[str, dict] = {}
    for row in enterprise.get("structure_departments_routing_codes") or []:
        code = str(row.get("code") or "").strip()
        if code:
            structure_by_code[code] = row

    records: list[DepartmentRecord] = []
    for dept in departments:
        code = dept.department_id
        structure = structure_by_code.get(code, {})
        emails = _all_emails_for_code(code, tz_topics=tz_topics, tz_emails=tz_emails)
        records.append(
            DepartmentRecord(
                code=code,
                name=dept.department_name,
                direction=directions.get(code),
                email=_primary_email_for_code(code, tz_topics=tz_topics, tz_emails=tz_emails),
                is_active=True,
                metadata={
                    "head_name": dept.head_name,
                    "responsibility": dept.responsibility,
                    "emails": emails,
                    "onec_name": str(structure.get("name") or "").strip() or None,
                    "path": str(structure.get("path") or "").strip() or None,
                    "keywords_count": len(dept.keywords),
                },
            )
        )

    return sorted(records, key=lambda item: item.code)
