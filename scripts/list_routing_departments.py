"""Вывод всех отделов, участвующих в поиске/маршрутизации."""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402
from agent_pochta.services.routing_departments import (  # noqa: E402
    build_departments_from_rules,
    build_departments_from_structure,
    load_routing_rules,
    resolve_enterprise_positions_path,
)


def main() -> None:
    rules = load_routing_rules()
    spam = str(rules.get("spam_code", "00-999999"))
    reserve = str(rules.get("reserve_code", ""))

    sources: dict[str, dict[str, list[str]]] = defaultdict(
        lambda: {"email_keyword": [], "exact_email": [], "content": [], "sales": []}
    )

    for rule in rules.get("email_keyword_rules", []):
        code = str(rule["code"])
        if code != spam:
            sources[code]["email_keyword"].append(str(rule.get("keyword", "")))

    for rule in rules.get("exact_email_rules", []):
        code = str(rule["code"])
        if code != spam:
            sources[code]["exact_email"].append(str(rule.get("email", "")))

    for rule in rules.get("content_rules", []):
        code = str(rule["code"])
        if code != spam:
            sources[code]["content"].extend(str(k) for k in rule.get("keywords", []))

    sources["00-000076"]["sales"].extend(["orkk_holdings"] * len(rules.get("sales_orkk_holdings", [])))
    sources["00-000076"]["sales"].extend(["gazprom"] * len(rules.get("sales_gazprom", [])))
    sources["00-000075"]["sales"].extend(["odp"] * len(rules.get("sales_odp", [])))

    try:
        departments = build_departments_from_structure(rules)
        dept_source = "1C structure + routing_rules + TZ"
    except FileNotFoundError:
        departments = build_departments_from_rules(rules)
        dept_source = "routing_rules only"

    qdrant_ids: set[str] = set()
    try:
        from qdrant_client import QdrantClient

        client = QdrantClient(url=get_settings().qdrant_url, prefer_grpc=False)
        points, _ = client.scroll("departments", limit=100, with_payload=True, with_vectors=False)
        qdrant_ids = {str(p.payload.get("department_id")) for p in points}
        client.close()
        qdrant_status = f"OK ({len(qdrant_ids)} точек)"
    except Exception as exc:
        qdrant_status = f"недоступен: {exc}"

    lines: list[str] = []
    lines.append("=" * 80)
    lines.append("ОТДЕЛЫ, УЧАСТВУЮЩИЕ В ПОИСКЕ / МАРШРУТИЗАЦИИ (RAG + RuleRouter)")
    lines.append("=" * 80)
    lines.append("Источник правил: data/routing_rules.json")
    try:
        lines.append(f"Источник отделов: {dept_source} ({resolve_enterprise_positions_path()})")
    except FileNotFoundError:
        lines.append(f"Источник отделов: {dept_source}")
    lines.append(f"Qdrant departments: {qdrant_status}")
    lines.append(f"Исключён spam-код: {spam}")
    lines.append(f"Резервный отдел (fallback): {reserve} — {rules.get('reserve_name', '')}")
    lines.append(f"Всего отделов в поиске: {len(departments)}")
    lines.append("")

    for index, dept in enumerate(departments, 1):
        code = dept.department_id
        src_parts = [dept_source]
        if code in rules.get("department_names", {}):
            src_parts.append("routing_rules")
        if code in qdrant_ids:
            src_parts.append("Qdrant")
        src = " + ".join(dict.fromkeys(src_parts))

        rule_info = sources.get(code, {})
        rule_types: list[str] = []
        if rule_info.get("email_keyword"):
            rule_types.append(f"email_keyword ({len(rule_info['email_keyword'])})")
        if rule_info.get("exact_email"):
            rule_types.append(f"exact_email ({len(rule_info['exact_email'])})")
        if rule_info.get("content"):
            rule_types.append(f"content ({len(rule_info['content'])})")
        if rule_info.get("sales"):
            rule_types.append(f"sales ({len(rule_info['sales'])})")
        if code == reserve:
            rule_types.append("reserve (fallback)")
        if not rule_types:
            rule_types.append("только department_names")

        emails = rule_info.get("exact_email", [])
        if not emails:
            emails = sorted({kw for kw in dept.keywords if "@" in kw})
        email_sample = ", ".join(emails[:4])
        if len(emails) > 4:
            email_sample += f" (+{len(emails) - 4})"

        kw_sample = ", ".join(dept.keywords[:8])
        if len(dept.keywords) > 8:
            kw_sample += f" ... (+{len(dept.keywords) - 8})"

        lines.append(f"{index:2}. {code} | {dept.department_name}")
        lines.append(f"    Источник: {src}")
        lines.append(f"    Типы правил: {'; '.join(rule_types)}")
        lines.append(f"    Keywords: {len(dept.keywords)} — {kw_sample}")
        if emails:
            lines.append(f"    Email: {email_sample}")
        if dept.responsibility:
            lines.append(f"    Ответственность: {dept.responsibility[:120]}")
        lines.append("")

    lines.append("-" * 80)
    lines.append("СВОДНАЯ ТАБЛИЦА")
    lines.append("-" * 80)
    lines.append(
        f"{'№':>3} | {'Код':<12} | {'Название':<35} | {'Источник':<20} | {'Kw':>3} | Email (пример)"
    )
    lines.append("-" * 80)
    for index, dept in enumerate(departments, 1):
        code = dept.department_id
        src = "both" if code in qdrant_ids else "routing_rules"
        emails = sources.get(code, {}).get("exact_email", [])
        em = emails[0] if emails else "—"
        name = dept.department_name[:35]
        lines.append(
            f"{index:3} | {code:<12} | {name:<35} | {src:<20} | {len(dept.keywords):3} | {em}"
        )

    out = ROOT / "data" / "routing_departments_list.txt"
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"\nСохранено: {out}")


if __name__ == "__main__":
    main()
