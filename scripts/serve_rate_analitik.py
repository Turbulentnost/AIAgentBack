"""Локальный UI RAG-разметки imap_cards_1000.jsonl.

  py -3 scripts/serve_rate_analitik.py
  → http://0.0.0.0:8765 (доступен в локальной сети)

Для каждой карточки UI запрашивает /api/cards/<index>/candidates: spam-learning,
department RAG, rule-router и безопасные allowlist/enums fallback. Qdrant и LLM
не обязательны: при недоступности Qdrant остаются rule-router и allowlist.
"""
from __future__ import annotations

import json
import socket
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
UI_DIR = ROOT / "tools" / "rate_analitik_ui"
DEFAULT_JSONL = ROOT / "data" / "stats" / "imap_cards_1000.jsonl"
HOST = "0.0.0.0"
PORT = 8765

_cards: list[dict] = []
_jsonl_path = DEFAULT_JSONL
_default_imap_mailbox_cache: str | None = None
_imap_email_cache: dict[tuple[int, bool], object] = {}
INFO_RECIPIENT_Q = "info"


def _candidate_source_rank(source: object) -> int:
    """Prefer RAG evidence over fallbacks when scores are equal."""
    value = str(source or "").lower()
    if value.startswith("rag"):
        return 0
    if value in {"rule_router", "rule"}:
        return 1
    if value == "current_card":
        return 2
    if value == "allowlist":
        return 3
    return 4


def _candidate_sort_key(candidate: dict) -> tuple[float, int, str, str]:
    """Order strictly by numeric score, then source reliability."""
    score = candidate.get("score")
    try:
        numeric_score = float(score) if score is not None else -1.0
    except (TypeError, ValueError):
        numeric_score = -1.0
    return (
        -numeric_score,
        _candidate_source_rank(candidate.get("source")),
        str(candidate.get("code") or candidate.get("id") or ""),
        str(candidate.get("name") or ""),
    )


def _sort_candidates(candidates: list[dict]) -> list[dict]:
    return sorted(candidates, key=_candidate_sort_key)


def _mark_current_candidate(candidates: list[dict], value: str) -> None:
    """Tag the existing current option so source tie-breaking remains stable."""
    for candidate in candidates:
        if str(candidate.get("id") or "") == value:
            candidate["source"] = "current_card"
            return


def load_cards(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def save_cards(path: Path, cards: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for card in cards:
            fh.write(json.dumps(card, ensure_ascii=False) + "\n")


def _matches_info_recipient(card: dict) -> bool:
    """Как в IncomingMail: «Кому» содержит info (без учёта регистра)."""
    recipient = str(card.get("recipient") or "").lower()
    return INFO_RECIPIENT_Q in recipient if recipient else False


def _default_imap_mailbox() -> str:
    global _default_imap_mailbox_cache
    if _default_imap_mailbox_cache:
        return _default_imap_mailbox_cache
    patterns_path = ROOT / "data" / "stats" / "imap_spam_patterns_1000.json"
    if patterns_path.exists():
        try:
            mailbox = str(json.loads(patterns_path.read_text(encoding="utf-8")).get("mailbox") or "").strip()
            if mailbox:
                _default_imap_mailbox_cache = mailbox
                return mailbox
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
    from agent_pochta.config import get_settings

    mailboxes = get_settings().mailbox_list
    _default_imap_mailbox_cache = mailboxes[0] if mailboxes else "test_ii@turbo-don.ru"
    return _default_imap_mailbox_cache


def _mailboxes_to_try(card: dict) -> list[str]:
    from agent_pochta.config import get_settings

    ordered: list[str] = []
    for mailbox in (
        str(card.get("imap_mailbox") or "").strip(),
        _default_imap_mailbox(),
        str(card.get("recipient") or "").strip(),
        *get_settings().mailbox_list,
    ):
        if not mailbox or "@" not in mailbox:
            continue
        key = mailbox.lower()
        if key not in {item.lower() for item in ordered}:
            ordered.append(mailbox)
    return ordered


def _fetch_card_email(card_idx: int, card: dict, *, load_oversized: bool = False):
    cache_key = (card_idx, load_oversized)
    if cache_key in _imap_email_cache:
        return _imap_email_cache[cache_key]

    message_id = str(card.get("message_id") or "").strip()
    if not message_id:
        return None

    from agent_pochta.config import get_settings
    from agent_pochta.imap.client import ImapMailboxClient, resolve_imap_credentials
    from agent_pochta.services.vault import StubVaultClient

    settings = get_settings()
    vault = StubVaultClient()
    for mailbox in _mailboxes_to_try(card):
        try:
            credentials = resolve_imap_credentials(mailbox, vault)
            client = ImapMailboxClient(mailbox, credentials, settings=settings)
            email = client.fetch_by_message_id(
                message_id,
                load_oversized_attachments=load_oversized,
                timeout_sec=settings.imap_download_timeout_sec,
            )
            if email is not None:
                _imap_email_cache[cache_key] = email
                return email
        except Exception:
            continue
    return None


def _fetch_card_attachment(card_idx: int, card: dict, att_idx: int):
    """Байты одного вложения: кэш → частичный IMAP → fallback RFC822."""
    from agent_pochta.attachments.cache import (
        attachment_cache_key,
        get_cached_attachment,
        put_cached_attachment,
    )
    from agent_pochta.config import get_settings
    from agent_pochta.imap.client import ImapMailboxClient, resolve_imap_credentials
    from agent_pochta.services.vault import StubVaultClient

    message_id = str(card.get("message_id") or "").strip()
    if not message_id:
        return None

    email = _fetch_card_email(card_idx, card, load_oversized=False)
    filename = f"attachment-{att_idx + 1}"
    if email is not None and 0 <= att_idx < len(email.attachments or []):
        att = email.attachments[att_idx]
        filename = att.filename or filename

    settings = get_settings()
    vault = StubVaultClient()
    for mailbox in _mailboxes_to_try(card):
        cache_key = attachment_cache_key(mailbox, message_id, att_idx, filename)
        cached = get_cached_attachment(cache_key)
        if cached is not None:
            return cached.content, cached.mime_type, cached.filename

        try:
            credentials = resolve_imap_credentials(mailbox, vault)
            client = ImapMailboxClient(mailbox, credentials, settings=settings)
            fetched = client.fetch_attachment_bytes(
                message_id,
                filename=filename,
                attachment_index=att_idx,
                timeout_sec=settings.imap_download_timeout_sec,
            )
            if fetched is None:
                continue
            content, mime_type, resolved_name = fetched
            put_cached_attachment(
                cache_key,
                content=content,
                mime_type=mime_type,
                filename=resolved_name,
            )
            return content, mime_type, resolved_name
        except Exception:
            continue
    return None


def _attachment_items(card_idx: int, card: dict) -> tuple[list[dict], str | None]:
    email = _fetch_card_email(card_idx, card, load_oversized=False)
    if email is None:
        if not str(card.get("message_id") or "").strip():
            return [], "no_message_id"
        return [], "not_in_mailbox"
    items = [
        {
            "index": index,
            "filename": att.filename or f"attachment-{index + 1}",
            "mime_type": att.mime_type or "application/octet-stream",
            "size_bytes": att.size_bytes,
            "has_content": att.content is not None,
        }
        for index, att in enumerate(email.attachments or [])
    ]
    return items, None


def _card_passes_filters(
    card: dict,
    *,
    filter_name: str,
    info_only: bool,
    q: str,
) -> bool:
    rate = str(card.get("rate_analitik") or "").strip()
    decision = (card.get("spam") or {}).get("decision") or ""
    if filter_name == "unrated" and rate:
        return False
    if filter_name == "rated" and not rate:
        return False
    if filter_name == "spam" and decision != "spam":
        return False
    if filter_name == "not_spam" and decision == "spam":
        return False
    if filter_name == "ok" and rate.lower() != "ok":
        return False
    if filter_name == "fix" and not rate.lower().startswith("fix"):
        return False
    if filter_name == "info" and not _matches_info_recipient(card):
        return False
    if info_only and not _matches_info_recipient(card):
        return False
    blob = " ".join(
        [
            str(card.get("sender") or ""),
            str(card.get("recipient") or ""),
            str(card.get("subject") or ""),
            str(card.get("summary_ru") or ""),
            str((card.get("routing") or {}).get("department_name") or ""),
            rate,
        ]
    ).lower()
    return not q or q in blob


def _email_from_card(card: dict):
    """Build the minimal domain model needed by existing spam/routing helpers."""
    from agent_pochta.schemas import EmailMessage

    raw_date = str(card.get("received_at") or "")
    try:
        received_at = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
    except ValueError:
        received_at = datetime.now(timezone.utc)
    recipient = str(card.get("recipient") or "")
    return EmailMessage(
        message_id=str(card.get("message_id") or ""),
        mailbox=recipient,
        routing_recipient=recipient or None,
        sender_email=str(card.get("sender") or ""),
        subject=str(card.get("subject") or ""),
        body_text=str(card.get("body_excerpt") or ""),
        received_at=received_at,
        to=[recipient] if recipient else [],
    )


def _candidate_snippet(text: str, keywords: list[str]) -> str:
    text_l = text.lower()
    for keyword in keywords:
        pos = text_l.find(keyword.lower())
        if pos >= 0:
            return text[max(0, pos - 70) : pos + len(keyword) + 140].strip()
    return ""


def _spam_candidates(card: dict) -> tuple[list[dict], list[str]]:
    """Return the same evidence layers that feed the spam decision, without an LLM."""
    from agent_pochta.rules.spam_learning import _collect_learned_entries, _entry_matches_email
    from agent_pochta.rules.spam_rules import check_rule_spam

    email = _email_from_card(card)
    warnings: list[str] = []
    candidates: list[dict] = []
    current = card.get("spam") or {}
    candidates.append(
        {
            "value": current.get("decision") == "spam",
            "label": "spam" if current.get("decision") == "spam" else "not_spam",
            "score": 1.0 if current.get("decision") == "spam" else 0.0,
            "source": "current_card",
            "reason": str(current.get("reason") or current.get("layer") or "Текущий результат карточки"),
        }
    )
    try:
        rule = check_rule_spam(email)
        if rule is not None:
            candidates.append(
                {
                    "value": bool(rule.is_spam),
                    "label": "spam" if rule.is_spam else "not_spam",
                    "score": float(rule.confidence),
                    "source": "rule",
                    "reason": rule.reason,
                    "rule_hit": rule.rule_hit,
                }
            )
        for entry in _collect_learned_entries():
            if not _entry_matches_email(
                sender_email=email.sender_email, subject=email.subject, body=email.body_text, entry=entry
            ):
                continue
            keywords = list(entry.get("keywords") or [])
            keyword_hits = [kw for kw in keywords if kw and kw.lower() in f"{email.subject} {email.body_text}".lower()]
            candidates.append(
                {
                    "value": entry.get("label") == "spam",
                    "label": str(entry.get("label") or "not_spam"),
                    "score": 3 + len(keyword_hits),
                    "source": "rag_learning",
                    "reason": str(entry.get("reason") or ""),
                    "keywords": keyword_hits,
                    "snippet": _candidate_snippet(email.body_text, keyword_hits),
                }
            )
    except Exception as exc:
        warnings.append(f"Spam RAG недоступен: {exc}")
    return candidates, warnings


def _routing_candidates(card: dict) -> tuple[dict, list[str]]:
    """Build department candidates via the production RAG interface plus safe fallbacks."""
    from agent_pochta.config import get_settings
    from agent_pochta.routing import RouteEngine, route_email
    from agent_pochta.routing.recipients import build_routing_search_text
    from agent_pochta.routing.organizations import list_organizations_for_ui
    from agent_pochta.routing.process_type import VALID_PROCESS_TYPES
    from agent_pochta.services.rag import score_department_keywords
    from agent_pochta.services.rag_qdrant import build_rag_service
    from agent_pochta.services.routing_departments import (
        filter_departments_for_ui_llm,
        load_ui_department_allowlist,
    )

    email = _email_from_card(card)
    routing = card.get("routing") or {}
    text = str(card.get("body_excerpt") or "")
    search_text = build_routing_search_text(
        recipient=email.routing_recipient or "",
        subject=email.subject,
        combined_text=text,
    )
    warnings: list[str] = []
    candidates: list[dict] = []
    try:
        rag = build_rag_service(get_settings())
        found = filter_departments_for_ui_llm(
            rag.search_departments(search_text, top_k=30, recipient=email.routing_recipient)
        )
        for dept in found[:8]:
            hits = [kw for kw in dept.keywords if kw.lower() in search_text.lower()][:8]
            candidates.append(
                {
                    "code": dept.department_id,
                    "name": dept.department_name,
                    "score": score_department_keywords(dept, search_text, recipient=email.routing_recipient),
                    "source": "rag_qdrant" if get_settings().rag_backend == "qdrant" else "rag_stub",
                    "reason": dept.responsibility,
                    "keywords": hits,
                    "snippet": _candidate_snippet(search_text, hits),
                }
            )
        if hasattr(rag, "close"):
            rag.close()
    except Exception as exc:
        warnings.append(f"Department RAG недоступен: {exc}")

    try:
        decision = route_email(email, combined_text=text, recipient=email.routing_recipient, engine=RouteEngine.load())
        for service in decision.services[:3]:
            if not any(item["code"] == service.code for item in candidates):
                candidates.append(
                    {
                        "code": service.code,
                        "name": service.name,
                        "score": decision.confidence_score,
                        "source": "rule_router",
                        "reason": service.reasoning,
                        "keywords": list(routing.get("matching_keywords") or []),
                    }
                )
    except Exception as exc:
        warnings.append(f"Rule-router недоступен: {exc}")

    current_code = str(routing.get("department_code") or "")
    if current_code and not any(item["code"] == current_code for item in candidates):
        candidates.append({
            "code": current_code, "name": str(routing.get("department_name") or current_code),
            "score": None, "source": "current_card", "reason": "Текущий маршрут карточки",
        })
    if not candidates:
        for code, name in load_ui_department_allowlist().items():
            candidates.append({"code": code, "name": name, "score": None, "source": "allowlist"})

    orgs = list_organizations_for_ui()
    current_org = str(routing.get("organization") or "")
    if current_org and not any(item["id"] == current_org for item in orgs):
        orgs.append({"id": current_org, "name": current_org, "source": "current_card"})
    elif current_org:
        _mark_current_candidate(orgs, current_org)
    directions = []
    for value in dict.fromkeys(
        [str(routing.get("direction") or "")] + [item["id"] for item in orgs if item["id"] != "НП"] + ["КС"]
    ):
        if value:
            directions.append(
                {
                    "id": value,
                    "name": value,
                    "source": "current_card" if value == str(routing.get("direction") or "") else "routing_enum",
                }
            )
    processes = [
        {
            "id": value,
            "name": value,
            "source": "current_card" if value == str(routing.get("process") or "") else "process_enum",
        }
        for value in sorted(VALID_PROCESS_TYPES)
    ]
    return {
        "organization": _sort_candidates(orgs),
        "direction": _sort_candidates(directions),
        "department": _sort_candidates(candidates),
        "process": _sort_candidates(processes),
    }, warnings


def build_card_candidates(card: dict) -> dict:
    spam, spam_warnings = _spam_candidates(card)
    routing, routing_warnings = _routing_candidates(card)
    return {
        "context": {
            "current_spam": card.get("spam") or {},
            "current_routing": card.get("routing") or {},
            "subject": card.get("subject") or "",
        },
        "spam": _sort_candidates(spam),
        "routing": routing,
        "warnings": spam_warnings + routing_warnings,
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:  # quieter
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: object) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(code, raw, "application/json; charset=utf-8")

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8") or "{}")

    def do_GET(self) -> None:
        global _cards
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            file_path = UI_DIR / "index.html"
            self._send(200, file_path.read_bytes(), "text/html; charset=utf-8")
            return
        if path == "/styles.css":
            self._send(200, (UI_DIR / "styles.css").read_bytes(), "text/css; charset=utf-8")
            return
        if path == "/app.js":
            self._send(200, (UI_DIR / "app.js").read_bytes(), "application/javascript; charset=utf-8")
            return

        if path == "/api/meta":
            rated = sum(1 for c in _cards if str(c.get("rate_analitik") or "").strip())
            spam = sum(1 for c in _cards if (c.get("spam") or {}).get("decision") == "spam")
            info_count = sum(1 for c in _cards if _matches_info_recipient(c))
            self._json(
                200,
                {
                    "path": str(_jsonl_path),
                    "total": len(_cards),
                    "rated": rated,
                    "unrated": len(_cards) - rated,
                    "spam": spam,
                    "info": info_count,
                },
            )
            return

        if path.startswith("/api/cards/") and "/attachments" in path:
            parts = [unquote(part) for part in path.strip("/").split("/")]
            # api cards idx attachments [att_idx] [text]
            if len(parts) < 4 or parts[0] != "api" or parts[1] != "cards" or parts[3] != "attachments":
                self._json(404, {"error": "not found"})
                return
            try:
                idx = int(parts[2])
            except ValueError:
                self._json(400, {"error": "bad index"})
                return
            if idx < 0 or idx >= len(_cards):
                self._json(404, {"error": "not found"})
                return
            card = _cards[idx]
            if len(parts) == 4:
                items, error = _attachment_items(idx, card)
                self._json(200, {"items": items, "error": error, "mailbox": _default_imap_mailbox()})
                return
            try:
                att_idx = int(parts[4])
            except ValueError:
                self._json(400, {"error": "bad attachment index"})
                return
            items, list_error = _attachment_items(idx, card)
            if list_error:
                self._json(404, {"error": list_error})
                return
            if att_idx < 0 or att_idx >= len(items):
                self._json(404, {"error": "attachment_not_found"})
                return
            fetched = _fetch_card_attachment(idx, card, att_idx)
            if fetched is None:
                self._json(404, {"error": "not_in_mailbox"})
                return
            content, mime_type, filename = fetched
            if len(parts) >= 6 and parts[5] == "text":
                from agent_pochta.attachments.extract import extract_attachment_text
                from agent_pochta.schemas import Attachment

                attachment = Attachment(
                    filename=filename,
                    mime_type=mime_type,
                    size_bytes=len(content),
                    content=content,
                )
                text, ocr_used = extract_attachment_text(attachment)
                self._json(
                    200,
                    {
                        "text": text or "",
                        "ocr_used": ocr_used,
                        "filename": filename,
                    },
                )
                return
            if not content:
                self._json(404, {"error": "attachment_unavailable"})
                return
            from agent_pochta.attachments.download import content_disposition_header

            body = content
            self.send_response(200)
            self.send_header("Content-Type", mime_type or "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Disposition", content_disposition_header(filename))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/cards":
            qs = parse_qs(parsed.query)
            filter_name = (qs.get("filter") or ["all"])[0]
            info_only = (qs.get("info_only") or ["0"])[0].strip().lower() in {"1", "true", "yes"}
            q = (qs.get("q") or [""])[0].strip().lower()
            items = []
            for idx, card in enumerate(_cards):
                if not _card_passes_filters(card, filter_name=filter_name, info_only=info_only, q=q):
                    continue
                items.append(
                    {
                        "index": idx,
                        "sender": card.get("sender"),
                        "recipient": card.get("recipient"),
                        "subject": card.get("subject"),
                        "spam_decision": (card.get("spam") or {}).get("decision") or "",
                        "department": (card.get("routing") or {}).get("department_name"),
                        "department_code": (card.get("routing") or {}).get("department_code"),
                        "organization": (card.get("routing") or {}).get("organization"),
                        "rate_analitik": str(card.get("rate_analitik") or "").strip(),
                        "received_at": card.get("received_at"),
                        "info_recipient": _matches_info_recipient(card),
                    }
                )
            self._json(200, {"items": items})
            return

        if path.startswith("/api/cards/"):
            suffix = path.removeprefix("/api/cards/")
            if suffix.endswith("/candidates"):
                suffix = suffix.removesuffix("/candidates").rstrip("/")
                try:
                    idx = int(suffix)
                except ValueError:
                    self._json(400, {"error": "bad index"})
                    return
                if idx < 0 or idx >= len(_cards):
                    self._json(404, {"error": "not found"})
                    return
                self._json(200, build_card_candidates(_cards[idx]))
                return
            try:
                idx = int(suffix)
            except ValueError:
                self._json(400, {"error": "bad index"})
                return
            if idx < 0 or idx >= len(_cards):
                self._json(404, {"error": "not found"})
                return
            self._json(200, {"index": idx, "card": _cards[idx]})
            return

        self._json(404, {"error": "not found"})

    def do_PATCH(self) -> None:
        global _cards
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/cards/"):
            self._json(404, {"error": "not found"})
            return
        try:
            idx = int(parsed.path.rsplit("/", 1)[-1])
        except ValueError:
            self._json(400, {"error": "bad index"})
            return
        if idx < 0 or idx >= len(_cards):
            self._json(404, {"error": "not found"})
            return
        body = self._read_json()
        if "rate_analitik" not in body and "operator_choice" not in body:
            self._json(400, {"error": "rate_analitik or operator_choice required"})
            return
        if "rate_analitik" in body:
            _cards[idx]["rate_analitik"] = str(body.get("rate_analitik") or "")
        if "operator_choice" in body:
            choice = body["operator_choice"]
            if not isinstance(choice, dict):
                self._json(400, {"error": "operator_choice must be an object"})
                return
            _cards[idx]["operator_choice"] = choice
        save_cards(_jsonl_path, _cards)
        self._json(200, {"ok": True, "index": idx, "card": _cards[idx]})

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, PATCH, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


def _local_urls(port: int) -> list[str]:
    urls = [f"http://127.0.0.1:{port}/"]
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if ip.startswith("127."):
                continue
            url = f"http://{ip}:{port}/"
            if url not in urls:
                urls.append(url)
    except OSError:
        pass
    return urls


def main() -> int:
    global _cards, _jsonl_path
    path_arg = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_JSONL
    _jsonl_path = path_arg if path_arg.is_absolute() else ROOT / path_arg
    if not _jsonl_path.exists():
        print(f"JSONL not found: {_jsonl_path}")
        return 1
    if not UI_DIR.exists():
        print(f"UI dir not found: {UI_DIR}")
        return 1
    _cards = load_cards(_jsonl_path)
    print(f"Loaded {len(_cards)} cards from {_jsonl_path}")
    for url in _local_urls(PORT):
        print(f"Open {url}")
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
