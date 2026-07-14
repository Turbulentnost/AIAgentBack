"""Сохранение и применение коррекций маршрутизации (human-in-the-loop).

Запись в routing_corrections.json; дообучение Qdrant — routing.learning.learn_from_routing_correction.
"""

from __future__ import annotations

import json
import re
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from agent_pochta.config import PROJECT_ROOT
from agent_pochta.routing.normalize import normalize_email_address, normalize_text
from agent_pochta.services.routing_departments import load_routing_rules

_DEFAULT_CORRECTIONS_PATH = PROJECT_ROOT / "data" / "routing_corrections.json"

# Гибридное извлечение keywords (deterministic, reproducible):
# 1. Нормализованная тема письма (без Re:/Fwd:), если длина >= 4
# 2. Local-part получателя (например uk_omto4)
# 3. До 6 distinctive unigram/bigram из subject + body[:500]:
#    - subject bigram/trigram > subject unigram > body bigram > body unigram
#    - при наличии corpus (все entries) — TF same-dept vs other-depts
# 4. Фильтр junk: стоп-слова, email, фрагменты пунктуации, подписи

_CONTENT_KEYWORD_LIMIT = 6
_BODY_SNIPPET_LEN = 500
_MIN_UNIGRAM_LEN = 4

_STOPWORDS = {
    "этот",
    "этого",
    "этом",
    "этим",
    "который",
    "которые",
    "которой",
    "которого",
    "письмо",
    "письме",
    "просьба",
    "здравствуйте",
    "добрый",
    "день",
    "уважаем",
    "уважением",
    "благодарим",
    "спасибо",
    "пожалуйста",
    "коллеги",
    "данном",
    "тексте",
    "содержится",
    "сообщается",
    "отправка",
    "отправляю",
    "отправлены",
    "именно",
    "будет",
    "готов",
    "выдаче",
    "работу",
    "прошу",
    "please",
    "hello",
    "dear",
    "the",
    "and",
    "for",
    "from",
    "with",
    "re",
    "fw",
    "fwd",
}

_GENERIC_BODY_WORDS = {
    "приветствую",
    "уважаемый",
    "уважаемая",
    "менеджер",
    "директор",
    "специалист",
    "отправитель",
    "email",
    "телефон",
    "тел",
    "факс",
    "www",
    "http",
    "https",
}

_SUBJECT_PREFIX_RE = re.compile(
    r"^(?:re|fw|fwd|ответ|пересылка)\s*:\s*",
    re.IGNORECASE,
)
_TOKEN_EDGE_PUNCT_RE = re.compile(r'^["\'\(\[\{«]+|["\'\)\]\}»\.,:;!?]+$', re.UNICODE)
_EMAIL_RE = re.compile(r"[@\[\(]?[\w.+-]+@[\w.-]+\.[\w.-]+", re.IGNORECASE)
_JUNK_TOKEN_RE = re.compile(r"^[\W_]+$|[!]{2,}|\*\*")
_DIGIT_FRAGMENT_RE = re.compile(r"^\d+\s")


_WORD_TOKEN_RE = re.compile(r"[a-zA-Zа-яА-ЯёЁ0-9][a-zA-Zа-яА-ЯёЁ0-9_./-]*")


def _clean_token(token: str) -> str:
    return _TOKEN_EDGE_PUNCT_RE.sub("", token.strip())


def _strip_subject_prefix(subject: str) -> str:
    cleaned = subject or ""
    while True:
        next_value = _SUBJECT_PREFIX_RE.sub("", cleaned).strip()
        if next_value == cleaned:
            return next_value
        cleaned = next_value


def _recipient_local_part(recipient: str | None) -> str | None:
    if not recipient or "@" not in recipient:
        return None
    local = recipient.split("@", 1)[0].strip().lower()
    return local or None


def _is_useful_unigram(token: str) -> bool:
    if len(token) < _MIN_UNIGRAM_LEN or token in _STOPWORDS or token in _GENERIC_BODY_WORDS:
        return False
    if _EMAIL_RE.search(token) or _JUNK_TOKEN_RE.search(token):
        return False
    alnum = sum(1 for char in token if char.isalnum() or ("а" <= char.lower() <= "я") or char == "ё")
    return alnum >= max(3, len(token) // 2)


def _is_useful_phrase(phrase: str) -> bool:
    phrase = phrase.strip().lower()
    if len(phrase) < 6 or _EMAIL_RE.search(phrase) or _DIGIT_FRAGMENT_RE.match(phrase):
        return False
    words = phrase.split()
    if len(words) < 2:
        return False
    if all(word in _STOPWORDS or word in _GENERIC_BODY_WORDS for word in words):
        return False
    return True


def _dedupe_substring_keywords(keywords: list[str]) -> list[str]:
    """Убирает более короткие фразы, уже покрытые более длинными keywords."""
    result: list[str] = []
    for keyword in keywords:
        if any(keyword != other and keyword in other for other in result):
            continue
        result = [other for other in result if other == keyword or other not in keyword]
        result.append(keyword)
    return result


def _is_useful_keyword(token: str) -> bool:
    """Публичная проверка keyword (unigram или phrase)."""
    token = token.strip().lower()
    if not token:
        return False
    if " " in token:
        return _is_useful_phrase(token)
    return _is_useful_unigram(token)


def _word_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for match in _WORD_TOKEN_RE.finditer(text.lower()):
        token = _clean_token(match.group(0))
        if token:
            tokens.append(token)
    return tokens


def _phrase_candidates(tokens: list[str], *, max_n: int = 3) -> list[str]:
    phrases: list[str] = []
    for n in range(max_n, 0, -1):
        for idx in range(len(tokens) - n + 1):
            chunk = tokens[idx : idx + n]
            if n == 1:
                if _is_useful_unigram(chunk[0]):
                    phrases.append(chunk[0])
            else:
                phrase = " ".join(chunk)
                if _is_useful_phrase(phrase):
                    phrases.append(phrase)
    return phrases


def _body_snippet(body: str) -> str:
    snippet = (body or "")[:_BODY_SNIPPET_LEN]
    return normalize_text(snippet)


def _department_catalog_keywords(department_id: str | None) -> set[str]:
    if not department_id:
        return set()
    rules = load_routing_rules()
    catalog: set[str] = set()
    for rule in rules.get("email_keyword_rules") or []:
        if rule.get("department_id") != department_id:
            continue
        for kw in rule.get("keywords") or []:
            normalized = normalize_text(str(kw))
            if normalized:
                catalog.add(normalized)
                catalog.update(_word_tokens(normalized))
    return catalog


def _infer_legacy_subject_body(entry: dict) -> tuple[str, str]:
    """Восстанавливает subject/body из legacy keywords (до хранения subject в JSON)."""
    keywords = [str(kw) for kw in entry.get("keywords") or [] if kw]
    if not keywords:
        return "", ""
    subject = keywords[0]
    if subject.lower().startswith(("re:", "fw:", "fwd:")):
        subject = _strip_subject_prefix(subject)
    body = " ".join(keywords[1:3])
    return subject, body


def _entry_source_text(entry: dict) -> tuple[str, str]:
    subject = str(entry.get("subject") or "").strip()
    body = str(entry.get("body") or "").strip()
    if subject or body:
        return subject, body
    return _infer_legacy_subject_body(entry)


def _collect_candidate_tokens(subject: str, body: str) -> list[tuple[str, int]]:
    """Возвращает (token, priority) — меньше priority = выше приоритет."""
    subject_clean = _strip_subject_prefix(subject)
    subject_norm = normalize_text(subject_clean)
    subject_tokens = _word_tokens(subject_norm)
    body_norm = _body_snippet(body)
    body_tokens = _word_tokens(body_norm)

    candidates: list[tuple[str, int]] = []
    seen: set[str] = set()

    def _add(token: str, priority: int) -> None:
        token = token.strip().lower()
        if not token or token in seen:
            return
        if not _is_useful_keyword(token):
            return
        seen.add(token)
        candidates.append((token, priority))

    for phrase in _phrase_candidates(subject_tokens, max_n=3):
        _add(phrase, 1 if " " in phrase else 2)
    for phrase in _phrase_candidates(body_tokens, max_n=2):
        _add(phrase, 3 if " " in phrase else 4)

    return candidates


def _build_distinctive_scores(
    corpus_entries: list[dict],
    *,
    exclude_entry_id: str | None = None,
) -> dict[str, float]:
    dept_counts: dict[str, Counter[str]] = defaultdict(Counter)
    other_counts: Counter[str] = Counter()

    for entry in corpus_entries:
        if exclude_entry_id and entry.get("id") == exclude_entry_id:
            continue
        department_id = str(entry.get("department_id") or "")
        if not department_id:
            continue
        subject, body = _entry_source_text(entry)
        for token, _priority in _collect_candidate_tokens(subject, body):
            dept_counts[department_id][token] += 1
            other_counts[token] += 1

    scores: dict[str, float] = {}
    for department_id, token_counts in dept_counts.items():
        for token, same_count in token_counts.items():
            other = sum(
                count
                for dept, counter in dept_counts.items()
                if dept != department_id
                for token_key, count in counter.items()
                if token_key == token
            )
            scores[(department_id, token)] = same_count / (other + 1)
    return scores


def extract_correction_keywords(
    subject: str,
    body: str,
    *,
    recipient: str | None = None,
    department_id: str | None = None,
    corpus_entries: list[dict] | None = None,
    content_limit: int = _CONTENT_KEYWORD_LIMIT,
) -> list[str]:
    """Извлекает keywords для коррекции по детерминированным правилам."""
    keywords: list[str] = []
    seen: set[str] = set()

    def _add(value: str, *, force: bool = False) -> None:
        token = _clean_token(value.strip().lower())
        if not token or token in seen:
            return
        if not force and not _is_useful_keyword(token):
            return
        seen.add(token)
        keywords.append(token)

    subject_clean = _strip_subject_prefix(subject or "")
    subject_norm = normalize_text(_clean_token(subject_clean))
    if subject_norm and len(subject_norm) >= 4:
        _add(subject_norm, force=True)

    local_part = _recipient_local_part(recipient)
    if local_part:
        _add(local_part, force=True)

    candidates = _collect_candidate_tokens(subject, body)
    catalog = _department_catalog_keywords(department_id)

    distinctive: dict[str, float] = {}
    if corpus_entries and department_id:
        raw_scores = _build_distinctive_scores(corpus_entries)
        distinctive = {
            token: raw_scores.get((department_id, token), 0.0)
            for token, _priority in candidates
        }

    def _sort_key(item: tuple[str, int]) -> tuple[float, int, str]:
        token, priority = item
        catalog_boost = 1 if token in catalog or any(part in catalog for part in token.split()) else 0
        return (-distinctive.get(token, 0.0), -catalog_boost, priority, token)

    ranked = sorted(candidates, key=_sort_key)
    for token, _priority in ranked:
        if len(keywords) >= content_limit + (1 if subject_norm else 0) + (1 if local_part else 0):
            break
        _add(token)

    return _dedupe_substring_keywords(keywords)


def resolve_corrections_path(path: Path | str | None = None) -> Path:
    if path:
        return Path(path)
    from agent_pochta.config import get_settings

    custom = get_settings().routing_corrections_path.strip()
    if custom:
        return Path(custom)
    return _DEFAULT_CORRECTIONS_PATH


def _empty_store() -> dict:
    return {"version": "1.0", "entries": []}


def load_corrections(path: Path | str | None = None) -> dict:
    corrections_path = resolve_corrections_path(path)
    if not corrections_path.is_file():
        return _empty_store()
    with corrections_path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        return _empty_store()
    data.setdefault("version", "1.0")
    data.setdefault("entries", [])
    return data


def save_corrections(store: dict, path: Path | str | None = None) -> Path:
    corrections_path = resolve_corrections_path(path)
    corrections_path.parent.mkdir(parents=True, exist_ok=True)
    with corrections_path.open("w", encoding="utf-8") as fh:
        json.dump(store, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return corrections_path


def _correction_fingerprint(entry: dict) -> tuple[str, str, str]:
    return (
        str(entry.get("sender_email") or "").lower().strip(),
        str(entry.get("recipient") or "").lower().strip(),
        str(entry.get("department_id") or ""),
    )


def migrate_routing_corrections_store(
    path: Path | str | None = None,
    *,
    dry_run: bool = False,
) -> dict:
    """Удаляет message_id, сохраняет subject, пересчитывает keywords для всех entries."""
    store = load_corrections(path)
    entries = list(store.get("entries") or [])
    updated: list[dict] = []

    for entry in entries:
        migrated = dict(entry)
        migrated.pop("message_id", None)

        subject, body = _entry_source_text(migrated)
        subject_clean = _strip_subject_prefix(subject)
        if subject_clean:
            migrated["subject"] = normalize_text(_clean_token(subject_clean))
        migrated.pop("body", None)

        migrated["keywords"] = extract_correction_keywords(
            subject,
            body,
            recipient=migrated.get("recipient"),
            department_id=migrated.get("department_id"),
            corpus_entries=entries,
        )
        updated.append(migrated)

    if dry_run:
        return {
            "entries": len(updated),
            "message_ids_removed": sum(1 for entry in entries if entry.get("message_id")),
            "dry_run": True,
        }

    store["entries"] = updated
    save_corrections(store, path)
    from agent_pochta.routing.engine import reset_route_engine

    reset_route_engine()
    return {
        "entries": len(updated),
        "message_ids_removed": sum(1 for entry in entries if entry.get("message_id")),
        "dry_run": False,
    }


def save_routing_correction(
    *,
    sender_email: str,
    recipient: str | None,
    subject: str,
    body: str,
    department_id: str,
    department_name: str,
    original_department_id: str | None = None,
    original_department_name: str | None = None,
    path: Path | str | None = None,
) -> dict:
    store = load_corrections(path)
    rules = load_routing_rules()
    normalized_recipient = (
        normalize_email_address(recipient or "", rules.get("email_aliases")) if recipient else None
    )
    subject_clean = _strip_subject_prefix(subject)
    corpus_entries = list(store.get("entries") or [])
    entry = {
        "id": str(uuid.uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "sender_email": sender_email.lower().strip(),
        "recipient": normalized_recipient,
        "subject": normalize_text(_clean_token(subject_clean)) if subject_clean else "",
        "keywords": extract_correction_keywords(
            subject,
            body,
            recipient=normalized_recipient,
            department_id=department_id,
            corpus_entries=corpus_entries,
        ),
        "department_id": department_id,
        "department_name": department_name,
        "original_department_id": original_department_id,
        "original_department_name": original_department_name,
    }
    store["entries"].append(entry)
    save_corrections(store, path)
    from agent_pochta.routing.engine import reset_route_engine

    reset_route_engine()
    return entry


def find_correction_match(
    *,
    recipient: str,
    sender_email: str,
    subject: str,
    body: str,
    path: Path | str | None = None,
) -> dict | None:
    store = load_corrections(path)
    entries = store.get("entries") or []
    if not entries:
        return None

    rules = load_routing_rules()
    recipient = normalize_email_address(recipient, rules.get("email_aliases"))
    sender_email = sender_email.lower().strip()
    text = normalize_text(f"{subject} {body}")

    best: tuple[int, dict] | None = None
    for entry in reversed(entries):
        score = 0
        entry_recipient = entry.get("recipient")
        if entry_recipient:
            if entry_recipient != recipient:
                continue
            score += 3
        entry_sender = (entry.get("sender_email") or "").lower().strip()
        if entry_sender:
            if entry_sender != sender_email:
                continue
            score += 2
        keywords = entry.get("keywords") or []
        keyword_hits = sum(1 for kw in keywords if kw and kw in text)
        if keywords and keyword_hits == 0 and not entry_recipient and not entry_sender:
            continue
        score += keyword_hits
        if score <= 0:
            continue
        if best is None or score > best[0]:
            best = (score, entry)

    return best[1] if best else None
