"""TEMP/Aveon: выгрузка ресурсных спецификаций из 1С OData и запись в PostgreSQL."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from urllib.parse import quote

import requests
from requests.auth import HTTPBasicAuth
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.onec_odata import normalize_odata_base
from app.models.onec_resource_spec import (
    OnecResourceSpec,
    OnecResourceSpecMaterial,
    OnecResourceSpecOutput,
    OnecResourceSpecSyncRun,
)
from app.models.onec_nomenclature import OnecNomenclature

ONEC_BASE = normalize_odata_base("http://26.169.32.56/ERP2").rstrip("/")
ONEC_USER = "odata.user"
ONEC_PASSWORD = "npo852456"
EMPTY_GUID = "00000000-0000-0000-0000-000000000000"
PAGE_SIZE = 1000
LOOKUP_BATCH = 40
TIMEOUT_SEC = 120

SPEC_CATALOG = "Catalog_РесурсныеСпецификации"
MATERIALS_ENTITY = "Catalog_РесурсныеСпецификации_МатериалыИУслуги"
OUTPUTS_ENTITY = "Catalog_РесурсныеСпецификации_ВыходныеИзделия"
UNITS_CATALOG = "Catalog_УпаковкиЕдиницыИзмерения"

# TEMP: ветка 1С (Производство → Ресурсные спецификации)
SPEC_FOLDER_PATH = ("АВИОН", "Производство №2")
SPEC_EXCLUDED_DESCRIPTIONS = frozenset({"колесо под подшипник_kat_v1"})
ACTIVE_SPEC_STATUS = "действует"


def _get_json(session: requests.Session, url: str) -> dict:
    response = session.get(
        url,
        timeout=TIMEOUT_SEC,
        headers={"Accept": "application/json"},
    )
    response.encoding = "utf-8"
    if not response.ok:
        raise RuntimeError(f"HTTP {response.status_code}: {(response.text or '')[:300]}")
    return response.json()


def _fetch_all(http: requests.Session, entity: str, extra_query: str = "") -> list[dict]:
    rows: list[dict] = []
    skip = 0
    while True:
        if extra_query:
            url = (
                f"{ONEC_BASE}/{entity}?{extra_query}"
                f"&$top={PAGE_SIZE}&$skip={skip}&$format=json"
            )
        else:
            url = f"{ONEC_BASE}/{entity}?$top={PAGE_SIZE}&$skip={skip}&$format=json"
        batch = _get_json(http, url).get("value") or []
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        skip += len(batch)
    return rows


def _batch_lookup(
    http: requests.Session,
    catalog: str,
    keys: list[str],
    fields: str,
) -> dict[str, dict]:
    result: dict[str, dict] = {}
    unique = [k for k in dict.fromkeys(keys) if k and k != EMPTY_GUID]
    for i in range(0, len(unique), LOOKUP_BATCH):
        chunk = unique[i : i + LOOKUP_BATCH]
        filt = " or ".join(f"Ref_Key eq guid'{key}'" for key in chunk)
        url = (
            f"{ONEC_BASE}/{catalog}"
            f"?$filter={quote(filt, safe='')}"
            f"&$select={fields}&$format=json"
        )
        for row in _get_json(http, url).get("value") or []:
            ref = str(row.get("Ref_Key") or "")
            if ref:
                result[ref] = row
    return result


def _parse_odata_date(raw: object) -> date | None:
    if raw is None or raw == "":
        return None
    text = str(raw).strip()
    if not text or text.startswith("0001-01-01"):
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _line_number(raw: object) -> int:
    try:
        return int(str(raw).strip() or "0")
    except ValueError:
        return 0


def _normalize_spec_label(value: object) -> str:
    return str(value or "").strip().casefold().replace("ё", "е")


def _is_active_spec_header(header: dict) -> bool:
    if header.get("IsFolder"):
        return False
    if bool(header.get("DeletionMark")):
        return False
    status = _normalize_spec_label(header.get("Статус"))
    return status == ACTIVE_SPEC_STATUS


def _is_excluded_spec_header(header: dict) -> bool:
    description = _normalize_spec_label(header.get("Description"))
    return description in SPEC_EXCLUDED_DESCRIPTIONS


def _filter_importable_spec_headers(headers: list[dict]) -> list[dict]:
    """Только листовые спецификации со статусом «Действует», без явных исключений."""
    filtered: list[dict] = []
    for header in headers:
        if not _is_active_spec_header(header):
            continue
        if _is_excluded_spec_header(header):
            continue
        filtered.append(header)
    return filtered


def _find_child_folder(
    http: requests.Session,
    parent_key: str | None,
    name: str,
) -> dict:
    """Ищет папку по Description среди детей parent_key (None = корень)."""
    parent = parent_key or EMPTY_GUID
    kids = _fetch_all(
        http,
        SPEC_CATALOG,
        f"$filter=IsFolder eq true and Parent_Key eq guid'{parent}'"
        f"&$select=Ref_Key,Code,Description,Parent_Key,IsFolder",
    )
    rows = [r for r in kids if str(r.get("Description") or "").strip() == name]
    if not rows:
        raise RuntimeError(f"Папка спецификаций не найдена: «{name}» (parent={parent})")
    return rows[0]


def _resolve_folder_path(http: requests.Session, path: tuple[str, ...]) -> dict:
    parent: str | None = None
    current: dict = {}
    for name in path:
        current = _find_child_folder(http, parent, name)
        parent = str(current.get("Ref_Key") or "")
    return current


def _collect_specs_under_folder(http: requests.Session, folder_key: str) -> tuple[set[str], list[dict]]:
    """Все листовые спецификации в папке и подпапках + множество GUID папок ветки."""
    from collections import deque

    folder_keys: set[str] = {folder_key}
    queue: deque[str] = deque([folder_key])
    spec_keys: set[str] = set()

    while queue:
        current = queue.popleft()
        rows = _fetch_all(
            http,
            SPEC_CATALOG,
            f"$filter=Parent_Key eq guid'{current}'"
            f"&$select=Ref_Key,Code,Description,Parent_Key,IsFolder,DeletionMark",
        )
        for row in rows:
            ref = str(row.get("Ref_Key") or "")
            if not ref:
                continue
            if row.get("IsFolder"):
                if ref not in folder_keys:
                    folder_keys.add(ref)
                    queue.append(ref)
            else:
                spec_keys.add(ref)

    if not spec_keys:
        return folder_keys, []

    headers: list[dict] = []
    unique = list(spec_keys)
    for i in range(0, len(unique), LOOKUP_BATCH):
        chunk = unique[i : i + LOOKUP_BATCH]
        filt = " or ".join(f"Ref_Key eq guid'{key}'" for key in chunk)
        url = (
            f"{ONEC_BASE}/{SPEC_CATALOG}"
            f"?$filter={quote(filt, safe='')}"
            f"&$format=json"
        )
        headers.extend(_get_json(http, url).get("value") or [])

    # только не-папки (на всякий случай)
    headers = [h for h in headers if not h.get("IsFolder")]
    return folder_keys, headers


def _fetch_tabular_for_specs(
    http: requests.Session,
    entity: str,
    spec_keys: set[str],
) -> list[dict]:
    """Строки ТЧ только для нужных спецификаций (батч по Ref_Key)."""
    if not spec_keys:
        return []
    rows: list[dict] = []
    unique = list(spec_keys)
    for i in range(0, len(unique), LOOKUP_BATCH):
        chunk = unique[i : i + LOOKUP_BATCH]
        filt = " or ".join(f"Ref_Key eq guid'{key}'" for key in chunk)
        # пагинация внутри батча
        skip = 0
        while True:
            url = (
                f"{ONEC_BASE}/{entity}"
                f"?$filter={quote(filt, safe='')}"
                f"&$top={PAGE_SIZE}&$skip={skip}&$format=json"
            )
            batch = _get_json(http, url).get("value") or []
            rows.extend(batch)
            if len(batch) < PAGE_SIZE:
                break
            skip += len(batch)
    return rows


def fetch_resource_specs_from_onec(
    folder_path: tuple[str, ...] = SPEC_FOLDER_PATH,
) -> dict:
    """Тянет листовые ресурсные спецификации только из указанной ветки папок."""
    http = requests.Session()
    http.auth = HTTPBasicAuth(ONEC_USER, ONEC_PASSWORD)
    try:
        folder = _resolve_folder_path(http, folder_path)
        folder_key = str(folder.get("Ref_Key") or "")
        folder_path_label = " / ".join(folder_path)
        _folder_keys, headers = _collect_specs_under_folder(http, folder_key)
        headers = _filter_importable_spec_headers(headers)
        header_keys = {
            str(h.get("Ref_Key") or "")
            for h in headers
            if h.get("Ref_Key")
        }

        materials_raw = _fetch_tabular_for_specs(http, MATERIALS_ENTITY, header_keys)
        outputs_raw = _fetch_tabular_for_specs(http, OUTPUTS_ENTITY, header_keys)

        nom_keys: list[str] = []
        for h in headers:
            nom_keys.append(str(h.get("ОсновноеИзделиеНоменклатура_Key") or ""))
        for m in materials_raw:
            nom_keys.append(str(m.get("Номенклатура_Key") or ""))
        for o in outputs_raw:
            nom_keys.append(str(o.get("Номенклатура_Key") or ""))

        nom_map = _batch_lookup(
            http,
            "Catalog_Номенклатура",
            nom_keys,
            "Ref_Key,Code,Description,СтранаПроисхождения_Key,ЕдиницаИзмерения_Key",
        )
        country_keys = [
            str(nom.get("СтранаПроисхождения_Key") or "")
            for nom in nom_map.values()
        ]
        country_map = _batch_lookup(
            http, "Catalog_СтраныМира", country_keys, "Ref_Key,Code,Description"
        )

        unit_keys: list[str] = []
        for nom in nom_map.values():
            unit_keys.append(str(nom.get("ЕдиницаИзмерения_Key") or ""))
        for row in materials_raw:
            unit_keys.append(str(row.get("Упаковка_Key") or ""))
        unit_map = _batch_lookup(
            http,
            UNITS_CATALOG,
            unit_keys,
            "Ref_Key,Code,Description",
        )

        def _unit_name(unit_key: object) -> str:
            key = str(unit_key or "")
            if not key or key == EMPTY_GUID:
                return ""
            unit = unit_map.get(key) or {}
            return str(unit.get("Description") or "").strip()

        def _material_unit(material_row: dict, nom: dict) -> tuple[str, str]:
            packaging_key = str(material_row.get("Упаковка_Key") or "")
            unit = _unit_name(packaging_key)
            if unit:
                return unit, packaging_key
            nom_unit_key = str(nom.get("ЕдиницаИзмерения_Key") or "")
            return _unit_name(nom_unit_key), nom_unit_key

        def _country_for(nom: dict) -> tuple[str, str]:
            country_key = str(nom.get("СтранаПроисхождения_Key") or "")
            if not country_key or country_key == EMPTY_GUID:
                return "", ""
            country = country_map.get(country_key) or {}
            return country_key, str(country.get("Description") or "").strip()

        materials_by_spec: dict[str, list[dict]] = {}
        for row in materials_raw:
            spec_key = str(row.get("Ref_Key") or "")
            nom_key = str(row.get("Номенклатура_Key") or "")
            nom = nom_map.get(nom_key) or {}
            country_key, country_name = _country_for(nom)
            unit_name, unit_key = _material_unit(row, nom)
            materials_by_spec.setdefault(spec_key, []).append(
                {
                    "line_number": _line_number(row.get("LineNumber")),
                    "nomenclature_key": nom_key,
                    "nomenclature_code": str(nom.get("Code") or ""),
                    "nomenclature_name": str(nom.get("Description") or "").strip(),
                    "country_key": country_key,
                    "country_of_origin": country_name,
                    "unit_key": unit_key if unit_key != EMPTY_GUID else "",
                    "unit": unit_name,
                    "characteristic_key": str(row.get("Характеристика_Key") or EMPTY_GUID),
                    "qty": float(row.get("КоличествоУпаковок") or 0),
                    "packaging_key": str(row.get("Упаковка_Key") or EMPTY_GUID),
                    "produced_in_process": bool(row.get("ПроизводитсяВПроцессе")),
                    "alternative": bool(row.get("Альтернативный")),
                }
            )

        outputs_by_spec: dict[str, list[dict]] = {}
        for row in outputs_raw:
            spec_key = str(row.get("Ref_Key") or "")
            nom_key = str(row.get("Номенклатура_Key") or "")
            nom = nom_map.get(nom_key) or {}
            country_key, country_name = _country_for(nom)
            outputs_by_spec.setdefault(spec_key, []).append(
                {
                    "line_number": _line_number(row.get("LineNumber")),
                    "nomenclature_key": nom_key,
                    "nomenclature_code": str(nom.get("Code") or ""),
                    "nomenclature_name": str(nom.get("Description") or "").strip(),
                    "country_key": country_key,
                    "country_of_origin": country_name,
                    "characteristic_key": str(row.get("Характеристика_Key") or EMPTY_GUID),
                    "qty": float(row.get("КоличествоУпаковок") or 0),
                    "packaging_key": str(row.get("Упаковка_Key") or EMPTY_GUID),
                    "description": str(row.get("ОписаниеИзделия") or "").strip(),
                }
            )

        nomenclature_items: dict[str, dict] = {}
        for nom_key, nom in nom_map.items():
            if not nom_key or nom_key == EMPTY_GUID:
                continue
            country_key, country_name = _country_for(nom)
            nom_unit_key = str(nom.get("ЕдиницаИзмерения_Key") or "")
            nomenclature_items[nom_key] = {
                "ref_key": nom_key,
                "code": str(nom.get("Code") or ""),
                "name": str(nom.get("Description") or "").strip(),
                "country_key": country_key,
                "country_of_origin": country_name,
                "unit_key": nom_unit_key if nom_unit_key != EMPTY_GUID else "",
                "unit": _unit_name(nom_unit_key),
            }

        specs: list[dict] = []
        for h in headers:
            ref_key = str(h.get("Ref_Key") or "")
            main_key = str(h.get("ОсновноеИзделиеНоменклатура_Key") or "")
            main = nom_map.get(main_key) or {}
            mats = materials_by_spec.get(ref_key) or []
            outs = outputs_by_spec.get(ref_key) or []
            mats.sort(key=lambda x: x["line_number"])
            outs.sort(key=lambda x: x["line_number"])
            specs.append(
                {
                    "ref_key": ref_key,
                    "code": str(h.get("Code") or ""),
                    "description": str(h.get("Description") or "").strip(),
                    "status": str(h.get("Статус") or ""),
                    "process_type": str(h.get("ТипПроизводственногоПроцесса") or ""),
                    "is_folder": bool(h.get("IsFolder")),
                    "deletion_mark": bool(h.get("DeletionMark")),
                    "main_product_key": main_key if main_key != EMPTY_GUID else "",
                    "main_product_code": str(main.get("Code") or ""),
                    "main_product_name": str(main.get("Description") or "").strip(),
                    "main_product_qty": float(h.get("ОсновноеИзделиеКоличествоУпаковок") or 0),
                    "valid_from": _parse_odata_date(h.get("НачалоДействия")),
                    "valid_to": _parse_odata_date(h.get("КонецДействия")),
                    "materials": mats,
                    "outputs": outs,
                    "materials_count": len(mats),
                    "outputs_count": len(outs),
                }
            )

        specs.sort(key=lambda x: (x["description"].lower(), x["code"]))
        materials_total = sum(s["materials_count"] for s in specs)
        outputs_total = sum(s["outputs_count"] for s in specs)
        items = [
            {
                "ref_key": s["ref_key"],
                "code": s["code"],
                "description": s["description"],
                "status": s["status"],
                "process_type": s["process_type"],
                "main_product_code": s["main_product_code"],
                "main_product": s["main_product_name"],
                "main_product_qty": s["main_product_qty"],
                "materials_count": s["materials_count"],
                "outputs_count": s["outputs_count"],
            }
            for s in specs
        ]
        materials_rows = [
            {
                "spec_code": s["code"],
                "spec_name": s["description"],
                "line": m["line_number"],
                "code": m["nomenclature_code"],
                "name": m["nomenclature_name"],
                "qty": m["qty"],
            }
            for s in specs
            for m in s["materials"]
        ]
        outputs_rows = [
            {
                "spec_code": s["code"],
                "spec_name": s["description"],
                "line": o["line_number"],
                "code": o["nomenclature_code"],
                "name": o["nomenclature_name"],
                "qty": o["qty"],
            }
            for s in specs
            for o in s["outputs"]
        ]
        return {
            "ok": True,
            "message": (
                f"Ресурсные спецификации из «{folder_path_label}» "
                f"(статус «{ACTIVE_SPEC_STATUS}», без исключений): {len(specs)} шт., "
                f"материалов {materials_total}, выходных изделий {outputs_total}"
            ),
            "status_code": 200,
            "url": f"{ONEC_BASE}/{SPEC_CATALOG}",
            "base": ONEC_BASE,
            "source": SPEC_CATALOG,
            "folder_path": list(folder_path),
            "folder_ref_key": folder_key,
            "count": len(specs),
            "materials_count": materials_total,
            "outputs_count": outputs_total,
            "items": items,
            "materials": materials_rows,
            "outputs": outputs_rows,
            "specs": specs,
            "nomenclature_items": list(nomenclature_items.values()),
        }
    except (requests.RequestException, RuntimeError, ValueError, TypeError) as exc:
        return {
            "ok": False,
            "message": f"Не удалось получить ресурсные спецификации из 1С: {exc}",
            "status_code": None,
            "url": f"{ONEC_BASE}/{SPEC_CATALOG}",
            "base": ONEC_BASE,
            "source": SPEC_CATALOG,
            "folder_path": list(folder_path),
            "count": 0,
            "materials_count": 0,
            "outputs_count": 0,
            "items": [],
            "materials": [],
            "outputs": [],
            "specs": [],
            "nomenclature_items": [],
        }
    finally:
        http.close()


async def ensure_onec_resource_spec_tables() -> None:
    from app.services.onec_db_schema import ensure_onec_agent_tables

    await ensure_onec_agent_tables()


async def replace_resource_specs_in_db(
    db: AsyncSession,
    specs: list[dict],
    nomenclature_items: list[dict] | None = None,
) -> dict:
    """Полная перезапись спецификаций в БД."""
    await ensure_onec_resource_spec_tables()
    started = datetime.now(timezone.utc)
    materials_total = sum(len(s.get("materials") or []) for s in specs)
    outputs_total = sum(len(s.get("outputs") or []) for s in specs)
    run = OnecResourceSpecSyncRun(
        id=uuid.uuid4(),
        source=SPEC_CATALOG,
        status="running",
        specs_count=len(specs),
        materials_count=materials_total,
        outputs_count=outputs_total,
        saved_specs=0,
        saved_materials=0,
        saved_outputs=0,
        started_at=started,
    )
    db.add(run)
    await db.flush()

    await db.execute(delete(OnecResourceSpecMaterial))
    await db.execute(delete(OnecResourceSpecOutput))
    await db.execute(delete(OnecResourceSpec))
    await db.execute(delete(OnecNomenclature))

    now = datetime.now(timezone.utc)
    header_rows: list[OnecResourceSpec] = []
    material_rows: list[OnecResourceSpecMaterial] = []
    output_rows: list[OnecResourceSpecOutput] = []
    nomenclature_by_key: dict[str, dict] = {}

    if nomenclature_items:
        for item in nomenclature_items:
            ref_key = str(item.get("ref_key") or "")
            if ref_key and ref_key != EMPTY_GUID:
                nomenclature_by_key[ref_key] = item

    for spec in specs:
        ref_key = str(spec.get("ref_key") or "")
        if not ref_key:
            continue
        header_rows.append(
            OnecResourceSpec(
                id=uuid.uuid4(),
                ref_key=ref_key,
                code=str(spec.get("code") or ""),
                description=str(spec.get("description") or ""),
                status=str(spec.get("status") or ""),
                process_type=str(spec.get("process_type") or ""),
                is_folder=bool(spec.get("is_folder")),
                deletion_mark=bool(spec.get("deletion_mark")),
                main_product_key=str(spec.get("main_product_key") or ""),
                main_product_code=str(spec.get("main_product_code") or ""),
                main_product_name=str(spec.get("main_product_name") or ""),
                main_product_qty=float(spec.get("main_product_qty") or 0),
                valid_from=spec.get("valid_from"),
                valid_to=spec.get("valid_to"),
                materials_count=int(spec.get("materials_count") or 0),
                outputs_count=int(spec.get("outputs_count") or 0),
                synced_at=now,
            )
        )
        for mat in spec.get("materials") or []:
            nom_key = str(mat.get("nomenclature_key") or "")
            if nom_key and nom_key != EMPTY_GUID and nom_key not in nomenclature_by_key:
                nomenclature_by_key[nom_key] = {
                    "ref_key": nom_key,
                    "code": str(mat.get("nomenclature_code") or ""),
                    "name": str(mat.get("nomenclature_name") or ""),
                    "country_key": str(mat.get("country_key") or ""),
                    "country_of_origin": str(mat.get("country_of_origin") or ""),
                    "unit_key": str(mat.get("unit_key") or ""),
                    "unit": str(mat.get("unit") or ""),
                }
            material_rows.append(
                OnecResourceSpecMaterial(
                    id=uuid.uuid4(),
                    spec_ref_key=ref_key,
                    line_number=int(mat.get("line_number") or 0),
                    nomenclature_key=nom_key,
                    nomenclature_code=str(mat.get("nomenclature_code") or ""),
                    nomenclature_name=str(mat.get("nomenclature_name") or ""),
                    characteristic_key=str(mat.get("characteristic_key") or EMPTY_GUID),
                    qty=float(mat.get("qty") or 0),
                    packaging_key=str(mat.get("packaging_key") or EMPTY_GUID),
                    unit=str(mat.get("unit") or ""),
                    produced_in_process=bool(mat.get("produced_in_process")),
                    alternative=bool(mat.get("alternative")),
                )
            )
        for out in spec.get("outputs") or []:
            nom_key = str(out.get("nomenclature_key") or "")
            if nom_key and nom_key != EMPTY_GUID and nom_key not in nomenclature_by_key:
                nomenclature_by_key[nom_key] = {
                    "ref_key": nom_key,
                    "code": str(out.get("nomenclature_code") or ""),
                    "name": str(out.get("nomenclature_name") or ""),
                    "country_key": str(out.get("country_key") or ""),
                    "country_of_origin": str(out.get("country_of_origin") or ""),
                    "unit_key": "",
                    "unit": "",
                }
            output_rows.append(
                OnecResourceSpecOutput(
                    id=uuid.uuid4(),
                    spec_ref_key=ref_key,
                    line_number=int(out.get("line_number") or 0),
                    nomenclature_key=nom_key,
                    nomenclature_code=str(out.get("nomenclature_code") or ""),
                    nomenclature_name=str(out.get("nomenclature_name") or ""),
                    characteristic_key=str(out.get("characteristic_key") or EMPTY_GUID),
                    qty=float(out.get("qty") or 0),
                    packaging_key=str(out.get("packaging_key") or EMPTY_GUID),
                    description=str(out.get("description") or ""),
                )
            )

    db.add_all(header_rows)
    await db.flush()
    db.add_all(material_rows)
    db.add_all(output_rows)

    nomenclature_rows = [
        OnecNomenclature(
            id=uuid.uuid4(),
            ref_key=str(item.get("ref_key") or ""),
            code=str(item.get("code") or ""),
            name=str(item.get("name") or ""),
            country_key=str(item.get("country_key") or ""),
            country_of_origin=str(item.get("country_of_origin") or ""),
            unit_key=str(item.get("unit_key") or ""),
            unit=str(item.get("unit") or ""),
            synced_at=now,
        )
        for item in nomenclature_by_key.values()
        if str(item.get("ref_key") or "")
    ]
    if nomenclature_rows:
        db.add_all(nomenclature_rows)

    run.saved_specs = len(header_rows)
    run.saved_materials = len(material_rows)
    run.saved_outputs = len(output_rows)
    run.status = "ok"
    run.finished_at = datetime.now(timezone.utc)
    await db.flush()

    db_specs = await db.scalar(select(func.count()).select_from(OnecResourceSpec))
    db_mats = await db.scalar(select(func.count()).select_from(OnecResourceSpecMaterial))
    db_outs = await db.scalar(select(func.count()).select_from(OnecResourceSpecOutput))
    db_noms = await db.scalar(select(func.count()).select_from(OnecNomenclature))
    return {
        "sync_run_id": str(run.id),
        "saved_specs": len(header_rows),
        "saved_materials": len(material_rows),
        "saved_outputs": len(output_rows),
        "saved_nomenclature": len(nomenclature_rows),
        "db_specs": int(db_specs or 0),
        "db_materials": int(db_mats or 0),
        "db_outputs": int(db_outs or 0),
        "db_nomenclature": int(db_noms or 0),
    }


async def get_resource_spec_sync_status(db: AsyncSession, *, ensure: bool = True) -> dict:
    """Последняя выгрузка ресурсных спецификаций для UI."""
    if ensure:
        await ensure_onec_resource_spec_tables()
    latest_run = await db.scalar(
        select(OnecResourceSpecSyncRun)
        .order_by(OnecResourceSpecSyncRun.finished_at.desc().nullslast())
        .limit(1)
    )
    db_specs = int(await db.scalar(select(func.count()).select_from(OnecResourceSpec)) or 0)
    db_materials = int(
        await db.scalar(select(func.count()).select_from(OnecResourceSpecMaterial)) or 0
    )
    db_outputs = int(await db.scalar(select(func.count()).select_from(OnecResourceSpecOutput)) or 0)
    if latest_run is None:
        return {
            "last_sync_at": None,
            "status": None,
            "specs_count": 0,
            "materials_count": 0,
            "outputs_count": 0,
            "db_specs": db_specs,
            "db_materials": db_materials,
            "db_outputs": db_outputs,
            "error_message": None,
        }
    return {
        "last_sync_at": latest_run.finished_at.isoformat() if latest_run.finished_at else None,
        "status": latest_run.status,
        "specs_count": latest_run.saved_specs,
        "materials_count": latest_run.saved_materials,
        "outputs_count": latest_run.saved_outputs,
        "db_specs": db_specs,
        "db_materials": db_materials,
        "db_outputs": db_outputs,
        "error_message": latest_run.error_message,
    }


async def list_resource_specs_from_db(
    db: AsyncSession,
    *,
    status: str | None = None,
    query: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> dict:
    """Список спецификаций из БД для дальнейшей работы."""
    await ensure_onec_resource_spec_tables()
    stmt = select(OnecResourceSpec).order_by(OnecResourceSpec.description, OnecResourceSpec.code)
    if status:
        stmt = stmt.where(OnecResourceSpec.status == status)
    if query:
        like = f"%{query.strip()}%"
        stmt = stmt.where(
            (OnecResourceSpec.description.ilike(like))
            | (OnecResourceSpec.code.ilike(like))
            | (OnecResourceSpec.main_product_name.ilike(like))
        )
    total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = (await db.execute(stmt.offset(offset).limit(limit))).scalars().all()
    return {
        "ok": True,
        "total": int(total or 0),
        "limit": limit,
        "offset": offset,
        "items": [
            {
                "ref_key": r.ref_key,
                "code": r.code,
                "description": r.description,
                "status": r.status,
                "process_type": r.process_type,
                "main_product_key": r.main_product_key,
                "main_product_code": r.main_product_code,
                "main_product_name": r.main_product_name,
                "main_product_qty": r.main_product_qty,
                "materials_count": r.materials_count,
                "outputs_count": r.outputs_count,
                "valid_from": r.valid_from.isoformat() if r.valid_from else None,
                "valid_to": r.valid_to.isoformat() if r.valid_to else None,
                "synced_at": r.synced_at.isoformat() if r.synced_at else None,
            }
            for r in rows
        ],
    }


async def get_resource_spec_from_db(db: AsyncSession, ref_key: str) -> dict | None:
    """Одна спецификация с материалами и выходными изделиями — готовый BOM для работы."""
    await ensure_onec_resource_spec_tables()
    header = (
        await db.execute(select(OnecResourceSpec).where(OnecResourceSpec.ref_key == ref_key))
    ).scalar_one_or_none()
    if header is None:
        return None

    materials = (
        await db.execute(
            select(OnecResourceSpecMaterial)
            .where(OnecResourceSpecMaterial.spec_ref_key == ref_key)
            .order_by(OnecResourceSpecMaterial.line_number)
        )
    ).scalars().all()
    outputs = (
        await db.execute(
            select(OnecResourceSpecOutput)
            .where(OnecResourceSpecOutput.spec_ref_key == ref_key)
            .order_by(OnecResourceSpecOutput.line_number)
        )
    ).scalars().all()

    return {
        "ref_key": header.ref_key,
        "code": header.code,
        "description": header.description,
        "status": header.status,
        "process_type": header.process_type,
        "deletion_mark": header.deletion_mark,
        "main_product": {
            "key": header.main_product_key,
            "code": header.main_product_code,
            "name": header.main_product_name,
            "qty": header.main_product_qty,
        },
        "valid_from": header.valid_from.isoformat() if header.valid_from else None,
        "valid_to": header.valid_to.isoformat() if header.valid_to else None,
        "synced_at": header.synced_at.isoformat() if header.synced_at else None,
        "materials": [
            {
                "line_number": m.line_number,
                "nomenclature_key": m.nomenclature_key,
                "code": m.nomenclature_code,
                "name": m.nomenclature_name,
                "qty": m.qty,
                "characteristic_key": m.characteristic_key,
                "produced_in_process": m.produced_in_process,
                "alternative": m.alternative,
            }
            for m in materials
        ],
        "outputs": [
            {
                "line_number": o.line_number,
                "nomenclature_key": o.nomenclature_key,
                "code": o.nomenclature_code,
                "name": o.nomenclature_name,
                "qty": o.qty,
                "characteristic_key": o.characteristic_key,
                "description": o.description,
            }
            for o in outputs
        ],
    }
