"""Rebuild IMAP review cards from the locally stored excerpts, without IMAP or LLM."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from imap_bulk_analyze import _build_card, _classify_spam

from agent_pochta.config import get_settings, reset_settings
from agent_pochta.routing.engine import reset_route_engine
from agent_pochta.rules.spam_learning import load_spam_learning
from agent_pochta.schemas import EmailMessage
from agent_pochta.services.routing_departments import (
    load_onec_department_names_map,
    load_ui_department_allowlist,
)


def _received_at(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        return datetime.now(timezone.utc)


def _email_from_card(card: dict) -> EmailMessage:
    recipient = str(card.get("recipient") or "")
    return EmailMessage(
        message_id=str(card.get("message_id") or ""),
        mailbox=recipient,
        routing_recipient=recipient,
        sender_email=str(card.get("sender") or ""),
        subject=str(card.get("subject") or ""),
        body_text=str(card.get("body_excerpt") or ""),
        received_at=_received_at(card.get("received_at") or ""),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cards",
        type=Path,
        default=ROOT / "data" / "stats" / "imap_cards_1000.jsonl",
    )
    parser.add_argument("--preserve-first", type=int, default=100)
    args = parser.parse_args()

    cards = [
        json.loads(line)
        for line in args.cards.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(cards) <= args.preserve_first:
        raise ValueError("There are no cards after the preserved review set.")

    # RuleRouter's correction lookups normally prefer Qdrant.  A local review
    # rebuild must not make 900 network calls or depend on that service.
    os.environ["RAG_BACKEND"] = "stub"
    reset_settings()
    settings = get_settings()
    # Intentionally use the local JSON store: this rebuild must stay offline.
    learned_entries = load_spam_learning().get("entries") or []
    allowlist = load_ui_department_allowlist()
    onec_names = load_onec_department_names_map()
    reset_route_engine()

    changed_cards = 0
    changed_fields: Counter[str] = Counter()
    rebuilt: list[dict] = []
    for index, old in enumerate(cards):
        if index < args.preserve_first:
            rebuilt.append(old)
            continue

        email = _email_from_card(old)
        spam = _classify_spam(email, settings, learned_entries=learned_entries)
        refreshed = _build_card(
            email,
            spam,
            allowlist=allowlist,
            onec_names=onec_names,
        )
        # The UI must receive a clean review state; retain identity fields from the source card.
        for field in ("message_id", "received_at"):
            refreshed[field] = old.get(field, refreshed.get(field))
        refreshed["rate_analitik"] = ""

        fields = {
            key
            for key in set(old) | set(refreshed)
            if old.get(key) != refreshed.get(key)
        }
        if fields:
            changed_cards += 1
            changed_fields.update(fields)
        rebuilt.append(refreshed)

    temp_path = args.cards.with_suffix(".jsonl.tmp")
    with temp_path.open("w", encoding="utf-8", newline="\n") as fh:
        for card in rebuilt:
            fh.write(json.dumps(card, ensure_ascii=False) + "\n")
    temp_path.replace(args.cards)

    print(
        json.dumps(
            {
                "cards_total": len(cards),
                "preserved": args.preserve_first,
                "rerouted": len(cards) - args.preserve_first,
                "cards_changed": changed_cards,
                "changed_fields": dict(changed_fields.most_common()),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
