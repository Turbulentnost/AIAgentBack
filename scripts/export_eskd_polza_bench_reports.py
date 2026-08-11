"""Offline export of Polza VLM bench results into per-model Markdown reports.

Reads only data/eskd_polza_bench/results + COMPARISON.json — no API calls.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "data" / "eskd_polza_bench"
RESULTS = BENCH / "results"
OUT = BENCH / "reports_by_model"
COMPARISON_JSON = BENCH / "COMPARISON.json"
COMPARISON_MD = BENCH / "COMPARISON.md"


def _slug(model: str) -> str:
    return model.replace("/", "__").replace(":", "_")


def _fmt_remark(r: dict) -> list[str]:
    sev = str(r.get("severity") or "?")
    code = str(r.get("code") or "")
    msg = str(r.get("message") or "").strip()
    found = str(r.get("found") or "").strip()
    hint = str(r.get("action_hint") or "").strip()
    lines = [f"- **[{sev}]** {code}: {msg}" if code else f"- **[{sev}]** {msg}"]
    if found:
        lines.append(f"  - found: {found}")
    if hint:
        lines.append(f"  - hint: {hint}")
    return lines


def _load_comparison() -> dict:
    if COMPARISON_JSON.is_file():
        return json.loads(COMPARISON_JSON.read_text(encoding="utf-8"))
    return {}


def export_model(model_dir: Path, comparison: dict) -> tuple[str, Path]:
    summary_path = model_dir / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else {}
    model = summary.get("model") or model_dir.name.replace("__", "/")
    note = summary.get("requested_note") or ""
    quality = summary.get("kronshtein_quality") or {}
    cmp_model = (comparison.get("models") or {}).get(model) or summary

    lines: list[str] = [
        f"# {model}",
        "",
    ]
    if note and note.replace("requested:", "") != model:
        lines += [f"Запрошено: `{note.replace('requested:', '')}`", ""]
    lines += [
        "## Сводка",
        "",
        f"- Время: **{cmp_model.get('elapsed_sec', summary.get('elapsed_sec'))}** сек",
        f"- Стоимость: **{cmp_model.get('cost_rub', summary.get('cost_rub'))}** ₽",
        f"- JSON%: **{cmp_model.get('json_valid_rate', summary.get('json_valid_rate'))}**",
        f"- schema%: **{cmp_model.get('schema_ok_rate', summary.get('schema_ok_rate'))}**",
        f"- avg сек/вызов: **{cmp_model.get('avg_elapsed_sec', summary.get('avg_elapsed_sec'))}**",
        f"- errors/warnings: **{cmp_model.get('total_errors', summary.get('total_errors'))}** / "
        f"**{cmp_model.get('total_warnings', summary.get('total_warnings'))}**",
        f"- kron recall: **{quality.get('recall', '—')}** "
        f"({quality.get('hits', '?')}/{quality.get('expected', '?')})",
        "",
    ]
    if quality.get("details"):
        lines.append("### Эталонные находки (кронштейн)")
        lines.append("")
        for d in quality["details"]:
            mark = "✓" if d.get("hit") else "✗"
            lines.append(f"- {mark} `{d.get('id')}`")
        lines.append("")

    pdf_files = sorted(
        p for p in model_dir.glob("*.json") if p.name != "summary.json"
    )
    for pdf_json in pdf_files:
        bundle = json.loads(pdf_json.read_text(encoding="utf-8"))
        pdf_name = bundle.get("pdf") or pdf_json.stem
        lines += [f"## PDF: `{pdf_name}`", ""]
        for page in bundle.get("pages") or []:
            page_no = page.get("page")
            lines.append(f"### Страница {page_no}")
            lines.append("")
            gosts = page.get("gosts") or {}
            for gost_key in sorted(gosts.keys()):
                call = gosts[gost_key]
                ok = call.get("ok")
                t = call.get("elapsed_sec")
                cost = call.get("cost_rub")
                e = call.get("errors_count")
                w = call.get("warnings_count")
                jv = call.get("json_valid")
                lines.append(
                    f"#### ГОСТ {gost_key} — ok={ok} t={t}s cost={cost} E={e} W={w} json={jv}"
                )
                lines.append("")
                if call.get("error"):
                    lines.append(f"- ERROR: {call['error']}")
                    lines.append("")
                    continue
                remarks = call.get("remarks") or []
                if not remarks:
                    preview = (call.get("raw_preview") or "").strip()
                    if preview:
                        lines.append("_Замечаний в JSON нет; raw preview:_")
                        lines.append("")
                        lines.append("```")
                        lines.append(preview[:1500])
                        lines.append("```")
                    else:
                        lines.append("_Замечаний нет._")
                    lines.append("")
                    continue
                for r in remarks:
                    if isinstance(r, dict):
                        lines.extend(_fmt_remark(r))
                lines.append("")

    out_path = OUT / f"{_slug(model)}.md"
    out_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return model, out_path


def main() -> int:
    if not RESULTS.is_dir():
        raise SystemExit(f"Missing results dir: {RESULTS}")
    OUT.mkdir(parents=True, exist_ok=True)
    comparison = _load_comparison()
    models: list[tuple[str, Path]] = []
    for model_dir in sorted(p for p in RESULTS.iterdir() if p.is_dir()):
        models.append(export_model(model_dir, comparison))

    index = [
        "# Polza VLM bench — отчёты по моделям",
        "",
        "Сгенерировано офлайн из `results/` (без API).",
        "",
        "## Модели",
        "",
    ]
    for model, path in models:
        index.append(f"- [`{model}`]({path.name})")
    index += ["", f"Сводка: [../COMPARISON.md](../COMPARISON.md)", ""]
    (OUT / "INDEX.md").write_text("\n".join(index), encoding="utf-8")

    if COMPARISON_MD.is_file():
        text = COMPARISON_MD.read_text(encoding="utf-8")
        link_block = (
            "\n## Отчёты по моделям\n\n"
            "Подробные замечания (офлайн): "
            "[reports_by_model/INDEX.md](reports_by_model/INDEX.md)\n"
        )
        if "reports_by_model/INDEX.md" not in text:
            COMPARISON_MD.write_text(text.rstrip() + "\n" + link_block, encoding="utf-8")

    print(f"Wrote {len(models)} reports -> {OUT}")
    for model, path in models:
        print(f"  {model} -> {path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
