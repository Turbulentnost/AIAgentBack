"""Offline RAG analysis: last N emails vs spam_learning + departments collections."""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sqlalchemy import select

from agent_pochta.config import get_settings
from agent_pochta.db.message_filters import load_payload_dict, recipient_display_value
from agent_pochta.db.models import EmailMessageRow
from agent_pochta.db.session import get_session_factory
from agent_pochta.routing.normalize import normalize_text
from agent_pochta.routing.recipients import build_routing_search_text
from agent_pochta.services.rag import score_department_keywords
from agent_pochta.services.rag_qdrant import build_rag_service
from agent_pochta.services.spam_learning_rag_qdrant import list_spam_learning_in_qdrant


def _short(text: str | None, n: int = 60) -> str:
    s = " ".join((text or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _spam_score(entry: dict, sender_email: str, subject: str, body: str) -> tuple[int, int]:
    sender_email = sender_email.lower().strip()
    text = normalize_text(f"{subject} {body}")
    entry_sender = (entry.get("sender_email") or "").lower().strip()
    if entry_sender and entry_sender != sender_email:
        return 0, 0
    keywords = entry.get("keywords") or []
    keyword_hits = sum(1 for kw in keywords if kw and kw in text)
    if keywords and keyword_hits == 0 and not entry_sender:
        return 0, 0
    score = 0
    if entry_sender:
        score += 3
    score += keyword_hits
    return score, keyword_hits


def _find_spam_match(
    entries: list[dict],
    *,
    sender_email: str,
    subject: str,
    body: str,
) -> tuple[dict | None, int, int]:
    ranked: list[tuple[int, int, dict]] = []
    for entry in entries:
        score, hits = _spam_score(entry, sender_email, subject, body)
        if score > 0:
            ranked.append((score, hits, entry))
    if not ranked:
        return None, 0, 0
    ranked.sort(
        key=lambda item: (item[0], item[2].get("created_at") or ""),
        reverse=True,
    )
    score, hits, entry = ranked[0]
    return entry, score, hits


def _dept_top(
    rag,
    search_text: str,
    recipient: str,
    top_k: int = 3,
) -> list[tuple[int, object]]:
    depts = list(getattr(rag, "_departments", {}).values())
    if not depts and hasattr(rag, "refresh_departments_cache"):
        rag.refresh_departments_cache()
        depts = list(getattr(rag, "_departments", {}).values())
    scored = [
        (score_department_keywords(d, search_text, recipient=recipient or None), d)
        for d in depts
    ]
    scored = [(s, d) for s, d in scored if s > 0]
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:top_k]


def main(limit: int = 100) -> int:
    settings = get_settings()
    print(f"RAG_BACKEND={settings.rag_backend} QDRANT={settings.qdrant_url}")
    print(f"DATABASE={settings.database_url.split('@')[-1]}")

    Session = get_session_factory()
    rag = build_rag_service(settings)
    spam_entries: list[dict] = []
    if settings.rag_backend == "qdrant":
        try:
            spam_entries = list_spam_learning_in_qdrant(settings.qdrant_url)
        except Exception as exc:
            print(f"WARN: spam_learning list failed: {exc}")
    print(f"spam_learning entries in Qdrant: {len(spam_entries)}")
    dept_count = len(getattr(rag, "_departments", {}) or {})
    print(f"departments cached: {dept_count}")

    with Session() as session:
        rows = session.scalars(
            select(EmailMessageRow)
            .order_by(EmailMessageRow.received_at.desc())
            .limit(limit)
        ).all()

    out_dir = ROOT / "data" / "stats"
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "rag_last_100.csv"

    results: list[dict] = []
    body_empty = 0
    spam_hit = 0
    spam_label_counter: Counter[str] = Counter()
    dept_top1_counter: Counter[str] = Counter()
    agree_spam = 0
    disagree_spam = 0
    agree_dept = 0
    disagree_dept = 0
    both_no_spam_rag = 0
    dept_rag_hit = 0

    for row in rows:
        payload = load_payload_dict(row.raw_payload_json) or {}
        body = str(payload.get("body_text") or "").strip()
        has_body = bool(body)
        if not has_body:
            body_empty += 1
        recipient = recipient_display_value(mailbox=row.mailbox, payload=payload) or row.mailbox
        subject = row.subject or ""
        sender = row.sender_email or ""

        spam_entry, spam_score, spam_kw_hits = _find_spam_match(
            spam_entries,
            sender_email=sender,
            subject=subject,
            body=body,
        )
        spam_rag_hit = spam_entry is not None
        if spam_rag_hit:
            spam_hit += 1
            spam_label_counter[str(spam_entry.get("label") or "?")] += 1

        stored_spam = bool(row.is_spam)
        if spam_rag_hit:
            rag_says_spam = str(spam_entry.get("label") or "") == "spam"
            rag_says_not = str(spam_entry.get("label") or "") == "not_spam"
            if rag_says_spam and stored_spam:
                agree_spam += 1
            elif rag_says_not and not stored_spam:
                agree_spam += 1
            elif rag_says_spam and not stored_spam:
                disagree_spam += 1
            elif rag_says_not and stored_spam:
                disagree_spam += 1
        else:
            both_no_spam_rag += 1

        search_text = build_routing_search_text(
            recipient=recipient,
            subject=subject,
            body=body,
        )
        top = _dept_top(rag, search_text, recipient, top_k=3)
        if top:
            dept_rag_hit += 1
            top1_id = top[0][1].department_id
            top1_name = top[0][1].department_name
            top1_score = top[0][0]
            dept_top1_counter[f"{top1_id}|{top1_name}"] += 1
            stored_id = (row.department_id or "").strip()
            if stored_id:
                if stored_id == top1_id:
                    agree_dept += 1
                else:
                    disagree_dept += 1
        else:
            top1_id = ""
            top1_name = ""
            top1_score = 0

        top2 = f"{top[1][1].department_id}:{top[1][0]}" if len(top) > 1 else ""
        top3 = f"{top[2][1].department_id}:{top[2][0]}" if len(top) > 2 else ""

        results.append(
            {
                "id": str(row.id),
                "received_at": row.received_at.isoformat() if row.received_at else "",
                "from": sender,
                "to": recipient,
                "subject": subject,
                "subject_short": _short(subject, 70),
                "status": row.status,
                "is_spam_stored": stored_spam,
                "department_id_stored": row.department_id or "",
                "department_name_stored": row.department_name or "",
                "has_body": has_body,
                "body_len": len(body),
                "spam_rag_hit": spam_rag_hit,
                "spam_rag_label": (spam_entry or {}).get("label") or "",
                "spam_rag_score": spam_score,
                "spam_rag_kw_hits": spam_kw_hits,
                "spam_rag_reason": _short((spam_entry or {}).get("reason") or "", 80),
                "spam_rag_entry_id": (spam_entry or {}).get("id") or "",
                "spam_rag_sender": (spam_entry or {}).get("sender_email") or "",
                "dept_rag_top1_id": top1_id,
                "dept_rag_top1_name": top1_name,
                "dept_rag_top1_score": top1_score,
                "dept_rag_top2": top2,
                "dept_rag_top3": top3,
                "spam_agree": (
                    "agree"
                    if spam_rag_hit
                    and (
                        (spam_entry.get("label") == "spam" and stored_spam)
                        or (spam_entry.get("label") == "not_spam" and not stored_spam)
                    )
                    else (
                        "disagree"
                        if spam_rag_hit
                        and (
                            (spam_entry.get("label") == "spam" and not stored_spam)
                            or (spam_entry.get("label") == "not_spam" and stored_spam)
                        )
                        else "n/a"
                    )
                ),
                "dept_agree": (
                    "agree"
                    if top1_id and (row.department_id or "") == top1_id
                    else (
                        "disagree"
                        if top1_id and (row.department_id or "")
                        else ("no_rag" if not top1_id else "no_stored")
                    )
                ),
            }
        )

    fieldnames = list(results[0].keys()) if results else []
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    n = len(results)
    print("\n========== ИТОГИ RAG (последние {} писем) ==========".format(n))
    print(f"Тело письма пустое (body_text): {body_empty}/{n} — для них RAG шёл по subject (+from/recipient)")
    print(f"\nSpam learning RAG:")
    print(f"  hit: {spam_hit}/{n}  miss: {n - spam_hit}/{n}")
    print(f"  labels among hits: {dict(spam_label_counter)}")
    print(f"  согласие с is_spam (среди hit): agree={agree_spam} disagree={disagree_spam}")
    print(f"  без spam RAG hit: {both_no_spam_rag}")

    print(f"\nDepartments RAG (keyword score > 0):")
    print(f"  hit (есть top1): {dept_rag_hit}/{n}  miss: {n - dept_rag_hit}/{n}")
    print(f"  согласие top1 vs department_id: agree={agree_dept} disagree={disagree_dept}")
    print(f"  Top matched departments (by RAG top1):")
    for key, cnt in dept_top1_counter.most_common(15):
        did, dname = key.split("|", 1)
        print(f"    {cnt:3d}  {did}  {dname}")

    # notable outliers
    notables: list[dict] = []
    for r in results:
        if r["spam_agree"] == "disagree":
            notables.append(("spam_disagree", r))
        elif r["dept_agree"] == "disagree" and r["dept_rag_top1_score"] >= 2:
            notables.append(("dept_disagree", r))
        elif r["spam_rag_hit"] and r["spam_rag_label"] == "spam":
            notables.append(("spam_hit", r))
        elif r["dept_rag_top1_score"] >= 5:
            notables.append(("strong_dept", r))
        elif not r["spam_rag_hit"] and r["is_spam_stored"]:
            notables.append(("stored_spam_no_rag", r))

    seen = set()
    unique_notables = []
    for kind, r in notables:
        if r["id"] in seen:
            continue
        seen.add(r["id"])
        unique_notables.append((kind, r))
        if len(unique_notables) >= 20:
            break

    print("\n========== ПРИМЕЧАТЕЛЬНЫЕ СТРОКИ (до 20) ==========")
    print(
        f"{'#':>2} {'kind':<18} {'spamRAG':<12} {'deptRAG top1':<28} {'stored':<22} subject"
    )
    for i, (kind, r) in enumerate(unique_notables, 1):
        spam_s = (
            f"{r['spam_rag_label']}:{r['spam_rag_score']}"
            if r["spam_rag_hit"]
            else "—"
        )
        dept_s = (
            f"{r['dept_rag_top1_id']}:{r['dept_rag_top1_score']}"
            if r["dept_rag_top1_id"]
            else "—"
        )
        stored = f"spam={r['is_spam_stored']} {r['department_id_stored'] or '-'}"
        print(
            f"{i:2d} {kind:<18} {spam_s:<12} {dept_s:<28} {stored:<22} {_short(r['subject'], 50)}"
        )

    # compact markdown-ish table of first 15 by received_at (already desc)
    print("\n========== ВЫБОРКА 15 ПОСЛЕДНИХ ==========")
    print(
        "| # | received | spam_stored | spam_RAG | dept_stored | dept_RAG top1 | score | subject |"
    )
    print("|---|----------|-------------|----------|-------------|---------------|-------|---------|")
    for i, r in enumerate(results[:15], 1):
        spam_rag = (
            f"{r['spam_rag_label']}({r['spam_rag_score']})"
            if r["spam_rag_hit"]
            else "—"
        )
        print(
            f"| {i} | {r['received_at'][:16]} | {r['is_spam_stored']} | {spam_rag} | "
            f"{r['department_id_stored'] or '—'} | {r['dept_rag_top1_id'] or '—'} | "
            f"{r['dept_rag_top1_score']} | {_short(r['subject'], 40)} |"
        )

    print(f"\nCSV: {csv_path}")
    if hasattr(rag, "close"):
        try:
            rag.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main(100))
