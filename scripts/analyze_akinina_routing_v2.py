"""Акинина routing analysis v2: themes + payer direction + soft agent match."""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import quote

import httpx
from sqlalchemy import create_engine, text

ROOT = Path("/app") if Path("/app/src/agent_pochta").exists() else Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402

USER_KEY = "7a3fa603-0899-11f0-9637-6cb31113810e"
USER_NAME = "Акинина Татьяна Владимировна"
DOC = "Document_ТД_ВходящаяКорреспонденция"
OUT = ROOT / "data" / "temp" / "akinina_routing_analysis.json"
TOP_N = 100

PAYER_TO_DIRECTION = {
    "ТурбулентностьДОНПроизводство1": "ПР",
    "ТурбулентностьДОНСС": "СС",
    "ТурбулентностьДОНКС": "КС",
    "ТурбулентностьДОНМС": "МС",
    "ТурбулентностьДОНРУ": "РУ",
    "АЛМАЗ": "АЛ",
    "Метрогазсервис": "МГ",
    "АмурскаяЛегенда": "АМ",
    "БМИ": "БМ",
}

# Heuristic theme → department (expert patterns from Акинина themes + agent empirics).
THEME_RULES: list[tuple[str, str, str]] = [
    # АЛМАЗ / бытовые счётчики / ОТП
    (r"дубликат|паспорт\s*(счет|счёт|газ)|копия\s*паспорт|восстановить\s*паспорт|документы\s*на\s*(газ|счет|счёт)", "00-000099", "ОТП / паспорта ГРАНД"),
    (r"батаре|индикац|табло\s*не|пропала\s*индикац|не\s*достоверн|некорректн\w*\s*данн|выход\s*из\s*строя|села\s*батаре", "00-000099", "ОТП / неисправности бытовых"),
    (r"купить\s*(газов\w*\s*)?счет|счётчик\s*гранд|счетчик\s*гранд|\bгранд\b", "00-000155", "Дилерские продажи ГРАНД"),
    (r"замен\w*\s*пуг|пуг\b", "00-000099", "ОТП / замена ПУГ"),
    # Метрология
    (r"поверк|метролог|калибров|свидетельств.*поверк|протокол.*поверк", "00-000025", "Метрология / поверка"),
    # Бухгалтерия
    (r"акт\s*сверк|сверк\w*\s*расчет|взаиморасчет|об\s*оплат|задолж|погашен\w*\s*задолж", "00-000002", "Бухгалтерия"),
    (r"упд|сч[её]т[- ]?фактур|закрывающ", "00-000002", "Бухгалтерия"),
    # Юристы
    (r"претензи|иск\b|арбитраж|судебн|кассац|апелляц\w*\s*определен|определение\b|суд\b", "00-000044", "Юридический отдел"),
    # Сервис / качество НПО
    (r"неисправн|ремонт|гарантий|сервисн|несоответств|недокомплект|акт\s*несоответ|акт\s*вк|нарушен", "00-000163", "Сервис / тех. директор"),
    (r"поставк\w*\s*зип|\bзип\b", "00-000163", "Сервис / ЗИП"),
    # Коммерция / ТКП / запросы цен (часто ключевые клиенты, не только КД)
    (r"\bткп\b|коммерческ\w*\s*предложен|\bкп\b|выслать\s*кп|запрос\s*цен|прайс|цен[уа]\s*и\s*налич|наличи[ея].*срок|подобрать\s*(два\s*)?(электромагнит|оборудован|расходомер)|запрос\s*прибора", "00-000042", "Ключевые клиенты / ТКП"),
    (r"рассмотреть\s*возможность\s*поставки|возможность\s*поставки|о\s*поставке|поставка\s*аналог|аналог\w*|ультразвуков\w*\s*расходомер|заявка\s*на\s*проработку|заявка\s*сму", "00-000042", "Ключевые клиенты / поставка"),
    # Тендеры / конкурсы
    (r"тендер|аукцион|закупк|этп|44-фз|223-фз|опрос\s*рынка|проведен\w*\s*конкурс|положен\w*\s*о\s*проведении\s*конкурс", "00-000054", "Тендеры"),
    # Маркетинг
    (r"выставк|форум|приглашен|конференц|экспо|семинар|100\s*лучших", "00-000013", "Развитие / маркетинг"),
    # Кадры / соц.
    (r"вакан|резюме|кадр|трудоустр|отпуск|арендн\w*\s*жиль", "00-000063", "Кадры"),
    # Договоры — чаще юристы или КД; оставляем юристам по слову договор alone only if short theme
    (r"^договор$|договор\s|соглашен", "00-000044", "Договоры/юристы"),
    # АСУ
    (r"настройк|программн|\bасу\b|помощь\s*с\s*настрой", "00-000119", "АСУ / ПО"),
    # Мин/админ → ОД (info_strict ministry)
    (r"министерств|администраци|для\s*ознакомлен|о\s*направлении\s*информац|наличия\s*на\s*предприятии\s*дефицит", "00-000152", "Операционный директор"),
    # Логистика / ЭПД
    (r"перевозочн|электронн\w*\s*перевоз|логистик|доставк", "00-000076", "Логистика/Газпром поток"),
    # Опытная эксплуатация → часто продажи/ключевые
    (r"опытн\w*\s*эксплуатац", "00-000042", "Ключевые клиенты / ОПЭ"),
]


def load_names() -> tuple[dict[str, str], dict[str, str]]:
    names: dict[str, str] = {}
    # Prefer functional names (topics / routing) over person names in UI allowlist.
    tz = json.loads((ROOT / "data" / "tz_department_topics.json").read_text(encoding="utf-8"))
    for code, meta in tz.items():
        if isinstance(meta, dict):
            if meta.get("topics"):
                names[code] = meta["topics"][0]
            elif meta.get("names"):
                names[code] = meta["names"][0]
    rules = json.loads((ROOT / "data" / "routing_rules.json").read_text(encoding="utf-8"))
    for rule in rules.get("exact_email_rules", []) + rules.get("content_rules", []):
        if rule.get("code") and rule.get("name"):
            names.setdefault(rule["code"], rule["name"])
    ui = json.loads((ROOT / "data" / "ui_department_allowlist.json").read_text(encoding="utf-8"))
    for it in ui.get("departments", []):
        names.setdefault(it["code"], it["name"])
    # Hard overrides for clarity in the report
    names["00-000099"] = "Отдел технической поддержки (ОТП)"
    names["00-000042"] = "Отдел по работе с ключевыми клиентами"
    names["00-000155"] = "Отдел дилерских продаж"
    names["00-000025"] = "Отдел метрологии и сертификации"
    names["00-000152"] = "ОПЕРАЦИОННЫЙ ДИРЕКТОР"
    payer = json.loads((ROOT / "data" / "odata_payer_direction_display.json").read_text(encoding="utf-8"))
    return names, payer


def infer_dept(theme: str, payer: str = "") -> tuple[str, str, str]:
    t = (theme or "").lower()
    for pat, code, label in THEME_RULES:
        if re.search(pat, t, re.I):
            return code, label, pat
    # Payer fallback: АЛМАЗ without clear theme → ОТП (бытовой контур)
    if payer == "АЛМАЗ":
        return "00-000099", "ОТП (fallback АЛМАЗ)", "payer:АЛМАЗ"
    if payer == "БМИ" and t:
        return "00-000128", "Продажи БМИ (fallback)", "payer:БМИ"
    return "", "не классифицировано", ""


def subject_bucket(theme: str, payer: str = "") -> str:
    code, label, _ = infer_dept(theme, payer)
    if code:
        return label
    t = re.sub(r"\d+", "#", (theme or "").lower())
    t = re.sub(r"\s+", " ", t).strip()
    return t[:60] or "(пусто)"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    settings = get_settings()
    base = settings.odata_base_url.rstrip("/") + "/"
    auth = (settings.odata_username, settings.odata_password)
    names, payer_labels = load_names()

    with httpx.Client(auth=auth, timeout=180) as client:
        flt = f"Ответственный_Key eq guid'{USER_KEY}'"
        docs_raw = client.get(
            f"{base}{quote(DOC)}?$format=json&$filter={quote(flt)}&$orderby=Date desc&$top={TOP_N}"
        ).raise_for_status().json()["value"]

        # attachments for first 20
        attach_map: dict[str, list[str]] = {}
        ent = "Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы"
        for d in docs_raw[:20]:
            ref = d["Ref_Key"]
            try:
                af = f"ВладелецФайла_Key eq guid'{ref}'"
                items = client.get(
                    f"{base}{quote(ent)}?$format=json&$filter={quote(af)}&$top=20"
                ).raise_for_status().json().get("value", [])
                attach_map[d["Number"]] = [
                    it.get("Description") or it.get("FileName") or ""
                    for it in items
                    if (it.get("Description") or it.get("FileName"))
                    and (it.get("Description") or "") != d["Number"]
                ]
            except Exception:
                attach_map[d["Number"]] = []

    docs = []
    for d in docs_raw:
        theme = d.get("ТемаСлужебнойЗаписки") or ""
        payer = d.get("ПлательщикНаправление") or ""
        code, label, pat = infer_dept(theme, payer)
        docs.append(
            {
                "Number": d.get("Number"),
                "Date": d.get("Date"),
                "Ref_Key": d.get("Ref_Key"),
                "Статус": d.get("Статус"),
                "ИсточникПоступления": d.get("ИсточникПоступления"),
                "Партнер": d.get("Партнер"),
                "ТемаСлужебнойЗаписки": theme,
                "ПлательщикНаправление": payer,
                "ПлательщикНаправление_label": payer_labels.get(payer, payer),
                "direction_code": PAYER_TO_DIRECTION.get(payer, ""),
                "Организация_Key": d.get("Организация_Key"),
                "НомерИсходящий": d.get("НомерИсходящий"),
                "Кому": d.get("Кому") or "",
                "ПодразделениеИсполнитель_Key": d.get("ПодразделениеИсполнитель_Key"),
                "Направление": d.get("Направление") or "",
                "inferred_department_code": code,
                "inferred_department_name": names.get(code, label) if code else label,
                "infer_pattern": pat,
                "odata_attachments": attach_map.get(d.get("Number") or "", []),
            }
        )

    # Soft match agent DB by partner token / theme keywords
    engine = create_engine(settings.database_url)
    soft_matches = []
    with engine.connect() as conn:
        for d in docs:
            partner = (d.get("Партнер") or "").strip()
            theme = (d.get("ТемаСлужебнойЗаписки") or "").strip()
            # extract meaningful token from partner
            partner_token = ""
            m = re.search(r"[«\"]([^»\"]{4,})[»\"]", partner)
            if m:
                partner_token = m.group(1)
            else:
                parts = re.findall(r"[A-Za-zА-Яа-я]{5,}", partner)
                partner_token = parts[0] if parts else ""
            theme_token = ""
            for kw in ("поверк", "ткп", "сверк", "претенз", "ремонт", "выставк", "форум", "тендер", "гарант", "настройк"):
                if kw in theme.lower():
                    theme_token = kw
                    break

            row = None
            if partner_token and len(partner_token) >= 4:
                row = conn.execute(
                    text(
                        """
                        SELECT erp_document_number, department_id, department_name,
                               sender_email, subject, summary_ru, mailbox, dept_confidence
                        FROM email_messages
                        WHERE erp_document_number IS NOT NULL
                          AND (
                            subject ILIKE :p OR summary_ru ILIKE :p OR sender_email ILIKE :p
                            OR subject ILIKE :p2 OR summary_ru ILIKE :p2
                          )
                        ORDER BY received_at DESC
                        LIMIT 1
                        """
                    ),
                    {"p": f"%{partner_token[:40]}%", "p2": f"%{theme_token}%" if theme_token else f"%{partner_token[:40]}%"},
                ).mappings().first()
            if row is None and theme_token:
                row = conn.execute(
                    text(
                        """
                        SELECT erp_document_number, department_id, department_name,
                               sender_email, subject, summary_ru, mailbox, dept_confidence
                        FROM email_messages
                        WHERE erp_document_number IS NOT NULL
                          AND (subject ILIKE :t OR summary_ru ILIKE :t)
                        ORDER BY received_at DESC
                        LIMIT 1
                        """
                    ),
                    {"t": f"%{theme_token}%"},
                ).mappings().first()
            if row:
                soft_matches.append(
                    {
                        "akinina_number": d["Number"],
                        "akinina_theme": theme[:100],
                        "akinina_inferred": d["inferred_department_code"],
                        "agent_erp": row["erp_document_number"],
                        "agent_department_id": row["department_id"],
                        "agent_department_name": row["department_name"],
                        "agent_subject": (row["subject"] or "")[:100],
                        "agent_from": row["sender_email"],
                        "agent_mailbox": row["mailbox"],
                        "agree": bool(
                            d["inferred_department_code"]
                            and row["department_id"]
                            and d["inferred_department_code"] == row["department_id"]
                        ),
                    }
                )
                d["agent_soft"] = dict(row)
            else:
                d["agent_soft"] = None

        # also: exact join still
        nums = [d["Number"] for d in docs]
        exact = conn.execute(
            text(
                "SELECT erp_document_number, department_id, department_name, sender_email, subject, mailbox "
                "FROM email_messages WHERE erp_document_number = ANY(:n)"
            ),
            {"n": nums},
        ).mappings().all()
        exact_map = {r["erp_document_number"]: dict(r) for r in exact}

    for d in docs:
        d["agent_exact"] = exact_map.get(d["Number"])

    # Aggregates
    dept_counts = Counter(
        d["inferred_department_code"] or "UNCLASSIFIED" for d in docs
    )
    payer_counts = Counter(d["ПлательщикНаправление"] or "(пусто)" for d in docs)
    direction_counts = Counter(d["direction_code"] or "(пусто)" for d in docs)
    source_counts = Counter(d["ИсточникПоступления"] or "(пусто)" for d in docs)
    status_counts = Counter(d["Статус"] or "(пусто)" for d in docs)
    theme_buckets = Counter(
        subject_bucket(d["ТемаСлужебнойЗаписки"], d["ПлательщикНаправление"]) for d in docs
    )

    # Partner domain-ish from partner string if email-like
    partner_orgs = Counter()
    for d in docs:
        p = d.get("Партнер") or ""
        if "@" in p:
            # email in partner field
            m = re.search(r"[\w.+-]+@([\w.-]+)", p)
            if m:
                partner_orgs[m.group(1).lower()] += 1
        else:
            # normalize org short
            m = re.search(r"[«\"]([^»\"]{3,60})[»\"]", p)
            key = m.group(1).strip() if m else re.sub(r"\s+", " ", p)[:50]
            if key:
                partner_orgs[key] += 1

    # Cross: theme dept vs payer direction
    cross = defaultdict(Counter)
    for d in docs:
        cross[d["inferred_department_code"] or "UNCLASSIFIED"][d["direction_code"] or "?"] += 1

    # Proposals
    proposals = []
    # content rules from classified themes with enough mass
    for code, n in dept_counts.most_common():
        if code == "UNCLASSIFIED" or n < 3:
            continue
        patterns = [d["infer_pattern"] for d in docs if d["inferred_department_code"] == code and d["infer_pattern"]]
        pat_counts = Counter(patterns)
        top_pat = pat_counts.most_common(1)[0][0] if pat_counts else ""
        examples = [d["ТемаСлужебнойЗаписки"][:80] for d in docs if d["inferred_department_code"] == code][:5]
        proposals.append(
            {
                "priority": 1 if n >= 8 else 2,
                "type": "content_rules",
                "suggested_code": code,
                "suggested_name": names.get(code, code),
                "evidence_count": n,
                "pattern_hint": top_pat,
                "example_themes": examples,
                "rationale": (
                    f"У Акининой {n}/{len(docs)} писем с темами под «{names.get(code, code)}». "
                    f"Добавить/усилить content_rules (и RAG keywords) по шаблону /{top_pat}/."
                ),
            }
        )

    # direction consistency notes
    for payer, n in payer_counts.most_common():
        if n < 5:
            continue
        direction = PAYER_TO_DIRECTION.get(payer, "")
        # dominant inferred dept under this payer
        sub = Counter(
            d["inferred_department_code"] or "UNCLASSIFIED"
            for d in docs
            if d["ПлательщикНаправление"] == payer
        )
        top_dept, top_n = sub.most_common(1)[0]
        proposals.append(
            {
                "priority": 2,
                "type": "direction_default_hint",
                "payer_direction": payer,
                "xml_direction": direction,
                "evidence_count": n,
                "dominant_inferred_dept": top_dept,
                "dominant_share": round(top_n / n, 2),
                "rationale": (
                    f"ПлательщикНаправление={payer} ({n} док.): чаще всего темы → {top_dept} "
                    f"({top_n}/{n}). При маршрутизации агента для направления {direction or '?'} "
                    f"учитывать этот prior."
                ),
            }
        )

    # Gazprom / official request → often metrology or commercial
    gazprom_n = sum(1 for d in docs if "газпром" in (d.get("Партнер") or "").lower())
    if gazprom_n:
        g_depts = Counter(
            d["inferred_department_code"] or "UNCLASSIFIED"
            for d in docs
            if "газпром" in (d.get("Партнер") or "").lower()
        )
        proposals.append(
            {
                "priority": 1,
                "type": "info_strict_or_content",
                "partner_pattern": "газпром",
                "evidence_count": gazprom_n,
                "dept_breakdown": dict(g_depts),
                "rationale": (
                    f"Партнёры «Газпром*» у Акининой: {gazprom_n} док., разбивка по темам {dict(g_depts)}. "
                    "Не слать всё в 00-000001 (info_strict Ilchenko) — смотреть тему: поверка→00-000025, ТКП→коммерция."
                ),
            }
        )

    # АЛМАЗ / ОТП — главный объём ручной работы Акининой
    almaz_n = sum(1 for d in docs if d["ПлательщикНаправление"] == "АЛМАЗ")
    otp_n = dept_counts.get("00-000099", 0)
    if almaz_n or otp_n:
        proposals.insert(
            0,
            {
                "priority": 1,
                "type": "content_rules",
                "suggested_code": "00-000099",
                "suggested_name": names.get("00-000099"),
                "evidence_count": max(almaz_n, otp_n),
                "patterns_to_add": [
                    "дубликат",
                    "предоставить дубликат",
                    "восстановить паспорт",
                    "копия паспорта",
                    "паспорт счетчика",
                    "паспорт счётчика",
                    "села батарея",
                    "пропала индикация",
                    "на табло не отображается",
                    "недостоверные показания",
                    "некорректные данные",
                ],
                "organization": "АЛ",
                "rationale": (
                    f"Контур АЛМАЗ: {almaz_n} док. Акининой; темы паспортов/дубликатов/батарей → ОТП 00-000099 (~{otp_n}). "
                    "Расширить content_rules ОТП короткими формулировками без обязательного слова «гранд» "
                    "(сейчас правило слишком узкое: «дубликат паспорта счетчика газа гранд»)."
                ),
            },
        )

    # ТКП → 00-000042 not commercial director alone
    tkp_n = dept_counts.get("00-000042", 0)
    if tkp_n:
        proposals.append(
            {
                "priority": 1,
                "type": "content_rules",
                "suggested_code": "00-000042",
                "evidence_count": tkp_n,
                "patterns_to_add": [
                    "ткп",
                    "выслать кп",
                    "рассмотреть возможность поставки",
                    "цена и наличие",
                    "подобрать оборудование",
                    "заявка на проработку",
                    "прайс-лист",
                ],
                "rationale": (
                    f"Запросы ТКП/поставки у Акининой (~{tkp_n}) ближе к 00-000042 (ключевые клиенты), "
                    "а не к УД/ОД. Не путать с info_strict Газпром→председатель."
                ),
            }
        )

    # Soft mismatch proposals
    disagree = [m for m in soft_matches if m["akinina_inferred"] and m["agent_department_id"] and not m["agree"]]
    if disagree:
        pairs = Counter((m["agent_department_id"], m["akinina_inferred"]) for m in disagree)
        for (agent_d, ak_d), n in pairs.most_common(8):
            proposals.append(
                {
                    "priority": 2 if n >= 2 else 3,
                    "type": "correction_pattern_or_confidence_gate",
                    "agent_department_id": agent_d,
                    "akinina_inferred_department": ak_d,
                    "mismatch_count": n,
                    "samples": [m for m in disagree if m["agent_department_id"] == agent_d and m["akinina_inferred"] == ak_d][:3],
                    "rationale": f"Мягкое сопоставление: агент→{agent_d}, темы Акининой→{ak_d} ({n}).",
                }
            )

    # Data model note
    proposals.append(
        {
            "priority": 1,
            "type": "integration_gap",
            "rationale": (
                "В документах Акининой через OData НЕ заполнены Кому / ПодразделениеИсполнитель_Key / "
                "Направление / Email* / Содержание. Маршрут для человека выражается темой + "
                "ПлательщикНаправление + Партнёр. Для обучения агента: (1) парсить тему как intent, "
                "(2) не ждать department_id из её карточек 1С, (3) либо доработать выгрузку поля Кому "
                "в толстом клиенте, либо логировать её выбор иначе."
            ),
        }
    )

    # Unclassified themes → need HITL / UD
    unc = [d for d in docs if not d["inferred_department_code"]]
    if unc:
        proposals.append(
            {
                "priority": 2,
                "type": "confidence_gate",
                "unclassified_count": len(unc),
                "example_themes": [d["ТемаСлужебнойЗаписки"][:100] for d in unc[:10]],
                "rationale": (
                    f"{len(unc)} тем Акининой не попали в эвристики — для похожих писем агенту "
                    "держать низкий confidence / info_strict unclear → 00-000066, а не угадывать."
                ),
            }
        )

    proposals.sort(key=lambda p: (p.get("priority", 9), -int(p.get("evidence_count") or p.get("mismatch_count") or 0)))

    # Examples 12-15 diverse
    examples = []
    used = set()
    # one per top dept
    for code, _n in dept_counts.most_common(10):
        for d in docs:
            if (d["inferred_department_code"] or "UNCLASSIFIED") == code and d["Number"] not in used:
                examples.append(_ex(d, names))
                used.add(d["Number"])
                break
    for d in docs:
        if len(examples) >= 15:
            break
        if d["Number"] not in used:
            examples.append(_ex(d, names))
            used.add(d["Number"])

    dept_stats = []
    for code, n in dept_counts.most_common():
        dept_stats.append(
            {
                "department_code": code,
                "department_name": names.get(code, "не классифицировано по теме" if code == "UNCLASSIFIED" else code),
                "count": n,
                "note": "inferred_from_theme" if code != "UNCLASSIFIED" else "theme_unmatched",
            }
        )

    report = {
        "meta": {
            "user_name": USER_NAME,
            "user_ref_key": USER_KEY,
            "employee_catalog_ref": "5c9b35fd-086f-11f0-9637-6cb31113810e",
            "identification_field": "Ответственный_Key",
            "identification_notes": (
                "Акинина Татьяна Владимировна найдена в Catalog_Пользователи "
                f"(Ref_Key={USER_KEY}). На Document_ТД_ВходящаяКорреспонденция единственное "
                "стабильное пользовательское поле — Ответственный_Key. "
                "Автор_Key / Зарегистрировал_Key / Изменил_Key в OData отсутствуют; "
                "строковые Автор/Подразделение через $select не найдены. "
                "ВАЖНО: у её документов Кому и ПодразделениеИсполнитель_Key пусты (0/100) — "
                "целевой отдел в OData напрямую не хранится. Анализ отделов = эвристика по "
                "ТемаСлужебнойЗаписки + ПлательщикНаправление. "
                "Точного join с email_messages по erp_document_number нет (её номера не создавались агентом)."
            ),
            "sample_size": len(docs),
            "date_from": docs[-1]["Date"] if docs else None,
            "date_to": docs[0]["Date"] if docs else None,
            "agent_exact_joinable": len(exact_map),
            "agent_soft_matches": len(soft_matches),
            "agent_soft_agreements": sum(1 for m in soft_matches if m.get("agree")),
            "destination_reality": {
                "Кому_filled": 0,
                "ПодразделениеИсполнитель_filled": 0,
                "Направление_filled": 0,
                "ПлательщикНаправление_filled": sum(1 for d in docs if d["ПлательщикНаправление"]),
                "Тема_filled": sum(1 for d in docs if d["ТемаСлужебнойЗаписки"]),
                "Партнер_filled": sum(1 for d in docs if d["Партнер"]),
            },
        },
        "department_stats_inferred_from_theme": dept_stats,
        "payer_direction_stats": [
            {
                "payer": p,
                "label": payer_labels.get(p, p),
                "direction_code": PAYER_TO_DIRECTION.get(p, ""),
                "count": n,
            }
            for p, n in payer_counts.most_common()
        ],
        "xml_direction_stats": [
            {"direction": d, "count": n} for d, n in direction_counts.most_common()
        ],
        "source_stats": dict(source_counts),
        "status_stats": dict(status_counts),
        "theme_bucket_stats": [
            {"bucket": b, "count": n} for b, n in theme_buckets.most_common(30)
        ],
        "top_partners": [
            {"partner_or_domain": p, "count": n} for p, n in partner_orgs.most_common(25)
        ],
        "dept_x_direction": {k: dict(v) for k, v in cross.items()},
        "examples": examples,
        "soft_matches_vs_agent": soft_matches[:40],
        "improvement_proposals": proposals[:20],
        "documents": [
            {
                "Number": d["Number"],
                "Date": d["Date"],
                "theme": d["ТемаСлужебнойЗаписки"][:160],
                "partner": d["Партнер"],
                "source": d["ИсточникПоступления"],
                "status": d["Статус"],
                "payer": d["ПлательщикНаправление"],
                "direction_code": d["direction_code"],
                "inferred_department_code": d["inferred_department_code"],
                "inferred_department_name": d["inferred_department_name"],
                "attachments": d["odata_attachments"],
                "agent_exact": d.get("agent_exact"),
                "agent_soft_department_id": (d.get("agent_soft") or {}).get("department_id")
                if d.get("agent_soft")
                else None,
            }
            for d in docs
        ],
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "written": str(OUT),
                "sample_size": len(docs),
                "date_from": report["meta"]["date_from"],
                "date_to": report["meta"]["date_to"],
                "dept_stats": dept_stats,
                "payer_stats": report["payer_direction_stats"],
                "exact_join": len(exact_map),
                "soft_matches": len(soft_matches),
                "examples": examples,
                "proposals": [
                    {"priority": p.get("priority"), "type": p.get("type"), "rationale": p.get("rationale")[:180]}
                    for p in proposals[:12]
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _ex(d: dict, names: dict[str, str]) -> dict:
    soft = d.get("agent_soft") or {}
    return {
        "Number": d["Number"],
        "Date": (d["Date"] or "")[:10],
        "subject_short": (d["ТемаСлужебнойЗаписки"] or "")[:100],
        "partner": (d["Партнер"] or "")[:80],
        "source": d["ИсточникПоступления"],
        "payer_direction": d["ПлательщикНаправление"],
        "akinina_inferred_dept": d["inferred_department_code"] or "UNCLASSIFIED",
        "akinina_inferred_name": d["inferred_department_name"],
        "agent_soft_dept": soft.get("department_id"),
        "agent_soft_name": soft.get("department_name"),
        "attachments": d.get("odata_attachments") or [],
    }


if __name__ == "__main__":
    main()
