"""Build expanded GOST guidance from PDF text extracts (line-range based)."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data" / "temp" / "gost_extract"
OUT = Path(r"c:\Users\mdj\Desktop\рабочее\agent_nd\агенты\eskd-agent\model\gost_guidance_expanded.py")

# (file, start_line_contains, end_line_contains) — 1-based line search
RANGES: dict[str, list[tuple[str, str, str]]] = {
    "2.104": [
        ("2.104.txt", "5  Правила выполнения и заполнения основной надписи", "Приложение  А"),
        ("2.104.txt", "Т а б л и ц а 1", "Сведения об учете КД"),
    ],
    "2.201": [("2.201.txt", "5  Правила обозначения изделий", "6.2  Обозначение групповых")],
    "2.109": [
        ("2.109.txt", "5  Основные требования к чертежам", "8  Требования к монтажным"),
        ("2.109.txt", "7.3  Номера позиций", "7.4  Отдельные случаи"),
    ],
    "2.503": [("2.503.txt", "5  Внесение изменений", "5.12  В таблице изменений")],
    "2.316": [("2.316.txt", "5  Правила выполнения надписей", "Приложение")],
    "2.308": [("2.308.txt", "4  Основные положения", "Т а б л и ц а 1")],
    "2.105": [
        ("2.109.txt", "7  Требования к сборочным чертежам", "8  Требования к монтажным"),
        ("2.102.txt", "спецификац", "Ведомость"),
    ],
    "2.301": [
        ("2.104.txt", "6  Масштаб", "7 \nЛист"),
    ],
}

PREFACE: dict[str, str] = {
    "2.104": (
        "ГОСТ Р 2.104-2023 — основная надпись (штамп). Проверяй ТОЛЬКО штамп/реквизиты.\n"
        "Ключевое: графы 1–2 (●), 6 масштаб на чертежах, 7–8 лист/листов, 9 org, "
        "10–13 подписи (Разраб.+Н.контр. обяз.), 14–18 изменения.\n\n"
    ),
    "2.201": "ГОСТ Р 2.201-2023 — обозначения. Уникальность, структура, исполнения .01/.02.\n\n",
    "2.105": "ГОСТ 2.105 / 2.102 — спецификация и BOM (PDF 2.105 отсутствует в базе).\n\n",
    "2.109": "ГОСТ Р 2.109-2023 — виды, позиции, сборочные чертежи.\n\n",
    "2.503": "ГОСТ Р 2.503-2023 — изменения, литера, ИИ.\n\n",
    "2.316": "ГОСТ Р 2.316-2023 — ТТ и надписи.\n\n",
    "2.308": "ГОСТ Р 2.308-2023 — размеры и допуски.\n\n",
    "2.301": "ГОСТ 2.301/2.302 — формат и масштаб (PDF 2.301 отсутствует).\n\n",
}


def extract_range(path: Path, start: str, end: str, *, max_chars: int = 3500) -> str:
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    start_i = end_i = None
    for i, line in enumerate(lines):
        if start_i is None and start in line:
            start_i = i
        elif start_i is not None and end in line and i > start_i + 3:
            end_i = i
            break
    if start_i is None:
        return ""
    chunk = "\n".join(lines[start_i : end_i or start_i + 120])
    chunk = re.sub(r"Страница \d+\s*", "", chunk)
    chunk = re.sub(r"ИС «Техэксперт:.*?Интранет\s*", "", chunk)
    chunk = re.sub(r"ГОСТ Р 2\.\d{3}-\d{4}.*", "", chunk)
    chunk = re.sub(r"\n{3,}", "\n\n", chunk)
    if len(chunk) > max_chars:
        chunk = chunk[: max_chars - 3].rsplit("\n", 1)[0] + "..."
    return chunk.strip()


def build(key: str) -> str:
    parts = [PREFACE[key]]
    for fname, start, end in RANGES.get(key, []):
        body = extract_range(EXTRACT / fname, start, end)
        if body:
            parts.append(body)
            parts.append("")
    return "\n".join(parts).strip()


def main() -> None:
    keys = ["2.104", "2.201", "2.105", "2.109", "2.503", "2.316", "2.308", "2.301"]
    out = ['"""Expanded GOST guidance from База ГОСТов (PDF extracts)."""\n\n', "GOST_GUIDANCE_EXPANDED: dict[str, str] = {\n"]
    for key in keys:
        g = build(key)
        if len(g) > 5500:
            g = g[:5497].rsplit("\n", 1)[0] + "..."
        print(f"{key}: {len(g)}")
        esc = g.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
        out.append(f'    "{key}": (\n        """{esc}"""\n    ),\n')
    out.append("}\n")
    OUT.write_text("".join(out), encoding="utf-8")
    print("Wrote", OUT)


if __name__ == "__main__":
    main()
