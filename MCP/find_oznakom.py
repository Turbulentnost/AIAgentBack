# -*- coding: utf-8 -*-
import json
import urllib.parse
import urllib.request
import base64
from datetime import datetime

BASE = "http://192.168.2.229:81/erp_pm/odata/standard.odata/"
AUTH = base64.b64encode(b"odata.user:npo852456").decode()
HEADERS = {
    "Authorization": f"Basic {AUTH}",
    "Accept": "application/json; charset=utf-8",
}

USER_KEY = "9f5cc704-002c-11f1-9792-6cb31113810c"
USER_NAME = "Уставицкий Андрей Алексеевич"
DOC_TYPE = "Document_ТД_СлужебнаяЗаписка"
USER_CACHE = {}


def fetch(url: str) -> dict:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def paginate(entity: str, odata_filter: str, select: str):
    skip = 0
    while True:
        params = urllib.parse.urlencode(
            {
                "$format": "json",
                "$filter": odata_filter,
                "$select": select,
                "$top": "500",
                "$skip": str(skip),
                "$orderby": "Date desc",
            },
            safe="$'(),",
        )
        data = fetch(BASE + entity + "?" + params)
        rows = data.get("value", [])
        yield from rows
        if len(rows) < 500:
            break
        skip += 500


def resolve_user(ref_key: str, user_type: str) -> str:
    if not ref_key or ref_key == "00000000-0000-0000-0000-000000000000":
        return ""
    cache_key = (ref_key, user_type)
    if cache_key in USER_CACHE:
        return USER_CACHE[cache_key]

    entity = None
    if "Catalog_Пользователи" in (user_type or ""):
        entity = "Catalog_Пользователи"
    elif "Catalog_ФизическиеЛица" in (user_type or ""):
        entity = "Catalog_ФизическиеЛица"
    elif "Catalog_Сотрудники" in (user_type or ""):
        entity = "Catalog_Сотрудники"

    name = ref_key
    if entity:
        try:
            url = (
                BASE
                + f"{entity}(guid'{ref_key}')"
                + "?"
                + urllib.parse.urlencode({"$format": "json", "$select": "Description"})
            )
            data = fetch(url)
            name = data.get("Description") or ref_key
        except Exception:
            name = ref_key

    USER_CACHE[cache_key] = name
    return name


def fmt_date(value: str) -> str:
    if not value or value.startswith("0001-01-01"):
        return ""
    try:
        return datetime.fromisoformat(value.replace("Z", "")).strftime("%d.%m.%Y")
    except Exception:
        return value[:10] if value else ""


def main():
    select = (
        "Ref_Key,Description,Number,Date,Executed,ДатаИсполнения,"
        "Предмет,Предмет_Type,Исполнитель,Исполнитель_Type,"
        "Автор,Автор_Type,RoutePoint,RoutePoint_Type,РезультатВыполнения,ПредметСтрокой"
    )
    filters = [
        "substringof('ознаком',Description) eq true and DeletionMark eq false",
        "substringof('Ознаком',Description) eq true and DeletionMark eq false",
    ]

    tasks = {}
    for odata_filter in filters:
        for row in paginate("Task_ЗадачаИсполнителя", odata_filter, select):
            if row.get("Исполнитель") != USER_KEY:
                continue
            if DOC_TYPE not in (row.get("Предмет_Type") or ""):
                continue
            tasks[row["Ref_Key"]] = row

    results = []
    for task in sorted(tasks.values(), key=lambda x: x.get("Date") or "", reverse=True):
        doc_key = task.get("Предмет")
        doc = {}
        if doc_key:
            try:
                url = (
                    BASE
                    + f"Document_ТД_СлужебнаяЗаписка(guid'{doc_key}')"
                    + "?"
                    + urllib.parse.urlencode(
                        {
                            "$format": "json",
                            "$select": "Ref_Key,Number,Date,ТемаСлужебнойЗаписки,ТемаСлужебнойЗаписки_Type,Ответственный_Key",
                        }
                    )
                )
                doc = fetch(url)
            except Exception as exc:
                doc = {"error": str(exc)}

        theme = doc.get("ТемаСлужебнойЗаписки") or task.get("ПредметСтрокой") or task.get("Description") or ""
        if isinstance(theme, dict):
            theme = str(theme)

        author = resolve_user(task.get("Автор"), task.get("Автор_Type"))
        addressee = resolve_user(task.get("Исполнитель"), task.get("Исполнитель_Type")) or USER_NAME
        executed = task.get("Executed")
        status = "Ознакомлен" if executed else "Не ознакомлен"

        results.append(
            {
                "date": fmt_date(doc.get("Date") or task.get("Date")),
                "number": doc.get("Number") or task.get("Number") or "",
                "theme": theme,
                "author": author,
                "addressee": addressee,
                "status": status,
                "oznakom_date": fmt_date(task.get("ДатаИсполнения")) if executed else "",
                "ref_key": doc.get("Ref_Key") or doc_key,
                "task_ref": task.get("Ref_Key"),
                "task_description": task.get("Description") or "",
            }
        )

    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
