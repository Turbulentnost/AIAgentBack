"""RAG-only выборка 20–30 писем из routing_corrections для ручной проверки отделов."""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import PROJECT_ROOT, get_settings
from agent_pochta.db.message_filters import load_payload_dict, recipient_display_value
from agent_pochta.db.models import EmailMessageRow
from agent_pochta.db.session import get_session_factory
from agent_pochta.routing.corrections_audit import (
    MatchStatus,
    match_all_corrections,
)
from agent_pochta.routing.recipients import build_routing_search_text
from agent_pochta.services.rag import score_department_keywords
from agent_pochta.services.rag_qdrant import build_rag_service

STATS_DIR = PROJECT_ROOT / "data" / "stats"
ENRICHED_PATH = STATS_DIR / "routing_corrections_enriched.json"


def _short(text: str | None, n: int = 60) -> str:
    s = " ".join((text or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _dept_top(rag, search_text: str, recipient: str, top_k: int = 3):
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


def _load_pool(session, *, enriched_path: Path, api_base: str, no_imap: bool) -> list[dict]:
    if enriched_path.is_file():
        data = json.loads(enriched_path.read_text(encoding="utf-8"))
        if isinstance(data, list) and data:
            by_corr = {str(r.get("corr_id")): r for r in data}
            matches = match_all_corrections(
                session,
                api_base=api_base,
                fetch_imap=not no_imap,
            )
            pool: list[dict] = []
            for m in matches:
                if m.status != MatchStatus.UNAMBIGUOUS:
                    continue
                rec = by_corr.get(m.corr_id, {})
                pool.append(
                    {
                        "corr_id": m.corr_id,
                        "email_id": m.email_id,
                        "subject": m.subject,
                        "sender_email": m.sender_email,
                        "recipient": m.recipient or "",
                        "body": m.body,
                        "body_source": m.body_source.value,
                        "expected_department_id": m.department_id,
                        "expected_department_name": m.department_name,
                        "original_department_id": m.original_department_id or "",
                        "reassigned": bool(
                            m.original_department_id
                            and m.original_department_id != m.department_id
                        ),
                        "keywords": rec.get("keywords") or m.keywords,
                    }
                )
            return pool

    matches = match_all_corrections(
        session,
        api_base=api_base,
        fetch_imap=not no_imap,
        only_unambiguous=True,
    )
    return [
        {
            "corr_id": m.corr_id,
            "email_id": m.email_id,
            "subject": m.subject,
            "sender_email": m.sender_email,
            "recipient": m.recipient or "",
            "body": m.body,
            "body_source": m.body_source.value,
            "expected_department_id": m.department_id,
            "expected_department_name": m.department_name,
            "original_department_id": m.original_department_id or "",
            "reassigned": bool(
                m.original_department_id and m.original_department_id != m.department_id
            ),
            "keywords": m.keywords,
        }
        for m in matches
    ]


def _stratified_sample(pool: list[dict], count: int, seed: int) -> list[dict]:
    if len(pool) <= count:
        return list(pool)

    rng = random.Random(seed)
    by_dept: dict[str, list[dict]] = defaultdict(list)
    for item in pool:
        by_dept[item["expected_department_name"]].append(item)

    reassigned = [p for p in pool if p.get("reassigned")]
    others = [p for p in pool if not p.get("reassigned")]

    picked: list[dict] = []
    seen: set[str] = set()

    def _take(items: list[dict], n: int) -> None:
        rng.shuffle(items)
        for item in items:
            if len(picked) >= count:
                return
            cid = item["corr_id"]
            if cid in seen:
                continue
            seen.add(cid)
            picked.append(item)
            if sum(1 for x in picked if x.get("reassigned")) >= n:
                return

    target_reassigned = max(1, count // 2)
    _take(reassigned, target_reassigned)

    dept_names = sorted(by_dept.keys(), key=lambda k: len(by_dept[k]), reverse=True)
    idx = 0
    while len(picked) < count and dept_names:
        dept = dept_names[idx % len(dept_names)]
        candidates = [p for p in by_dept[dept] if p["corr_id"] not in seen]
        if candidates:
            item = rng.choice(candidates)
            seen.add(item["corr_id"])
            picked.append(item)
        idx += 1
        if idx > len(dept_names) * 20:
            break

    if len(picked) < count:
        rest = [p for p in others if p["corr_id"] not in seen]
        rng.shuffle(rest)
        for item in rest:
            if len(picked) >= count:
                break
            picked.append(item)

    return picked[:count]


def _score_row(rag, item: dict) -> dict:
    search_text = build_routing_search_text(
        recipient=item.get("recipient") or "",
        subject=item.get("subject") or "",
        body=item.get("body") or "",
    )
    top = _dept_top(rag, search_text, item.get("recipient") or "", top_k=3)
    expected_id = item.get("expected_department_id") or ""
    top1_id = top[0][1].department_id if top else ""
    top1_name = top[0][1].department_name if top else ""
    top1_score = top[0][0] if top else 0
    совпадение = "да" if (top1_id == expected_id if top1_id and expected_id else False) else "нет"

    top2 = ""
    top3 = ""
    if len(top) > 1:
        top2 = f"{top[1][1].department_name} ({top[1][0]})"
    if len(top) > 2:
        top3 = f"{top[2][1].department_name} ({top[2][0]})"

    return {
        "corr_id": item.get("corr_id") or "",
        "email_id": item.get("email_id") or "",
        "тема": item.get("subject") or "",
        "тема_кратко": _short(item.get("subject"), 70),
        "от_кого": item.get("sender_email") or "",
        "кому": item.get("recipient") or "",
        "отдел_по_коррекции": item.get("expected_department_name") or "",
        "отдел_по_коррекции_id": expected_id,
        "отдел_по_rag": top1_name,
        "отдел_по_rag_id": top1_id,
        "балл_rag": top1_score,
        "rag_топ2": top2,
        "rag_топ3": top3,
        "совпадение": совпадение,
        "переназначено": "да" if item.get("reassigned") else "нет",
        "источник_тела": item.get("body_source") or "",
    }


def _write_md(rows: list[dict], path: Path) -> None:
    lines = [
        "# RAG validation sample (только отделы)",
        "",
        "**Колонки:**",
        "- **кому** — адрес получателя (куда пришло письмо)",
        "- **отдел_по_коррекции** — отдел, который указал оператор в HITL-коррекции (эталон, «как должно быть»)",
        "- **отдел_по_rag** — отдел, который RAG выбрал первым по keywords (без RuleRouter и LLM)",
        "- **балл_rag** — score keyword-matching",
        "- **совпадение** — да, если RAG top-1 = отдел по коррекции",
        "",
        "| тема | от_кого | кому | отдел_по_коррекции | отдел_по_rag | балл_rag | совпадение |",
        "|------|---------|------|--------------------|--------------|----------|------------|",
    ]
    for r in rows:
        lines.append(
            f"| {_short(r['тема'], 40)} | {_short(r['от_кого'], 22)} | "
            f"{_short(r['кому'], 22)} | {r['отдел_по_коррекции']} | "
            f"{r['отдел_по_rag']} | {r['балл_rag']} | {r['совпадение']} |"
        )
    yes_n = sum(1 for r in rows if r["совпадение"] == "да")
    lines.append("")
    lines.append(f"Всего: {len(rows)}. Совпадений RAG с коррекцией: {yes_n}.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    print(f"RAG_BACKEND={settings.rag_backend} QDRANT={settings.qdrant_url}")

    Session = get_session_factory()
    rag = build_rag_service(settings)
    dept_count = len(getattr(rag, "_departments", {}) or {})
    if dept_count == 0 and hasattr(rag, "refresh_departments_cache"):
        rag.refresh_departments_cache()
        dept_count = len(getattr(rag, "_departments", {}) or {})
    print(f"departments cached: {dept_count}")

    enriched_path = Path(args.enriched) if args.enriched else ENRICHED_PATH
    with Session() as session:
        pool = _load_pool(
            session,
            enriched_path=enriched_path,
            api_base=args.api_base,
            no_imap=args.no_imap,
        )

    print(f"Пул однозначных: {len(pool)}")
    sample = _stratified_sample(pool, args.count, args.seed)
    rows = [_score_row(rag, item) for item in sample]

    csv_path = STATS_DIR / "rag_validation_sample.csv"
    md_path = STATS_DIR / "rag_validation_sample.md"
    STATS_DIR.mkdir(parents=True, exist_ok=True)

    fieldnames = list(rows[0].keys()) if rows else []
    with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    _write_md(rows, md_path)

    matches = sum(1 for r in rows if r["совпадение"] == "да")
    print(f"Выборка: {len(rows)} писем, RAG совпал с коррекцией: {matches}/{len(rows)}")
    print(f"CSV: {csv_path}")
    print(f"MD:  {md_path}")

    if hasattr(rag, "close"):
        try:
            rag.close()
        except Exception:
            pass
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="RAG-only validation sample для routing_corrections")
    parser.add_argument("--count", type=int, default=25, help="Размер выборки (20–30)")
    parser.add_argument("--seed", type=int, default=42, help="Seed стратификации")
    parser.add_argument("--enriched", default="", help="Путь к routing_corrections_enriched.json")
    parser.add_argument("--api-base", default="http://127.0.0.1:8080")
    parser.add_argument("--no-imap", action="store_true")
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
