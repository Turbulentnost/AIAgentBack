"""Проверка предусловий для ручного теста routing_corrections audit."""

from __future__ import annotations

import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import httpx
from sqlalchemy import text

from agent_pochta.config import get_settings
from agent_pochta.db.session import get_session_factory
from agent_pochta.routing.corrections import load_corrections
from agent_pochta.services.rag_qdrant import build_rag_service


def _ok(msg: str) -> None:
    print(f"  OK  {msg}")


def _fail(msg: str) -> None:
    print(f"  FAIL  {msg}")


def main() -> int:
    settings = get_settings()
    errors = 0

    print("=== Routing audit prerequisites ===\n")

    # PostgreSQL
    print("[1] PostgreSQL")
    try:
        Session = get_session_factory()
        with Session() as session:
            n = session.scalar(text("SELECT COUNT(*) FROM email_messages"))
        _ok(f"email_messages: {n}")
    except Exception as exc:
        _fail(f"DB: {exc}")
        errors += 1

    # Qdrant
    print("\n[2] Qdrant")
    try:
        url = settings.qdrant_url.rstrip("/")
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(f"{url}/collections")
        resp.raise_for_status()
        cols = [c["name"] for c in resp.json().get("result", {}).get("collections", [])]
        _ok(f"collections: {len(cols)} ({', '.join(cols[:4])}…)")
        rag = build_rag_service(settings)
        dept_n = len(getattr(rag, "_departments", {}) or {})
        if dept_n == 0 and hasattr(rag, "refresh_departments_cache"):
            rag.refresh_departments_cache()
            dept_n = len(getattr(rag, "_departments", {}) or {})
        _ok(f"departments cache: {dept_n}")
        if hasattr(rag, "close"):
            rag.close()
    except Exception as exc:
        _fail(f"Qdrant: {exc}")
        errors += 1

    # API
    print("\n[3] API (fetch-body)")
    api_base = "http://127.0.0.1:8080"
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(f"{api_base}/health")
        if resp.status_code == 200:
            _ok(f"{api_base}/health")
        else:
            _fail(f"{api_base}/health → {resp.status_code}")
            errors += 1
    except Exception as exc:
        _fail(f"API недоступен ({exc}). Для body используйте --no-imap")

    # LLM
    print("\n[4] LLM")
    try:
        from agent_pochta.services import build_container
        from agent_pochta.services.gigachat_llm import GigaChatLLMGateway
        from agent_pochta.services.http_llm import ChatCompletionsLLMGateway

        llm = build_container(settings).llm
        if isinstance(llm, (ChatCompletionsLLMGateway, GigaChatLLMGateway)):
            _ok(f"gateway: {type(llm).__name__}")
        else:
            _fail(f"LLM stub ({type(llm).__name__}) — будет deterministic_fallback")
            errors += 1
    except Exception as exc:
        _fail(f"LLM: {exc}")
        errors += 1

    # routing_corrections.json
    print("\n[5] routing_corrections.json")
    try:
        store = load_corrections()
        entries = store.get("entries") or []
        _ok(f"entries: {len(entries)}")
    except Exception as exc:
        _fail(str(exc))
        errors += 1

    print("\n=== Итог ===")
    if errors:
        print(f"Проблем: {errors}. Исправьте и повторите.")
        print("Docker: docker compose -p agent-pochta up -d")
        return 1
    print("Всё готово для ручного теста.")
    print("\nДальше: run_routing_audit_manual.cmd")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
