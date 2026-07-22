"""CLI-диспетчер постановки задач создания черновиков протоколов в Celery."""

from __future__ import annotations

import asyncio
import json
import sys

from app.services.meeting_protocol_dispatch_service import run_protocol_draft_dispatch


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    payload = asyncio.run(run_protocol_draft_dispatch())
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not payload.get("errors") else 1


if __name__ == "__main__":
    raise SystemExit(main())
