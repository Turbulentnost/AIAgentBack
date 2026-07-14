"""Диагностика доступа к OData 1С (чтение/запись по сущностям).

Запуск:
  python scripts/odata_probe.py
  python scripts/odata_probe.py --try-post
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import quote

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402

ENTITIES_READ = [
    "$metadata",
    "Catalog_Организации",
    "Catalog_СтруктураПредприятия",
    "Catalog_ПодразделенияОрганизаций",
    "Catalog_Сотрудники",
    "Catalog_Контрагенты",
    "Catalog_КонтрагентыForMail",
    "Catalog_ПодразделенияForMail",
    "Document_ТД_ВходящаяКорреспонденция",
    "InformationRegister_КадроваяИсторияСотрудников_RecordType",
]

POST_ENTITY = "Document_ТД_ВходящаяКорреспонденция"


def _error_text(response: httpx.Response) -> str:
    text = response.text.strip()
    if not text:
        return ""
    try:
        data = response.json()
        err = data.get("odata.error") or data.get("error")
        if isinstance(err, dict):
            msg = err.get("message")
            if isinstance(msg, dict):
                return str(msg.get("value") or msg)
            return str(msg or err)
    except json.JSONDecodeError:
        pass
    m = re.search(r"<m:message>(.*?)</m:message>", text, re.S)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()
    return text[:300]


def probe_get(
    client: httpx.Client,
    base: str,
    entity: str,
    *,
    auth: tuple[str, str] | None,
    top: int = 1,
) -> dict:
    if entity == "$metadata":
        url = f"{base.rstrip('/')}/$metadata"
    else:
        url = f"{base.rstrip('/')}/{quote(entity)}?$format=json&$top={top}"
    try:
        response = client.get(url, auth=auth, timeout=30)
    except Exception as exc:
        return {"entity": entity, "ok": False, "error": str(exc)}
    result = {
        "entity": entity,
        "status": response.status_code,
        "ok": response.status_code == 200,
        "error": _error_text(response) if response.status_code != 200 else "",
    }
    if response.status_code == 200 and entity != "$metadata":
        try:
            rows = response.json().get("value", [])
            result["rows"] = len(rows)
            if rows:
                result["sample_keys"] = sorted(rows[0].keys())[:12]
        except json.JSONDecodeError:
            result["error"] = "invalid json"
            result["ok"] = False
    elif response.status_code == 200:
        result["metadata_bytes"] = len(response.content)
    return result


def probe_post_minimal(
    client: httpx.Client,
    base: str,
    *,
    auth: tuple[str, str] | None,
) -> dict:
    url = f"{base.rstrip('/')}/{quote(POST_ENTITY)}?$format=json"
    payload = {
        "Date": "2026-07-09T14:00:00",
        "ИсточникПоступления": "E-MAIL",
        "Статус": "Передано на исполнение",
        "ТемаСлужебнойЗаписки": "OData probe test",
        "Подразделение": "Управление делами",
        "Партнер": "OData probe partner",
        "ПлательщикНаправление": "OData probe partner",
        "Организация_Key": "fbca2148-6cfd-11e7-812d-001e67112509",
        "ПодразделениеИсполнитель_Key": "a831b004-fb2c-11e2-8c56-001e67112509",
        "КомуПодразделениеСсылка_Key": "a831b004-fb2c-11e2-8c56-001e67112509",
        "Posted": False,
    }
    try:
        response = client.post(
            url,
            json=payload,
            auth=auth,
            timeout=30,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
    except Exception as exc:
        return {"entity": POST_ENTITY, "method": "POST", "ok": False, "error": str(exc)}
    return {
        "entity": POST_ENTITY,
        "method": "POST",
        "status": response.status_code,
        "ok": response.status_code in (200, 201),
        "error": _error_text(response) if response.status_code not in (200, 201) else "",
        "body_preview": response.text[:400] if response.status_code in (200, 201) else "",
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="OData access probe")
    parser.add_argument("--try-post", action="store_true", help="Пробный POST документа")
    args = parser.parse_args()

    settings = get_settings()
    base = settings.odata_base_url.strip()
    user = settings.odata_username.strip()
    password = settings.odata_password

    print("=== Конфигурация ===")
    print(f"ODATA_BASE_URL: {base or '(пусто)'}")
    print(f"ODATA_USERNAME: {user or '(пусто)'}")
    print(f"ODATA_PASSWORD: {'задан' if password else 'пусто'} ({len(password)} симв.)")
    print(f"ERP_MODE: {settings.erp_integration_mode}")
    print(f"USE_STUBS: {settings.use_stubs}")
    print(f"ORG_KEYS_FILE: {settings.odata_organization_keys_file}")
    print(f"DEPT_KEYS_FILE: {settings.odata_department_keys_file}")

    org_file = ROOT / settings.odata_organization_keys_file
    dept_file = ROOT / settings.odata_department_keys_file
    print(f"  org keys: {len(json.loads(org_file.read_text(encoding='utf-8'))) if org_file.is_file() else 0} записей")
    print(f"  dept keys: {len(json.loads(dept_file.read_text(encoding='utf-8'))) if dept_file.is_file() else 0} записей")

    if not base:
        print("\nODATA_BASE_URL не задан — проверка невозможна.")
        sys.exit(1)

    auth = (user, password) if user else None
    print()

    with httpx.Client() as client:
        print("=== Сеть ===")
        try:
            ping = client.get(base.rstrip("/"), timeout=10)
            print(f"  GET {base} → HTTP {ping.status_code}")
        except Exception as exc:
            print(f"  GET {base} → FAIL: {exc}")

        print("\n=== GET без авторизации ===")
        for entity in ["$metadata", "Catalog_Организации"]:
            r = probe_get(client, base, entity, auth=None)
            print(f"  {r['entity']}: HTTP {r.get('status', '?')} {r.get('error', '')}")

        print("\n=== GET с авторизацией (.env) ===")
        read_ok = 0
        read_fail = 0
        for entity in ENTITIES_READ:
            r = probe_get(client, base, entity, auth=auth)
            status = r.get("status", "?")
            extra = ""
            if r.get("rows") is not None:
                extra = f", rows={r['rows']}"
            if r.get("metadata_bytes"):
                extra = f", bytes={r['metadata_bytes']}"
            if r.get("sample_keys"):
                extra += f", keys={r['sample_keys'][:4]}…"
            err = f" — {r['error']}" if r.get("error") else ""
            mark = "OK" if r.get("ok") else "FAIL"
            print(f"  [{mark}] {entity}: HTTP {status}{extra}{err}")
            if r.get("ok"):
                read_ok += 1
            else:
                read_fail += 1

        print(f"\n  Итого чтение: OK={read_ok}, FAIL={read_fail}")

        if args.try_post:
            print("\n=== POST (пробный документ) ===")
            r = probe_post_minimal(client, base, auth=auth)
            mark = "OK" if r.get("ok") else "FAIL"
            print(f"  [{mark}] {r['entity']}: HTTP {r.get('status', '?')}")
            if r.get("error"):
                print(f"    Ошибка: {r['error']}")
            if r.get("body_preview"):
                print(f"    Ответ: {r['body_preview'][:200]}")

    print("\n=== Рекомендации ===")
    if read_fail and read_ok == 0:
        print("  • Нет доступа на чтение — проверьте логин/пароль и публикацию OData в 1С.")
    elif read_ok and read_fail:
        print("  • Часть сущностей недоступна — в публикации OData не включены все каталоги.")
    if args.try_post:
        print("  • Если POST → «Нарушение прав доступа» — нужен пользователь с правом записи документа.")
        print("  • odata.user обычно только на чтение; для узла 7 нужен отдельный пользователь 1С.")


if __name__ == "__main__":
    main()
