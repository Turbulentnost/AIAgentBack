#!/usr/bin/env python3
"""Compare Tesseract OCR on UFG page 9 against EasyOCR/rebuilt PDF baselines."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fitz

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.convert_pdf_page_to_text_pdf import (  # noqa: E402
    _prepare_ocr_image,
    _resolve_source_path,
)
from app.services.document_processing.kd.tesseract_ocr import tesseract_ocr_image  # noqa: E402
from scripts.rebuild_pdf_page_with_scheme_png import detect_regions  # noqa: E402

DEFAULT_SOURCE_HINT = PROJECT_ROOT / "ескд-путь.txt"
FIXTURES = PROJECT_ROOT / "tests" / "eskd" / "fixtures" / "pdfs"
REPORTS_DIR = PROJECT_ROOT / "reports"

EASYOCR_PDF = FIXTURES / "UFG-800-16.02.00.000_page9_text.pdf"
REBUILT_PDF = FIXTURES / "UFG-800-16.02.00.000_page9_rebuilt.pdf"

PSM_MODES = (3, 6, 11)
DEFAULT_OCR_ROTATION = 90
DEFAULT_DPI = 300

KEY_FIELDS: dict[str, list[str]] = {
    "designation": [
        r"UFG[\-\s]*800[\-\s]*16[\.\s]*02[\.\s]*11[\.\s]*000",
        r"UFG-800-16\.02\.11\.000",
    ],
    "designation_sb": [r"СБ", r"CB"],
    "view_bb": [r"[BbBb][\-\–—][BbBb]", r"В[\-\–—]В", r"\bBB\b"],
    "view_gg": [r"Г[\-\–—]Г", r"G[\-\–—]G", r"1\s*:\s*2"],
    "dimension_1106": [r"1106[\s,]*2\s*±\s*2", r"1106[,.]2"],
    "sheet_2": [r"лист\s*2", r"\bлист\b.*\b2\b", r"^2$"],
    "stamp_headers": [r"Изм\.", r"Лист", r"№ докум\.", r"Подп\.", r"Дата"],
    "inventory_labels": [
        r"Инв\.\s*№\s*подл",
        r"Подп\.\s*и\s*дата",
        r"Взам\.\s*инв",
        r"Инв\.\s*№\s*дубл",
    ],
}


@dataclass
class OcrRun:
    region: str
    psm: int
    char_count: int
    text: str
    key_hits: dict[str, bool] = field(default_factory=dict)
    key_score: float = 0.0
    method: str = "standard"


HAKATON_TARGET_SIZE = None  # для КД не уменьшаем — иначе теряются мелкие подписи


@dataclass
class BaselineText:
    name: str
    path: str
    exists: bool
    char_count: int
    text: str
    key_hits: dict[str, bool] = field(default_factory=dict)
    key_score: float = 0.0


def _read_default_source() -> str:
    if DEFAULT_SOURCE_HINT.is_file():
        return DEFAULT_SOURCE_HINT.read_text(encoding="utf-8").strip()
    return ""


def _find_tesseract_cmd() -> str | None:
    env_path = Path(__file__).resolve().parents[1] / "tools" / "tesseract" / "tesseract.exe"
    if env_path.is_file():
        return str(env_path)
    which = shutil.which("tesseract")
    if which:
        return which
    for candidate in (
        Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
        Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
    ):
        if candidate.is_file():
            return str(candidate)
    return None


def _score_key_fields(text: str) -> tuple[dict[str, bool], float]:
    hits: dict[str, bool] = {}
    for name, patterns in KEY_FIELDS.items():
        found = any(re.search(pattern, text, re.IGNORECASE | re.MULTILINE) for pattern in patterns)
        hits[name] = found
    score = sum(1 for value in hits.values() if value) / max(len(hits), 1)
    return hits, score


def _extract_pdf_text(pdf_path: Path) -> tuple[int, str]:
    if not pdf_path.is_file():
        return 0, ""
    doc = fitz.open(pdf_path)
    try:
        text = doc[0].get_text("text").strip()
    finally:
        doc.close()
    return len(text), text


def _render_page_image(page: fitz.Page, *, dpi: int, rotation: int):
    scale = dpi / 72.0
    matrix = fitz.Matrix(scale, scale)
    pix = page.get_pixmap(matrix=matrix, alpha=False)
    return _prepare_ocr_image(pix, rotation)


def _render_region_image(
    page: fitz.Page,
    clip: fitz.Rect,
    *,
    dpi: int,
    rotation: int,
):
    scale = dpi / 72.0
    matrix = fitz.Matrix(scale, scale)
    pix = page.get_pixmap(matrix=matrix, clip=clip, alpha=False)
    return _prepare_ocr_image(pix, rotation)


def _tesseract_ocr(image, *, lang: str, psm: int, tesseract_cmd: str) -> str:
    from app.services.document_processing.kd.tesseract_ocr import _configure_tesseract

    import pytesseract

    _configure_tesseract(tesseract_cmd)
    config = f"--psm {psm} --oem 3"
    return pytesseract.image_to_string(image, lang=lang, config=config).strip()


def _run_tesseract_matrix(
    page: fitz.Page,
    regions: PageRegionsLike,
    *,
    dpi: int,
    rotation: int,
    tesseract_cmd: str,
    include_hakaton: bool = True,
) -> tuple[list[OcrRun], list[OcrRun]]:
    runs: list[OcrRun] = []
    hakaton_runs: list[OcrRun] = []

    full_image, _, _ = _render_page_image(page, dpi=dpi, rotation=rotation)
    title_image, _, _ = _render_region_image(
        page,
        regions.title_block,
        dpi=dpi,
        rotation=-90,
    )

    jobs = [
        ("full_page", full_image),
        ("title_block", title_image),
    ]
    for region_name, image in jobs:
        for psm in PSM_MODES:
            text = _tesseract_ocr(image, lang="rus+eng", psm=psm, tesseract_cmd=tesseract_cmd)
            hits, score = _score_key_fields(text)
            runs.append(
                OcrRun(
                    region=region_name,
                    psm=psm,
                    char_count=len(text),
                    text=text,
                    key_hits=hits,
                    key_score=score,
                    method="standard",
                )
            )
        if include_hakaton:
            for variant, undistort in (("hakaton", True), ("hakaton_no_undistort", False)):
                text = tesseract_ocr_image(
                    image,
                    preprocess="hakaton",
                    hakaton_target_size=HAKATON_TARGET_SIZE,
                    hakaton_apply_undistort=undistort,
                    tesseract_cmd=tesseract_cmd,
                )
                hits, score = _score_key_fields(text)
                hakaton_runs.append(
                    OcrRun(
                        region=region_name,
                        psm=6,
                        char_count=len(text),
                        text=text,
                        key_hits=hits,
                        key_score=score,
                        method=variant,
                    )
                )
    return runs, hakaton_runs


@dataclass
class PageRegionsLike:
    title_block: fitz.Rect


def _best_tesseract_run(runs: list[OcrRun]) -> OcrRun | None:
    if not runs:
        return None
    return max(runs, key=lambda item: (item.key_score, item.char_count))


def _run_eskd_validation(text: str, designation: str = "UFG-800-16.02.11.000") -> dict[str, Any]:
    from app.eskd.validation.engine import EskdValidationEngine
    from app.models.enums import DocumentType, EskdDocumentKind, TextExtractStatus
    from tests.eskd.mocks import build_validation_context

    context = build_validation_context(
        designation=designation,
        document_kind=EskdDocumentKind.DRAWING,
        document_title="Преобразователь расхода ультразвуковой",
        original_filename=f"{designation}.pdf",
        document_type=DocumentType.KD,
        text_extract_status=TextExtractStatus.EXTRACTED,
        document_text=text,
        qms_document_code=designation,
        owner_department="ОКР",
    )
    report = EskdValidationEngine().validate(context)
    return {
        "passed": report.passed,
        "score": round(report.score, 4),
        "summary": report.summary,
        "failed_codes": [item.code for item in report.checks if not item.passed],
    }


def _format_comparison_table(
    baselines: list[BaselineText],
    tesseract_runs: list[OcrRun],
    best: OcrRun | None,
    hakaton_runs: list[OcrRun] | None = None,
    hakaton_best: OcrRun | None = None,
) -> str:
    lines: list[str] = []
    lines.append("=" * 90)
    lines.append("UFG page 9 — сравнение OCR")
    lines.append("=" * 90)
    lines.append("")
    lines.append("## Символы")
    lines.append(f"{'Источник':<42} {'Символов':>10} {'Key score':>12}")
    lines.append("-" * 66)
    for item in baselines:
        status = "OK" if item.exists else "MISSING"
        lines.append(
            f"{item.name:<42} {item.char_count:>10} {item.key_score:>11.0%}  [{status}]"
        )
    for run in tesseract_runs:
        label = f"tesseract/{run.region}/psm{run.psm}"
        lines.append(f"{label:<42} {run.char_count:>10} {run.key_score:>11.0%}")
    if hakaton_runs:
        for run in hakaton_runs:
            label = f"{run.method}/{run.region}/psm{run.psm}"
            lines.append(f"{label:<42} {run.char_count:>10} {run.key_score:>11.0%}")
    if best:
        lines.append("")
        lines.append(
            f"Лучший Tesseract (standard): {best.region} PSM {best.psm} "
            f"({best.char_count} chars, key score {best.key_score:.0%})"
        )
    if hakaton_best:
        lines.append(
            f"Лучший Tesseract (hakaton): {hakaton_best.region} PSM {hakaton_best.psm} "
            f"({hakaton_best.char_count} chars, key score {hakaton_best.key_score:.0%})"
        )

    lines.append("")
    lines.append("## Ключевые поля (✓ = найдено)")
    field_names = list(KEY_FIELDS.keys())
    header = f"{'Поле':<18}" + "".join(f"{name[:10]:>12}" for name in field_names)
    lines.append(header)
    lines.append("-" * len(header))

    def _row(label: str, hits: dict[str, bool]) -> str:
        cells = "".join(f"{'Y' if hits.get(name) else '.':>12}" for name in field_names)
        return f"{label[:18]:<18}{cells}"

    for item in baselines:
        if item.exists:
            lines.append(_row(item.name, item.key_hits))
    if best:
        lines.append(_row("tesseract std best", best.key_hits))
    if hakaton_best:
        lines.append(_row("tesseract hakaton", hakaton_best.key_hits))

    lines.append("")
    lines.append("## Образцы текста")
    for item in baselines:
        if item.exists and item.text:
            lines.append(f"\n--- {item.name} (first 400 chars) ---")
            lines.append(item.text[:400])
    if best and best.text:
        lines.append(f"\n--- tesseract standard best ({best.region} psm{best.psm}) ---")
        lines.append(best.text[:400])
    if hakaton_best and hakaton_best.text:
        lines.append(
            f"\n--- tesseract hakaton best ({hakaton_best.region} psm{hakaton_best.psm}) ---"
        )
        lines.append(hakaton_best.text[:400])

    return "\n".join(lines)


def compare_ufg_page9(
    source: Path,
    *,
    page_number: int = 9,
    dpi: int = DEFAULT_DPI,
    ocr_rotation: int = DEFAULT_OCR_ROTATION,
    run_validation: bool = True,
) -> dict[str, Any]:
    tesseract_cmd = _find_tesseract_cmd()
    tesseract_version: str | None = None
    if tesseract_cmd:
        import subprocess

        try:
            proc = subprocess.run(
                [tesseract_cmd, "--version"],
                capture_output=True,
                text=True,
                check=False,
            )
            tesseract_version = (proc.stdout or proc.stderr).splitlines()[0]
        except OSError:
            tesseract_cmd = None

    baselines: list[BaselineText] = []
    for name, path in (
        ("easyocr_overlay", EASYOCR_PDF),
        ("rebuilt_pdf", REBUILT_PDF),
    ):
        char_count, text = _extract_pdf_text(path)
        hits, score = _score_key_fields(text)
        baselines.append(
            BaselineText(
                name=name,
                path=str(path),
                exists=path.is_file(),
                char_count=char_count,
                text=text,
                key_hits=hits,
                key_score=score,
            )
        )

    if not source.is_file():
        raise FileNotFoundError(f"Source PDF not found: {source}")

    doc = fitz.open(source)
    try:
        page = doc[page_number - 1]
        regions = detect_regions(page)
        source_text = page.get_text("text").strip()
        tesseract_runs: list[OcrRun] = []
        hakaton_runs: list[OcrRun] = []
        tesseract_error: str | None = None

        if tesseract_cmd:
            try:
                from app.services.document_processing.kd.tesseract_ocr import _configure_tesseract

                _configure_tesseract(tesseract_cmd)
                tesseract_runs, hakaton_runs = _run_tesseract_matrix(
                    page,
                    PageRegionsLike(title_block=regions.title_block),
                    dpi=dpi,
                    rotation=ocr_rotation,
                    tesseract_cmd=tesseract_cmd,
                )
            except Exception as exc:  # noqa: BLE001
                tesseract_error = str(exc)
        else:
            tesseract_error = (
                "Tesseract binary not found. Install UB-Mannheim Tesseract OCR "
                "or place tesseract.exe in tools/tesseract/"
            )
    finally:
        doc.close()

    best = _best_tesseract_run(tesseract_runs)
    hakaton_best = _best_tesseract_run(hakaton_runs)
    validation: dict[str, Any] | None = None
    hakaton_validation: dict[str, Any] | None = None
    if run_validation and best and best.text.strip():
        validation = _run_eskd_validation(best.text)
    if run_validation and hakaton_best and hakaton_best.text.strip():
        hakaton_validation = _run_eskd_validation(hakaton_best.text)

    rebuilt = next(item for item in baselines if item.name == "rebuilt_pdf")
    easyocr = next(item for item in baselines if item.name == "easyocr_overlay")

    closer = "none"
    if best or hakaton_best:
        scores = {
            "tesseract_standard": best.key_score if best else -1,
            "tesseract_hakaton": hakaton_best.key_score if hakaton_best else -1,
            "rebuilt_pdf": rebuilt.key_score if rebuilt.exists else -1,
            "easyocr_overlay": easyocr.key_score if easyocr.exists else -1,
        }
        closer = max(scores, key=scores.get)

    result: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": str(source),
        "page_number": page_number,
        "dpi": dpi,
        "ocr_rotation": ocr_rotation,
        "title_block_rect": [
            regions.title_block.x0,
            regions.title_block.y0,
            regions.title_block.x1,
            regions.title_block.y1,
        ],
        "region_notes": regions.detection_notes,
        "source_page_text_chars": len(source_text),
        "tesseract": {
            "cmd": tesseract_cmd,
            "version": tesseract_version,
            "error": tesseract_error,
            "runs": [asdict(item) for item in tesseract_runs],
            "best": asdict(best) if best else None,
            "hakaton_preprocess": {
                "source": "https://github.com/MaxJalo/model_for_hakaton",
                "steps": [
                    "cv2.undistort",
                    "grayscale",
                    "kernel blur",
                "adaptiveThreshold GAUSSIAN_C 11/2",
                "resize optional (None for KD)",
                ],
                "tesseract_config": "--psm 6 --oem 1",
                "lang": "rus",
                "target_size": HAKATON_TARGET_SIZE,
            },
            "hakaton_runs": [asdict(item) for item in hakaton_runs],
            "hakaton_best": asdict(hakaton_best) if hakaton_best else None,
        },
        "baselines": [asdict(item) for item in baselines],
        "comparison": {
            "closer_to_eskd_fields": closer,
            "char_counts": {
                "source_native": len(source_text),
                "easyocr_overlay": easyocr.char_count if easyocr.exists else None,
                "rebuilt_pdf": rebuilt.char_count if rebuilt.exists else None,
                "tesseract_standard_best": best.char_count if best else None,
                "tesseract_hakaton_best": hakaton_best.char_count if hakaton_best else None,
            },
            "key_scores": {
                "easyocr_overlay": easyocr.key_score if easyocr.exists else None,
                "rebuilt_pdf": rebuilt.key_score if rebuilt.exists else None,
                "tesseract_standard_best": best.key_score if best else None,
                "tesseract_hakaton_best": hakaton_best.key_score if hakaton_best else None,
            },
        },
        "eskd_validation_on_tesseract_best": validation,
        "eskd_validation_on_hakaton_best": hakaton_validation,
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source",
        nargs="?",
        default="",
        help="Path or file:// URL; default: ескд-путь.txt",
    )
    parser.add_argument("-p", "--page", type=int, default=9)
    parser.add_argument("--dpi", type=int, default=DEFAULT_DPI)
    parser.add_argument("--ocr-rotation", type=int, default=DEFAULT_OCR_ROTATION)
    parser.add_argument(
        "--output-json",
        type=Path,
        default=REPORTS_DIR / "ufg_tesseract_comparison.json",
    )
    parser.add_argument(
        "--output-txt",
        type=Path,
        default=REPORTS_DIR / "ufg_tesseract_comparison.txt",
    )
    parser.add_argument("--no-validation", action="store_true")
    args = parser.parse_args()

    raw_source = args.source.strip() or _read_default_source()
    if not raw_source:
        print("ERROR: source path required (argument or ескд-путь.txt)", file=sys.stderr)
        return 2

    source = _resolve_source_path(raw_source)
    result = compare_ufg_page9(
        source,
        page_number=args.page,
        dpi=args.dpi,
        ocr_rotation=args.ocr_rotation,
        run_validation=not args.no_validation,
    )

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_txt.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    baselines = [BaselineText(**item) for item in result["baselines"]]
    runs = [OcrRun(**item) for item in result["tesseract"]["runs"]]
    hakaton_runs = [OcrRun(**item) for item in result["tesseract"].get("hakaton_runs", [])]
    best_data = result["tesseract"]["best"]
    hakaton_best_data = result["tesseract"].get("hakaton_best")
    best = OcrRun(**best_data) if best_data else None
    hakaton_best = OcrRun(**hakaton_best_data) if hakaton_best_data else None
    txt = _format_comparison_table(baselines, runs, best, hakaton_runs, hakaton_best)
    txt += "\n\n"
    txt += f"Tesseract: {result['tesseract']['cmd'] or 'NOT FOUND'}\n"
    if result["tesseract"]["version"]:
        txt += f"Version: {result['tesseract']['version']}\n"
    if result["tesseract"]["error"]:
        txt += f"Error: {result['tesseract']['error']}\n"
    txt += f"Closer to ESKD fields: {result['comparison']['closer_to_eskd_fields']}\n"
    if result["eskd_validation_on_tesseract_best"]:
        val = result["eskd_validation_on_tesseract_best"]
        txt += f"ESKD validation (tesseract standard): passed={val['passed']}, score={val['score']}\n"
    if result.get("eskd_validation_on_hakaton_best"):
        val = result["eskd_validation_on_hakaton_best"]
        txt += f"ESKD validation (tesseract hakaton): passed={val['passed']}, score={val['score']}\n"
    args.output_txt.write_text(txt, encoding="utf-8")

    print(txt.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8"))
    print(f"\nJSON: {args.output_json}")
    print(f"TXT:  {args.output_txt}")
    return 0 if result["tesseract"]["cmd"] and not result["tesseract"]["error"] else 1


if __name__ == "__main__":
    sys.exit(main())
