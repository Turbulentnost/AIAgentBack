"""Build expanded GOST_GUIDANCE from PDFs in База ГОСТов."""
from __future__ import annotations

import re
import textwrap
from pathlib import Path

import fitz

GOST_BASE = Path(
    r"\\192.168.1.198\project\Служба качества\Проекты развития и улучшений"
    r"\Автоматизация проверки КД на соответствие требованиям ЕСКД\База ГОСТов"
)
OUT_PY = Path(
    r"c:\Users\mdj\Desktop\рабочее\agent_nd\агенты\eskd-agent\model\gost_guidance_expanded.py"
)
CURATED_PY = OUT_PY.parent / "gost_guidance_curated.py"
MAX_GUIDANCE_CHARS = 2200
MAX_SNIPPET_CHARS = 700

KEY_TO_PDF_PATTERN: dict[str, str] = {
    "2.104": r"2\.104",
    "2.201": r"2\.201",
    "2.109": r"2\.109",
    "2.503": r"2\.503",
    "2.316": r"2\.316",
    "2.308": r"2\.308",
    "2.105": r"2\.102",  # spec/BOM rules partly in 2.102 + 2.109; no 2.105 PDF in base
    "2.301": r"2\.104",  # formats referenced; extract 2.301 mentions from 2.104/2.109
}

EXTRA_PDFS = {
    "2.105": [r"2\.109"],
    "2.301": [r"2\.109"],
}


def find_pdf(pattern: str) -> Path | None:
    for pdf in GOST_BASE.rglob("*.pdf"):
        if re.search(pattern, pdf.name):
            return pdf
    return None


def pdf_text(path: Path, max_pages: int = 80) -> str:
    doc = fitz.open(str(path))
    chunks = [doc[i].get_text("text") for i in range(min(len(doc), max_pages))]
    doc.close()
    return "\n".join(chunks)


def clean(text: str) -> str:
    text = text.replace("\u00ad", "")
    text = text.replace("\xa0", " ")
    text = re.sub(r"Страница \d+\s*", "", text)
    text = re.sub(r"ИС «Техэксперт:.*?Интранет\s*", "", text)
    text = re.sub(r"ГОСТ Р 2\.\d{3}-\d{4}.*?\n", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _is_toc_line(line: str) -> bool:
    return bool(re.search(r"\. \.|\. \. \.", line))


def compress_guidance(text: str) -> str:
    """Сжать текст: склеить переносы, убрать мусор PDF, дедупликация."""
    text = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)
    text = re.sub(r"Применяется с \d{2}\.\d{2}\.\d{4}.*?\n", "", text)
    text = re.sub(r"Издание официальное.*?\n", "", text)
    text = re.sub(r"ГОСТ Р 2\.\d{3}—\d{4}.*?\n", "", text)
    text = re.sub(r"^\d+\s*$", "", text, flags=re.M)
    text = re.sub(r"П р и м е ч а н и е —.*", "", text)
    text = re.sub(r"\(Поправка\).*?\n", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    seen: set[str] = set()
    uniq: list[str] = []
    for p in paras:
        key = re.sub(r"\s+", " ", p)[:120]
        if key in seen or len(p) < 12:
            continue
        seen.add(key)
        uniq.append(p)
    return re.sub(r"\n{3,}", "\n\n", "\n\n".join(uniq)).strip()


def load_curated() -> dict[str, str]:
    import importlib.util

    spec = importlib.util.spec_from_file_location("gost_guidance_curated", CURATED_PY)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return dict(mod.CURATED_NUANCES)


def snippet_once(text: str, section: str, *, max_chars: int = MAX_SNIPPET_CHARS) -> str:
    block = extract_section_once(text, section, max_chars=max_chars)
    return compress_guidance(block) if block else ""


def remove_doc_type_matrix(text: str) -> str:
    """Убрать табл. 1 ГОСТ 2.102 (матрица видов КД)."""
    text = re.sub(
        r"Т а б л и ц а 1[^\n]*\n.*?Наименование вида КД.*?Окончание таблицы 1",
        "",
        text,
        flags=re.S,
    )
    text = re.sub(
        r"Код\s*\n?\s*вида\s*\n?\s*КД.*?Ремонтные документы[^\n]*(?:\n[^\n]*){0,3}",
        "",
        text,
        flags=re.S,
    )
    text = re.sub(
        r"Программа и методика испытаний\s*\n(?:—\s*\n|\u03bf\s*\n|ο\s*\n)+",
        "",
        text,
        flags=re.I,
    )
    kept: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if s in {"\u03bf", "ο", "—", "•", "…"}:
            continue
        if re.fullmatch(r"[А-ЯA-Z]{1,2}", s) and s in {
            "ТУ", "ПМ", "ТБ", "РР", "И", "Д", "С", "В", "Э",
        }:
            continue
        kept.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


def extract_section_once(text: str, section: str, *, max_chars: int = 3500) -> str:
    """Extract the main body of a top-level section (skip TOC/amendment stubs)."""
    num = section.strip()
    header_pat = rf"(?m)^{re.escape(num)}[\s\u2002]+[^\n]+"
    end_pat = rf"(?=\n\d{{1,2}}[\s\u2002][А-ЯЁA-Z]|$)"
    best = ""
    for m in re.finditer(header_pat, text):
        header = m.group(0)
        if _is_toc_line(header):
            continue
        if "Правила внесения изменений" in header or "Изменение №" in header:
            continue
        rest = text[m.end() :]
        em = re.search(end_pat, rest, re.S)
        body = header + "\n" + (rest[: em.start()] if em else rest[:max_chars])
        block = clean(body)
        if len(block) > len(best):
            best = block
    if len(best) > max_chars:
        best = best[: max_chars - 3].rsplit("\n", 1)[0] + "..."
    return best


def extract_sections(text: str, section_nums: list[str], *, max_chars: int = 6000) -> str:
    """Grab numbered sections (e.g. '5', '5.1') until next top-level section."""
    parts: list[str] = []
    seen: set[str] = set()
    for num in section_nums:
        num_clean = num.strip()
        if "." not in num_clean and num_clean in {n.split(".")[0] for n in section_nums if "." in n}:
            continue
        pat = (
            rf"(?m)^{re.escape(num_clean)}[\s\u2002]+[^\n]*\n"
            rf"(.*?)(?=\n\d{{1,2}}(?:\.\d+)?[\s\u2002]|$)"
        )
        for m in re.finditer(pat, text, re.S):
            block = clean(m.group(0))
            header = block.split("\n", 1)[0]
            if _is_toc_line(header):
                continue
            if len(block) < 40:
                continue
            if block in seen:
                continue
            seen.add(block)
            parts.append(block)
    blob = "\n\n".join(parts)
    if len(blob) > max_chars:
        blob = blob[: max_chars - 3].rsplit("\n", 1)[0] + "..."
    return blob


def extract_between(text: str, start: str, end: str, *, max_chars: int = 3500) -> str:
    """Extract longest text between markers (skip TOC hits with dot leaders)."""
    best = ""
    idx = 0
    while True:
        i = text.find(start, idx)
        if i < 0:
            break
        idx = i + 1
        header_end = text.find("\n", i)
        header = text[i:header_end if header_end > i else i + 120]
        if _is_toc_line(header):
            continue
        j = text.find(end, i + len(start))
        chunk = clean(text[i : j if j > i else i + max_chars])
        if len(chunk) > len(best):
            best = chunk
    if len(best) > max_chars:
        best = best[: max_chars - 3].rsplit("\n", 1)[0] + "..."
    return best


def extract_keywords(text: str, keywords: list[str], *, window: int = 400, limit: int = 12) -> str:
    found: list[str] = []
    low = text.lower()
    for kw in keywords:
        start = 0
        while len(found) < limit:
            idx = low.find(kw.lower(), start)
            if idx < 0:
                break
            snippet = clean(text[max(0, idx - 80) : idx + window])
            snippet = remove_doc_type_matrix(snippet)
            if snippet and snippet not in found:
                found.append(snippet)
            start = idx + len(kw)
    return "\n\n".join(found[:limit])


HEADERS: dict[str, str] = {
    "2.104": (
        "ГОСТ Р 2.104-2023 — штамп и реквизиты листа.\n\n"
        "ПРОВЕРИТЬ: форма штампа (прил. Б); ●-графы заполнены; лист/листов; подписи; масштаб на чертежах.\n"
        "НАРУШЕНИЯ: пустые ●-графы; лист≠комплект; нечитаемые подписи; неверная форма для формата.\n\n"
        "НЮАНСЫ:\n"
    ),
    "2.201": (
        "ГОСТ Р 2.201-2023 — обозначение.\n\n"
        "ПРОВЕРИТЬ: уникальность; структура org+класс+номер; .01 исполнения; единообразие штамп/BOM/выноски.\n"
        "НАРУШЕНИЯ: расхождение обозначений; опечатки; неверный код org.\n\n"
        "НЮАНСЫ:\n"
    ),
    "2.105": (
        "ГОСТ 2.105 / 2.102 — спецификация (BOM).\n\n"
        "ПРОВЕРИТЬ: разделы BOM; поз./обозн./наим./кол.; позиции ↔ выноски; порядок и нумерация.\n"
        "НАРУШЕНИЯ: позиция без строки BOM; строка без выноски; дубли/пропуски.\n\n"
        "НЮАНСЫ:\n"
    ),
    "2.109": (
        "ГОСТ Р 2.109-2023 — чертежи, виды, позиции.\n\n"
        "ПРОВЕРИТЬ: достаточность видов; подписи А-А; масштабы; выноски; номера позиций.\n"
        "НАРУШЕНИЯ: вид без подписи; позиция без выноски; перегрузка поля.\n\n"
        "НЮАНСЫ:\n"
    ),
    "2.503": (
        "ГОСТ Р 2.503-2023 — изменения.\n\n"
        "ПРОВЕРИТЬ: литера штамп ↔ таблица изменений; ИИ/ПИ; версия на всех листах.\n"
        "НАРУШЕНИЯ: литера без записи; битая ссылка на ИИ; разные литеры в комплекте.\n\n"
        "НЮАНСЫ:\n"
    ),
    "2.316": (
        "ГОСТ Р 2.316-2023 — ТТ и надписи.\n\n"
        "ПРОВЕРИТЬ: читаемость ТТ; действующие ссылки ГОСТ; шероховатость/покрытия; нет противоречий.\n"
        "НАРУШЕНИЯ: отменённый ГОСТ; нечитаемый ТТ; конфликт с размерами.\n\n"
        "НЮАНСЫ:\n"
    ),
    "2.308": (
        "ГОСТ Р 2.308-2023 — допуски формы и расположения.\n\n"
        "ПРОВЕРИТЬ: символы допусков; базы в рамке; выносные элементы; согласованность с ТТ.\n"
        "НАРУШЕНИЯ: допуск без базы; неверный символ; конфликт на видах.\n\n"
        "НЮАНСЫ:\n"
    ),
    "2.301": (
        "ГОСТ 2.301 / 2.302 — формат и масштаб.\n\n"
        "ПРОВЕРИТЬ: формат А0–А4; масштаб в штампе/у вида; допустимые масштабы; линии по 2.303.\n"
        "НАРУШЕНИЯ: масштаб не указан/не соответствует; штамп ≠ формат листа.\n\n"
        "НЮАНСЫ:\n"
    ),
}

# (gost_key, primary_pdf_text, snippet_section, extra_pdf_text, extra_section)
BUILD_SPEC: dict[str, tuple[str | None, str | None, str | None, str | None]] = {
    "2.104": ("main", "5.1", None, None),
    "2.201": ("main", "5.2", None, None),
    "2.105": ("extra_0", "7.3", "main", "5.3"),
    "2.109": ("main", "5.2", None, None),
    "2.503": ("main", "5.1", None, None),
    "2.316": ("main", "5.1", None, None),
    "2.308": ("main", "5.1", None, None),
    "2.301": (None, None, None, None),
}


def assemble_guidance(
    key: str,
    curated: dict[str, str],
    texts: dict[str, str],
    extras: list[str],
) -> str:
    header = HEADERS[key]
    body = curated.get(key, "").strip()
    spec = BUILD_SPEC[key]
    snippets: list[str] = []

    def _text_for(src: str | None) -> str:
        if src == "extra_0":
            return extras[0] if extras else ""
        if src == "main":
            return texts.get(key, "")
        return ""

    for src_key, section in ((spec[0], spec[1]), (spec[2], spec[3])):
        if not section:
            continue
        raw = _text_for(src_key)
        if raw:
            sn = snippet_once(raw, section, max_chars=500)
            if sn:
                snippets.append(sn)

    parts = [header + body]
    if snippets:
        parts.append("\n\nИЗ СТАНДАРТА:\n" + "\n".join(snippets))
    g = compress_guidance("\n".join(parts))
    g = remove_doc_type_matrix(g)
    if len(g) > MAX_GUIDANCE_CHARS:
        g = g[: MAX_GUIDANCE_CHARS - 3].rsplit("\n", 1)[0] + "..."
    return g


def main() -> None:
    curated = load_curated()
    texts: dict[str, str] = {}
    for key, pat in KEY_TO_PDF_PATTERN.items():
        pdf = find_pdf(pat)
        if pdf:
            texts[key] = clean(pdf_text(pdf))
            print(f"{key}: {pdf.name} ({len(texts[key])} chars)")
        else:
            print(f"{key}: PDF not found for {pat}")

    guidance: dict[str, str] = {}
    for key in HEADERS:
        extras_list: list[str] = []
        for ep in EXTRA_PDFS.get(key, []):
            pdf = find_pdf(ep)
            if pdf:
                extras_list.append(clean(pdf_text(pdf, max_pages=40)))
        g = assemble_guidance(key, curated, texts, extras_list)
        guidance[key] = g
        print(f"  guidance {key}: {len(g)} chars")

    lines = [
        '"""Compact GOST guidance for prompts (curated + minimal PDF snippets)."""\n\n',
        "GOST_GUIDANCE_EXPANDED: dict[str, str] = {\n",
    ]
    for key in [
        "2.104",
        "2.201",
        "2.105",
        "2.109",
        "2.503",
        "2.316",
        "2.308",
        "2.301",
    ]:
        val = guidance[key].replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
        lines.append(f'    "{key}": (\n        """{val}"""\n    ),\n')
    lines.append("}\n")
    OUT_PY.write_text("".join(lines), encoding="utf-8")
    print("Wrote", OUT_PY)


if __name__ == "__main__":
    main()
