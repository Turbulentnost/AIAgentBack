"""Seed OTK presentation cards from 1C OData (limited).

Pulls «Предъявление ТМЦ на ОТК» for the last N days OR $top=M (whichever
practical filter works), maps into the JSON store used by quality_engineer_agent.

Usage:
  py -3 scripts/seed_otk_presentations_from_odata.py --days 30 --top 100
  py -3 scripts/seed_otk_presentations_from_odata.py --top 50 --dry-run
  py -3 scripts/seed_otk_presentations_from_odata.py --keep-mock   # opt-in: keep legacy mock cards

Default: replace store with OData-only cards (drop mock ids that are not pres-1c-*).
Pass --keep-mock only if you intentionally want mock + OData mixed.

Writes:
  - app/agents/quality_engineer_agent/data/otk_presentations.json
  - AIAgentFront/src/pages/otk/otk_presentations.seed.json  (optional mirror)

Credentials: AIAgentBack/.env (ODATA_* / ONEC_ODATA_*). Never prints passwords.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
BACK_OUT = (
    ROOT
    / "app"
    / "agents"
    / "quality_engineer_agent"
    / "data"
    / "otk_presentations.json"
)
FRONT_OUT = (
    ROOT.parent
    / "AIAgentFront"
    / "src"
    / "pages"
    / "otk"
    / "otk_presentations.seed.json"
)

ENTITY = "Document_ТД_ПредъявлениеТМЦнаОТК"
EMPTY_GUID = "00000000-0000-0000-0000-000000000000"

DEFAULT_WORKERS = [
    {"id": "otk-w-1", "name": "Иванова А.С.", "position": "Инженер по качеству"},
    {"id": "otk-w-2", "name": "Петров Д.И.", "position": "Инженер по качеству"},
    {"id": "otk-w-3", "name": "Сидорова М.В.", "position": "Инженер ОТК"},
]


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


class ODataClient:
    def __init__(self, base: str, user: str, password: str, timeout: int = 120) -> None:
        self.base = base.rstrip("/")
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        self.auth = f"Basic {token}"
        self.timeout = timeout
        self._cache: dict[str, dict[str, Any]] = {}

    def get_json(self, path_and_query: str) -> Any:
        url = path_and_query if path_and_query.startswith("http") else f"{self.base}/{path_and_query.lstrip('/')}"
        req = Request(
            url,
            headers={"Authorization": self.auth, "Accept": "application/json"},
        )
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:400]
            raise RuntimeError(f"HTTP {e.code} for {url}: {body}") from e
        except URLError as e:
            raise RuntimeError(f"URL error for {url}: {e.reason}") from e
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"Request failed for {url}: {e}") from e

    def entity_get(
        self,
        entity: str,
        *,
        top: int | None = None,
        orderby: str | None = None,
        filter_expr: str | None = None,
        select: str | None = None,
        expand: str | None = None,
    ) -> list[dict[str, Any]]:
        parts: list[str] = ["$format=json"]
        if top is not None:
            parts.append(f"$top={top}")
        if orderby:
            # Encode spaces as %20 — http.client rejects raw spaces in path.
            parts.append(f"$orderby={quote(orderby, safe=',')}")
        if filter_expr:
            parts.append("$filter=" + quote(filter_expr, safe="'()"))
        if select:
            parts.append(f"$select={quote(select, safe=',_')}")
        if expand:
            parts.append(f"$expand={quote(expand, safe=',_')}")
        data = self.get_json(f"{quote(entity)}?{'&'.join(parts)}")
        if not isinstance(data, dict):
            return []
        return list(data.get("value") or [])

    def resolve_ref(
        self,
        entity: str,
        key: str,
        *,
        fields: tuple[str, ...] = ("Description", "Code", "DescriptionFull"),
    ) -> dict[str, Any]:
        if not key or key == EMPTY_GUID:
            return {}
        cache_key = f"{entity}:{key}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        select = ",".join(fields)
        try:
            data = self.get_json(
                f"{quote(entity)}(guid'{key}')?$format=json&$select={quote(select, safe=',')}"
            )
        except RuntimeError:
            try:
                data = self.get_json(f"{quote(entity)}(guid'{key}')?$format=json")
            except RuntimeError:
                data = {}
        if not isinstance(data, dict):
            data = {}
        self._cache[cache_key] = data
        return data


def _is_empty_date(value: Any) -> bool:
    if value is None or value == "":
        return True
    s = str(value)
    return s.startswith("0001-01-01")


def _date_only(value: Any) -> str:
    if _is_empty_date(value):
        return ""
    s = str(value)
    return s[:10]


def _iso_due(value: Any, fallback: str = "") -> str:
    if _is_empty_date(value):
        return fallback
    s = str(value)
    if "T" not in s:
        return f"{s}T17:00:00+03:00"
    if s.endswith("Z") or "+" in s[10:] or s.count("-") > 2:
        return s
    return f"{s}+03:00"


def map_status(doc: dict[str, Any]) -> str:
    """Map 1C Состояние/ЭтапДокумента → OTK UI status."""
    state = str(doc.get("Состояние") or "")
    stage = str(doc.get("ЭтапДокумента") or "")
    done_markers = (
        "Завершен",
        "Завершён",
        "Выполнен",
        "Закрыт",
        "Принят",
        "Проверен",
        "Готов",
    )
    progress_markers = ("Исполнен", "Проверк", "ОТК", "Работ")
    blob = f"{state} {stage}"
    if any(m in blob for m in done_markers) and "НаИсполнении" not in blob:
        return "done"
    if not _is_empty_date(doc.get("ДатаЗавершения")):
        return "done"
    if any(m in blob for m in progress_markers):
        return "in_progress"
    return "queued"


def pick_description(row: dict[str, Any]) -> str:
    for key in ("Description", "DescriptionFull", "Наименование", "Description_en"):
        val = row.get(key)
        if val:
            return str(val)
    return ""


def pick_code(row: dict[str, Any]) -> str:
    for key in ("Code", "Артикул", "Код"):
        val = row.get(key)
        if val:
            return str(val)
    return ""


def guess_category(name: str, code: str) -> str:
    text = f"{name} {code}".lower()
    rules = [
        ("cable", ("кабел", "провод", "ввг", "кг ")),
        ("pipes", ("труб", "отвод", "тройник")),
        ("flanges", ("фланец", "фланц")),
        ("gaskets", ("проклад", "уплотн", "паронит")),
        ("fasteners", ("болт", "гайк", "шайб", "шпил", "винт")),
        ("electronics", ("микросхем", "контактор", "автомат", "датчик", "термопар", "преобразовател")),
        ("metal", ("лист", "уголок", "швеллер", "двутавр", "круг", "полоса", "сталь")),
        ("drawing_parts", ("чертеж", "по чертежу")),
    ]
    for cat, needles in rules:
        if any(n in text for n in needles):
            return cat
    return "other"


def cycle_worker(index: int, workers: list[dict[str, Any]]) -> str:
    if not workers:
        return ""
    return str(workers[index % len(workers)]["id"])


def fetch_documents(client: ODataClient, *, days: int, top: int) -> tuple[list[dict[str, Any]], str]:
    """Prefer Date filter for last `days`; fall back to orderby+$top."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    # 1C OData often wants datetime'YYYY-MM-DDTHH:MM:SS'
    since_lit = since.strftime("%Y-%m-%dT00:00:00")
    filter_expr = f"Date ge datetime'{since_lit}' and DeletionMark eq false"
    try:
        rows = client.entity_get(
            ENTITY,
            top=top,
            orderby="Date desc",
            filter_expr=filter_expr,
        )
        if rows:
            return rows, f"filter Date ge {since_lit} & $top={top} orderby Date desc"
    except RuntimeError as e:
        print(f"Date filter failed, fallback to $top: {e}", file=sys.stderr)

    rows = client.entity_get(
        ENTITY,
        top=top,
        orderby="Date desc",
        filter_expr="DeletionMark eq false",
    )
    return rows, f"$filter DeletionMark eq false & $orderby Date desc & $top={top}"


def map_document(
    client: ODataClient,
    doc: dict[str, Any],
    *,
    index: int,
    workers: list[dict[str, Any]],
) -> dict[str, Any]:
    ref = str(doc.get("Ref_Key") or "")
    number = str(doc.get("Number") or "").strip()
    supplier_row = client.resolve_ref("Catalog_Контрагенты", str(doc.get("Контрагент_Key") or ""))
    org_row = client.resolve_ref("Catalog_Организации", str(doc.get("Организация_Key") or ""))
    wh_row = client.resolve_ref("Catalog_Склады", str(doc.get("Склад_Key") or ""))
    otk_wh_row = client.resolve_ref(
        "Catalog_Склады", str(doc.get("СкладВходногоКонтроля_Key") or "")
    )

    supplier = pick_description(supplier_row) or "Контрагент"
    organization = pick_description(org_row) or "ООО НПО «Турбулентность-Дон»"
    warehouse = pick_description(wh_row) or "Склад"
    otk_wh = pick_description(otk_wh_row) or "Склад входного контроля ОТК"

    purchase_order = ""
    basis = str(doc.get("ДокументОснование") or "")
    basis_type = str(doc.get("ДокументОснование_Type") or "")
    if basis and basis != EMPTY_GUID and "ЗаказПоставщику" in basis_type:
        po = client.resolve_ref("Document_ЗаказПоставщику", basis, fields=("Number", "Date"))
        purchase_order = str(po.get("Number") or "").strip()

    lines_src = doc.get("Товары") or doc.get("ТоварыДляОТК") or []
    if not isinstance(lines_src, list):
        lines_src = []

    lines: list[dict[str, Any]] = []
    for i, raw in enumerate(lines_src):
        if not isinstance(raw, dict):
            continue
        nom_key = str(raw.get("Номенклатура_Key") or "")
        nom = client.resolve_ref("Catalog_Номенклатура", nom_key)
        name = pick_description(nom) or f"Номенклатура {nom_key[:8]}"
        code = pick_code(nom)
        qty_upd = float(raw.get("КоличествоВУПД") or raw.get("Количество") or 0)
        qty_accepted_raw = (
            raw.get("КоличествоПринятыхНаОТК")
            if raw.get("КоличествоПринятыхНаОТК") not in (None, "")
            else raw.get("ПринятоОТК")
        )
        qty_fact = float(qty_accepted_raw or raw.get("Количество") or 0)
        # If not yet accepted at OTK, show UPD qty as fact placeholder for UI testing
        if qty_fact <= 0 and qty_upd > 0:
            qty_fact = qty_upd
        checked = raw.get("ПровереноОТК")
        if isinstance(checked, bool):
            accepted = checked
        else:
            try:
                accepted = float(qty_accepted_raw or 0) > 0
            except (TypeError, ValueError):
                accepted = False
        unit = "шт"
        for ukey in ("ЕдиницаИзмерения", "Unit", "БазоваяЕдиницаИзмерения"):
            if nom.get(ukey):
                unit = str(nom[ukey])
                break
        lines.append(
            {
                "id": f"l-{ref[:8]}-{raw.get('LineNumber', i + 1)}",
                "code": code,
                "nomenclature": name,
                "storage_unit": unit,
                "qty_upd": qty_upd,
                "qty_fact": qty_fact,
                "category": guess_category(name, code),
                "supplier_quality_rating": None,
                "accepted": accepted,
            }
        )

    invoice_date = _date_only(doc.get("ДатаНакладной")) or _date_only(doc.get("Date"))
    invoice_number = str(doc.get("НомерНакладной") or "").strip() or number
    due_at = _iso_due(
        doc.get("СрокИсполнения"),
        fallback=_iso_due(doc.get("Date"), fallback=""),
    )
    status = map_status(doc)
    pid = f"pres-1c-{number or ref[:8]}"

    return {
        "id": pid,
        "organization": organization,
        "purchase_order": purchase_order or f"1C-{number}",
        "project_code": None,
        "project_name": None,
        "supplier": supplier,
        "counterparty": supplier,
        "warehouse": warehouse,
        "invoice_date": invoice_date,
        "invoice_number": invoice_number,
        "storage_zone": str(doc.get("ЗонаХранения") or "Зона приёмки"),
        "presentation_place": str(doc.get("МестоПредъявления") or "Участок входного контроля"),
        "otk_incoming_warehouse": otk_wh,
        "executor_id": cycle_worker(index, workers),
        "due_at": due_at,
        "status": status,
        "lines": lines,
        # keep 1C provenance for debugging / re-seed (UI ignores unknown fields)
        "_1c": {
            "ref_key": ref,
            "number": number,
            "date": doc.get("Date"),
            "состояние": doc.get("Состояние"),
            "этап": doc.get("ЭтапДокумента"),
            "entity": ENTITY,
        },
    }


def load_workers(path: Path) -> list[dict[str, Any]]:
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            workers = data.get("workers")
            if isinstance(workers, list) and workers:
                return workers
        except (OSError, json.JSONDecodeError):
            pass
    return list(DEFAULT_WORKERS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30, help="Lookback window in days (default 30)")
    parser.add_argument("--top", type=int, default=100, help="Hard max documents (default 100)")
    parser.add_argument("--dry-run", action="store_true", help="Fetch/map but do not write JSON")
    parser.add_argument("--no-front", action="store_true", help="Do not mirror to frontend seed JSON")
    parser.add_argument(
        "--keep-mock",
        action="store_true",
        default=False,
        help="Opt-in: keep non-OData mock cards (id not starting with pres-1c-) and append OData; default is replace/drop mocks",
    )
    args = parser.parse_args(argv)

    if args.top > 100:
        print("Refusing --top > 100 (hard safety limit)", file=sys.stderr)
        return 2
    if args.days > 90:
        print("Refusing --days > 90 (hard safety limit)", file=sys.stderr)
        return 2

    env = load_env(ENV_PATH)
    base = (env.get("ODATA_BASE_URL") or env.get("ONEC_ODATA_URL") or "").rstrip("/")
    user = env.get("ODATA_USERNAME") or env.get("ONEC_ODATA_USER") or ""
    password = env.get("ODATA_PASSWORD") or env.get("ONEC_ODATA_PASSWORD") or ""
    if not base or not user or not password:
        print("Missing ODATA credentials in .env", file=sys.stderr)
        return 1

    print(f"BASE={base}")
    print(f"USER={user}")
    print(f"ENTITY={ENTITY}")
    print(f"limits: days={args.days} top={args.top}")

    client = ODataClient(base, user, password)
    docs, mode = fetch_documents(client, days=args.days, top=args.top)
    print(f"fetch_mode={mode}")
    print(f"documents_fetched={len(docs)}")

    workers = load_workers(BACK_OUT)
    presentations = [
        map_document(client, doc, index=i, workers=workers) for i, doc in enumerate(docs)
    ]

    if args.keep_mock and BACK_OUT.exists():
        existing = json.loads(BACK_OUT.read_text(encoding="utf-8"))
        old = [
            p
            for p in (existing.get("presentations") or [])
            if not str(p.get("id", "")).startswith("pres-1c-")
        ]
        presentations = old + presentations

    payload = {"workers": workers, "presentations": presentations}
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"

    samples = presentations[:3]
    for s in samples:
        meta = s.get("_1c") or {}
        print(
            f"sample: number={meta.get('number')} date={meta.get('date')} "
            f"invoice={s.get('invoice_number')} status={s.get('status')} "
            f"supplier={s.get('supplier')!r} lines={len(s.get('lines') or [])}"
        )

    if args.dry_run:
        print("dry-run: not writing files")
        return 0

    BACK_OUT.parent.mkdir(parents=True, exist_ok=True)
    BACK_OUT.write_text(text, encoding="utf-8")
    print(f"Wrote {BACK_OUT}")

    if not args.no_front:
        FRONT_OUT.parent.mkdir(parents=True, exist_ok=True)
        FRONT_OUT.write_text(text, encoding="utf-8")
        print(f"Wrote {FRONT_OUT}")

    statuses: dict[str, int] = {}
    for p in presentations:
        statuses[str(p["status"])] = statuses.get(str(p["status"]), 0) + 1
    print(f"statuses={statuses}")
    print("Hot-reload: otk_store reads JSON on each request — API restart not required.")
    print("UI: /agents/quality-engineer")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
