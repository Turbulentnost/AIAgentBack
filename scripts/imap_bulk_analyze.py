"""IMAP bulk: ~1000 newest mails → spam mine/Qdrant → RuleRouter XML cards + rate_analitik.

Offline, no Celery, no LLM. Usage:
  py -3 scripts/imap_bulk_analyze.py [--limit 1000] [--mailbox test_ii@turbo-don.ru]
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import ssl
import sys
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from imapclient import IMAPClient

from agent_pochta.config import get_settings
from agent_pochta.imap.client import resolve_imap_credentials
from agent_pochta.imap.parser import parse_raw_email
from agent_pochta.routing.corrections import extract_spam_learning_keywords
from agent_pochta.routing.engine import reset_route_engine, route_email
from agent_pochta.routing.xml_builder import validate_xml_document
from agent_pochta.rules.spam_context import trusted_sender_pass
from agent_pochta.rules.spam_learning import (
    check_learned_spam_decision,
    load_spam_learning,
    reason_indicates_not_spam,
    resync_spam_learning_to_qdrant,
    save_spam_learning,
)
from agent_pochta.rules.spam_rules import check_rule_spam
from agent_pochta.routing.spam_tz import check_tz_spam
from agent_pochta.schemas import EmailMessage
from agent_pochta.services.routing_departments import (
    load_onec_department_names_map,
    load_ui_department_allowlist,
)
from agent_pochta.services.vault import StubVaultClient

_COMPANY_RE = re.compile(
    r"(?:ооо|ао|пао|зао|ип|оао)\s*[«\"]?[\w\s.\-]{2,80}[»\"]?",
    re.IGNORECASE,
)
_BUSINESS_DOMAINS = (
    "gazprom",
    "rosneft",
    "sibur",
    "etpgpb",
    "roseltorg",
    "interrao",
    "greenatom",
    "mts.ru",
    "sber",
    "vtb",
    "alfabank",
    "gazprombank",
    "dellin",
    "pecom",
    "cdek",
    "russianpost",
    "zakupki",
    "srm-noreply",
    "notify.sibur",
    "turbo-don",
)


def _fetch_newest(mailbox: str, limit: int) -> list[EmailMessage]:
    settings = get_settings()
    creds = resolve_imap_credentials(mailbox, StubVaultClient())
    context = ssl.create_default_context()
    client = IMAPClient(
        settings.imap_host,
        port=settings.imap_port,
        ssl=True,
        ssl_context=context,
        timeout=settings.imap_connect_timeout_sec,
    )
    print(f"  connecting {settings.imap_host}:{settings.imap_port} as {creds.username}", flush=True)
    client.login(creds.username, creds.password)
    try:
        client.select_folder("INBOX", readonly=True)
        # Prefer UID range of highest UIDs — ALL search can hang on huge mailboxes.
        status = client.folder_status("INBOX", ["UIDNEXT", "MESSAGES"])
        uidnext = int(status.get(b"UIDNEXT") or status.get("UIDNEXT") or 1)
        messages = int(status.get(b"MESSAGES") or status.get("MESSAGES") or 0)
        print(f"  INBOX messages={messages} uidnext={uidnext}", flush=True)
        start_uid = max(1, uidnext - max(limit * 2, limit + 50))
        uids = client.search(["UID", f"{start_uid}:{uidnext - 1}"])
        if not uids:
            print("  UID range empty, fallback ALL (may be slow)", flush=True)
            uids = client.search(["ALL"])
        newest = sorted(uids, reverse=True)[:limit]
        print(f"  selected {len(newest)} UIDs", flush=True)
        batch_size = max(1, int(settings.imap_fetch_batch_size or 20))
        emails: list[EmailMessage] = []
        for offset in range(0, len(newest), batch_size):
            batch = newest[offset : offset + batch_size]
            fetch_data = client.fetch(batch, ["RFC822"])
            for uid in batch:
                item = fetch_data.get(uid)
                if not item or b"RFC822" not in item:
                    continue
                emails.append(parse_raw_email(item[b"RFC822"], mailbox))
            print(f"  fetched {min(offset + batch_size, len(newest))}/{len(newest)}", flush=True)
        return emails
    finally:
        try:
            client.logout()
        except Exception:
            pass


def _classify_spam(
    email: EmailMessage,
    settings,
    *,
    learned_entries: list[dict] | None = None,
) -> dict:
    rule = check_rule_spam(email)
    if rule is not None:
        return {
            "decision": "spam",
            "layer": "rules",
            "reason": rule.reason,
            "rule_hit": rule.rule_hit,
        }

    if learned_entries is not None:
        learned = _match_learned_local(email, learned_entries)
    else:
        learned = check_learned_spam_decision(email)
    if learned is not None:
        if learned.is_spam:
            return {
                "decision": "spam",
                "layer": "rag_learning",
                "reason": (learned.spam_result.reason if learned.spam_result else ""),
                "rule_hit": "learned_spam_pattern",
            }
        return {
            "decision": "not_spam",
            "layer": "rag_antipattern",
            "reason": (learned.matched_entry or {}).get("reason") or "antipattern",
            "rule_hit": "learned_not_spam",
        }

    tz = check_tz_spam(
        email,
        recipient=email.routing_recipient or email.mailbox,
    )
    if tz is not None:
        return {
            "decision": "spam",
            "layer": "tz",
            "reason": tz.reason,
            "rule_hit": tz.rule_hit,
        }

    trusted = trusted_sender_pass(email, settings)
    if trusted is not None:
        return {
            "decision": "not_spam",
            "layer": "trusted",
            "reason": trusted.reason,
            "rule_hit": trusted.rule_hit or "trusted_sender",
        }

    return {
        "decision": "uncertain",
        "layer": "needs_llm",
        "reason": "",
        "rule_hit": "",
    }


def _match_learned_local(email: EmailMessage, entries: list[dict]):
    from agent_pochta.rules.spam_learning import (
        LearnedSpamDecision,
        reason_indicates_not_spam,
        _entry_matches_email,
        _normalize_label,
        _reconcile_label_with_reason,
    )
    from agent_pochta.schemas import SpamResult

    for entry in entries:
        if not _entry_matches_email(
            sender_email=email.sender_email,
            subject=email.subject or "",
            body=email.body_text or "",
            entry=entry,
        ):
            continue
        label = _reconcile_label_with_reason(
            _normalize_label(entry.get("label")),
            str(entry.get("reason") or ""),
        )
        if label == "spam" and reason_indicates_not_spam(str(entry.get("reason") or "")):
            continue
        if label == "not_spam":
            return LearnedSpamDecision(
                is_spam=False,
                matched_entry=entry,
                entry_kind=label,
            )
        reason = entry.get("reason") or "Похожее на ранее отмеченный спам"
        return LearnedSpamDecision(
            is_spam=True,
            spam_result=SpamResult(
                is_spam=True,
                confidence=0.98,
                reason=f"Обучение на спаме: {reason}",
                rule_hit="learned_spam_pattern",
            ),
            matched_entry=entry,
            entry_kind=label,
        )
    return None


def _load_learned_entries() -> list[dict]:
    from agent_pochta.rules.spam_learning import _collect_learned_entries

    return _collect_learned_entries()


def _stub_summary(email: EmailMessage, company_hint: str) -> str:
    subject = " ".join((email.subject or "").split())
    body = " ".join((email.body_text or "").split())
    sentences = re.split(r"(?<=[.!?])\s+", body) if body else []
    useful = [s for s in sentences if len(s) > 25][:2]
    parts: list[str] = []
    if company_hint:
        parts.append(f"Запрос от {company_hint}.")
    if subject:
        parts.append(subject)
    parts.extend(useful)
    text = " ".join(parts).strip()
    return text[:300] if text else subject[:300]


def _company_hint(email: EmailMessage) -> str:
    blob = f"{email.subject or ''} {(email.body_text or '')[:800]}"
    match = _COMPANY_RE.search(blob)
    if match:
        return " ".join(match.group(0).split())[:120]
    domain = (email.sender_email or "").rsplit("@", 1)[-1].lower()
    if domain and domain not in {"gmail.com", "mail.ru", "yandex.ru", "turbo-don.ru"}:
        return domain
    return ""


def _is_business_sender(sender: str) -> bool:
    low = (sender or "").lower()
    return any(token in low for token in _BUSINESS_DOMAINS)


def _body_excerpt(body: str, n: int = 500) -> str:
    return " ".join((body or "").split())[:n]


def _clean_fp_entries(store: dict) -> int:
    fixed = 0
    for entry in store.get("entries") or []:
        if entry.get("label") != "spam":
            continue
        reason = str(entry.get("reason") or "")
        if reason_indicates_not_spam(reason):
            entry["label"] = "not_spam"
            if not reason.startswith("bulk_imap_1000"):
                entry["reason"] = f"bulk_imap_1000:fp_fix:{reason[:120]}"
            fixed += 1
    return fixed


def _mine_and_append_patterns(
    emails: list[EmailMessage],
    spam_rows: list[dict],
    store: dict,
) -> dict:
    """Append high-precision spam/antipattern entries; return mining stats."""
    spam_kw = Counter()
    for email, row in zip(emails, spam_rows):
        if row["decision"] != "spam" or row["layer"] not in {"rules", "tz"}:
            continue
        for kw in extract_spam_learning_keywords(
            email.subject or "",
            email.body_text or "",
            sender_email=email.sender_email,
        ):
            spam_kw[kw] += 1

    existing_senders = {
        (e.get("sender_email") or "").lower()
        for e in store.get("entries") or []
        if e.get("label") == "spam"
    }
    existing_anti = {
        (e.get("sender_email") or "").lower()
        for e in store.get("entries") or []
        if e.get("label") == "not_spam"
    }

    added_spam = 0
    added_anti = 0
    now = datetime.now(timezone.utc).isoformat()

    # Spam patterns: rules-confirmed, keyword freq >= 3, unique sender
    for email, row in zip(emails, spam_rows):
        if row["decision"] != "spam" or row["layer"] not in {"rules", "tz"}:
            continue
        sender = (email.sender_email or "").lower().strip()
        if not sender or sender in existing_senders:
            continue
        if _is_business_sender(sender):
            continue
        kws = extract_spam_learning_keywords(
            email.subject or "",
            email.body_text or "",
            sender_email=sender,
        )
        # keep only frequent markers from corpus
        kws = [k for k in kws if spam_kw[k] >= 2][:6]
        # skip patterns that look like gov/procurement (often TZ false positives)
        joined = " ".join(kws)
        if any(
            bad in joined
            for bad in (
                "госорган",
                "извещени",
                "закупк",
                "отчет п-2",
                "деловые линии",
            )
        ):
            continue
        if not kws and not sender:
            continue
        entry = {
            "id": str(uuid.uuid4()),
            "created_at": now,
            "message_id": email.message_id or f"bulk-{uuid.uuid4()}",
            "sender_email": sender,
            "keywords": kws,
            "label": "spam",
            "reason": f"bulk_imap_1000:rules:{row.get('rule_hit') or row['layer']}",
        }
        store.setdefault("entries", []).append(entry)
        existing_senders.add(sender)
        added_spam += 1
        if added_spam >= 80:
            break

    # Antipatterns: business domains in uncertain/not_spam OR wrongly spam via noreply TZ
    for email, row in zip(emails, spam_rows):
        sender = (email.sender_email or "").lower().strip()
        if not sender or not _is_business_sender(sender) or sender in existing_anti:
            continue
        if row["decision"] == "spam" and row["layer"] == "rules":
            # appendix A marketing can still be spam even from known domains — skip
            continue
        kws = extract_spam_learning_keywords(
            email.subject or "",
            email.body_text or "",
            sender_email=sender,
        )[:4]
        entry = {
            "id": str(uuid.uuid4()),
            "created_at": now,
            "message_id": email.message_id or f"bulk-anti-{uuid.uuid4()}",
            "sender_email": sender,
            "keywords": kws,
            "label": "not_spam",
            "reason": "bulk_imap_1000:business_sender_antipattern",
        }
        store.setdefault("entries", []).append(entry)
        existing_anti.add(sender)
        added_anti += 1
        if added_anti >= 60:
            break

    return {
        "top_spam_keywords": spam_kw.most_common(40),
        "added_spam_patterns": added_spam,
        "added_antipatterns": added_anti,
    }


def _build_card(
    email: EmailMessage,
    spam: dict,
    *,
    allowlist: dict[str, str],
    onec_names: dict[str, str],
) -> dict:
    company = _company_hint(email)
    recipient = email.routing_recipient or (email.to[0] if email.to else email.mailbox)
    decision = None
    outside = False
    if spam["decision"] != "spam":
        decision = route_email(
            email,
            combined_text=f"{email.subject or ''}\n{email.body_text or ''}",
            recipient=recipient,
        )
        code = decision.services[0].code if decision.services else ""
        name = (
            allowlist.get(code)
            or onec_names.get(code)
            or (decision.services[0].name if decision.services else "")
        )
        if allowlist and code and code not in allowlist:
            outside = True
        xml = decision.xml_document or ""
        xml_ok = bool(xml) and validate_xml_document(xml)
        routing = {
            "organization": decision.organization,
            "direction": decision.direction,
            "department_code": code,
            "department_name": name,
            "process": decision.process,
            "partner": decision.partner or company or "-",
            "match_source": decision.match_source,
            "matching_keywords": list(decision.matching_keywords or []),
            "outside_allowlist": outside,
            "confidence_level": decision.confidence_level,
        }
    else:
        xml = ""
        xml_ok = False
        routing = {
            "organization": "",
            "direction": "",
            "department_code": "00-999999",
            "department_name": "SPAM",
            "process": "",
            "partner": company or "-",
            "match_source": "spam",
            "matching_keywords": [],
            "outside_allowlist": False,
            "confidence_level": "HIGH",
        }

    return {
        "sender": email.sender_email,
        "recipient": recipient,
        "subject": email.subject or "",
        "body_excerpt": _body_excerpt(email.body_text or ""),
        "summary_ru": _stub_summary(email, company),
        "spam": {
            "decision": spam["decision"],
            "layer": spam["layer"],
            "reason": spam["reason"],
            "rule_hit": spam.get("rule_hit") or "",
        },
        "routing": routing,
        "xml_document": xml if spam["decision"] != "spam" else "",
        "xml_valid": xml_ok if spam["decision"] != "spam" else None,
        "rate_analitik": "",
        "message_id": email.message_id,
        "received_at": email.received_at.isoformat() if email.received_at else "",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--mailbox", default="")
    args = parser.parse_args()

    settings = get_settings()
    mailbox_list = list(settings.mailbox_list)
    mailbox = (args.mailbox or (mailbox_list[0] if mailbox_list else "")).strip()
    if not mailbox or "@" not in mailbox:
        print(f"Invalid mailbox: {mailbox!r}; MAILBOXES={settings.mailboxes!r}", flush=True)
        return 1

    out_dir = ROOT / "data" / "stats"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"=== IMAP fetch newest {args.limit} from {mailbox} ===", flush=True)
    emails = _fetch_newest(mailbox, args.limit)
    print(f"parsed: {len(emails)}", flush=True)
    if not emails:
        return 1

    # --- Phase A: spam before improve ---
    print("=== Spam classify (before learning update) ===", flush=True)
    learned_before = _load_learned_entries()
    print(f"  learned entries cached: {len(learned_before)}", flush=True)
    spam_before = [
        _classify_spam(e, settings, learned_entries=learned_before) for e in emails
    ]
    before_counts = Counter(r["decision"] for r in spam_before)
    print(dict(before_counts), flush=True)

    # Fix FP in store + mine patterns
    print("=== Clean FP + mine patterns → spam_learning JSON ===", flush=True)
    store = load_spam_learning()
    fp_fixed = _clean_fp_entries(store)
    mining = _mine_and_append_patterns(emails, spam_before, store)
    save_spam_learning(store)
    print(f"fp_fixed={fp_fixed} mining={mining}", flush=True)

    print("=== Resync spam_learning → Qdrant ===", flush=True)
    sync_result = resync_spam_learning_to_qdrant()
    print(sync_result, flush=True)

    # Reset engine cache after rule file not changed but learning changed
    reset_route_engine()

    print("=== Spam classify (after learning update) ===", flush=True)
    learned_after = _load_learned_entries()
    print(f"  learned entries cached: {len(learned_after)}", flush=True)
    spam_after = [
        _classify_spam(e, settings, learned_entries=learned_after) for e in emails
    ]
    after_counts = Counter(r["decision"] for r in spam_after)
    print(dict(after_counts), flush=True)

    # Eval CSV
    eval_path = out_dir / "imap_spam_eval_1000.csv"
    with eval_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "message_id",
                "sender",
                "subject",
                "before",
                "before_layer",
                "after",
                "after_layer",
                "reason",
            ],
        )
        writer.writeheader()
        for email, b, a in zip(emails, spam_before, spam_after):
            writer.writerow(
                {
                    "message_id": email.message_id,
                    "sender": email.sender_email,
                    "subject": (email.subject or "")[:120],
                    "before": b["decision"],
                    "before_layer": b["layer"],
                    "after": a["decision"],
                    "after_layer": a["layer"],
                    "reason": a["reason"][:200],
                }
            )

    patterns_path = out_dir / "imap_spam_patterns_1000.json"
    patterns_path.write_text(
        json.dumps(
            {
                "fetched": len(emails),
                "mailbox": mailbox,
                "before": dict(before_counts),
                "after": dict(after_counts),
                "fp_fixed": fp_fixed,
                "mining": {
                    "added_spam_patterns": mining["added_spam_patterns"],
                    "added_antipatterns": mining["added_antipatterns"],
                    "top_spam_keywords": mining["top_spam_keywords"],
                },
                "qdrant_sync": sync_result,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # --- Phase B: cards ---
    print("=== Build routing XML cards ===", flush=True)
    allowlist = load_ui_department_allowlist()
    onec_names = load_onec_department_names_map()
    cards_path = out_dir / "imap_cards_1000.jsonl"
    dept_counter: Counter[str] = Counter()
    org_counter: Counter[str] = Counter()
    xml_ok = 0
    xml_bad = 0

    with cards_path.open("w", encoding="utf-8") as fh:
        for i, (email, spam) in enumerate(zip(emails, spam_after), start=1):
            card = _build_card(
                email,
                spam,
                allowlist=allowlist,
                onec_names=onec_names,
            )
            fh.write(json.dumps(card, ensure_ascii=False) + "\n")
            if spam["decision"] != "spam":
                dept_counter[card["routing"]["department_code"]] += 1
                org_counter[card["routing"]["organization"]] += 1
                if card.get("xml_valid"):
                    xml_ok += 1
                else:
                    xml_bad += 1
            if i % 100 == 0:
                print(f"  cards {i}/{len(emails)}", flush=True)

    # Summary MD
    summary_path = out_dir / "imap_cards_1000_summary.md"
    lines = [
        "# IMAP bulk 1000 — сводка",
        "",
        f"- Ящик: `{mailbox}`",
        f"- Писем: **{len(emails)}**",
        f"- Spam до: `{dict(before_counts)}`",
        f"- Spam после: `{dict(after_counts)}`",
        f"- FP label fixes: **{fp_fixed}**",
        f"- Добавлено spam patterns: **{mining['added_spam_patterns']}**, antipatterns: **{mining['added_antipatterns']}**",
        f"- Qdrant sync: `{sync_result}`",
        f"- XML valid (non-spam): **{xml_ok}**, invalid: **{xml_bad}**",
        "",
        "## Топ отделов (non-spam)",
    ]
    for code, n in dept_counter.most_common(15):
        name = allowlist.get(code) or onec_names.get(code) or code
        lines.append(f"- `{code}` {name}: {n}")
    lines.extend(["", "## Организации", ""])
    for org, n in org_counter.most_common():
        lines.append(f"- `{org}`: {n}")
    lines.extend(
        [
            "",
            "## Файлы",
            f"- `{eval_path.relative_to(ROOT)}`",
            f"- `{patterns_path.relative_to(ROOT)}`",
            f"- `{cards_path.relative_to(ROOT)}`",
            "",
            "Поле `rate_analitik` в JSONL пустое — заполните при проверке (`ok` / `fix_spam` / `fix_dept`).",
        ]
    )
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("=== DONE ===", flush=True)
    print(f"eval: {eval_path}", flush=True)
    print(f"patterns: {patterns_path}", flush=True)
    print(f"cards: {cards_path}", flush=True)
    print(f"summary: {summary_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
