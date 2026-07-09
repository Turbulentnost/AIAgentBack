"""Проверка доступности платформенных сервисов перед USE_STUBS=false.

Запуск:  python scripts/check_platform.py
"""

from __future__ import annotations

import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx  # noqa: E402

from agent_pochta.config import get_settings  # noqa: E402


def check(name: str, url: str, path: str = "") -> bool:
    target = f"{url.rstrip('/')}{path}"
    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.get(target)
            ok = response.status_code < 500
            print(f"  {'OK' if ok else 'WARN'} {name}: {target} → HTTP {response.status_code}")
            return ok
    except Exception as exc:
        print(f"  FAIL {name}: {target} → {exc}")
        return False


def main() -> None:
    settings = get_settings()
    modes = settings.service_modes()
    print("Конфигурация сервисов:", modes)
    print()

    ok = True
    if settings.llm_gateway_url:
        ok &= check("LLM Gateway", settings.llm_gateway_url, "/models")
    else:
        print("  SKIP LLM Gateway — LLM_GATEWAY_URL не задан")

    if settings.rag_backend == "qdrant":
        ok &= check("Qdrant", settings.qdrant_url, "/collections")
    else:
        print("  SKIP Qdrant — RAG_BACKEND=stub")

    if settings.integration_service_url:
        ok &= check("Integration HTTP", settings.integration_service_url)
    elif settings.erp_integration_mode == "odata" and settings.odata_base_url:
        target = f"{settings.odata_base_url.rstrip('/')}/$metadata"
        try:
            with httpx.Client(timeout=10.0) as client:
                auth = (settings.odata_username, settings.odata_password) if settings.odata_username else None
                response = client.get(target, auth=auth)
                ok &= response.status_code < 500
                print(
                    f"  {'OK' if response.status_code < 500 else 'WARN'} "
                    f"Integration OData: {target} → HTTP {response.status_code}"
                )
        except Exception as exc:
            print(f"  FAIL Integration OData: {target} → {exc}")
            ok = False
    else:
        print("  INFO Integration — stub (ERP_MODE=stub или URL не задан)")

    if settings.document_service_url:
        ok &= check("Document", settings.document_service_url)
    else:
        print("  INFO Document — stub (DOCUMENT_SERVICE_URL не задан на платформе)")

    print()
    if ok:
        print("Проверка пройдена. Перезапустите worker + beat + API.")
    else:
        print("Есть недоступные сервисы — исправьте .env или поднимите инфраструктуру.")
        sys.exit(1)


if __name__ == "__main__":
    main()
