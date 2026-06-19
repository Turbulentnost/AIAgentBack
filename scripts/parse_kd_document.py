#!/usr/bin/env python3
"""CLI для локального тестирования парсера КД (PDF, DXF)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = PROJECT_ROOT / "ескд-путь.txt"
KD_EXTENSIONS = {".pdf", ".dxf", ".dwg"}


def _resolve_source_path(raw: str) -> Path:
    raw = raw.strip()
    if raw.startswith("file://"):
        parsed = urlparse(raw)
        path = unquote(parsed.path)
        if parsed.netloc and not (len(path) > 2 and path[2] == ":"):
            windows_path = path.replace("/", "\\")
            return Path(f"\\\\{parsed.netloc}{windows_path}")
        if path.startswith("/") and len(path) > 2 and path[2] == ":":
            path = path[1:]
        return Path(path.replace("/", "\\"))
    return Path(raw)


def _load_default_source() -> Path:
    if DEFAULT_SOURCE.is_file():
        first_line = DEFAULT_SOURCE.read_text(encoding="utf-8").strip()
        if first_line:
            return _resolve_source_path(first_line)
    raise FileNotFoundError(
        f"Укажите путь к PDF/DXF или заполните {DEFAULT_SOURCE} (file:// URL или путь)"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source",
        nargs="?",
        help="Путь или file:// URL к файлу КД (PDF, DXF; по умолчанию — из ескд-путь.txt)",
    )
    parser.add_argument(
        "-p",
        "--page",
        type=int,
        default=None,
        help="Номер страницы PDF (1-based); для DXF игнорируется",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "reports" / "kd_parse",
        help="Каталог для PNG схемы и JSON-отчёта",
    )
    parser.add_argument("--no-scheme", action="store_true", help="Не сохранять PNG схемы")
    parser.add_argument("--zoom", type=float, default=3.0, help="Масштаб рендера схемы")
    args = parser.parse_args()

    sys.path.insert(0, str(PROJECT_ROOT))
    from app.services.document_processing.kd import KDParser

    source = _resolve_source_path(args.source) if args.source else _load_default_source()
    if not source.is_file():
        print(f"Файл не найден: {source}", file=sys.stderr)
        return 1

    if source.suffix.lower() not in KD_EXTENSIONS:
        print(f"Предупреждение: расширение {source.suffix!r} не из {sorted(KD_EXTENSIONS)}", file=sys.stderr)

    page_numbers = None if source.suffix.lower() == ".dxf" else [args.page or 9]

    result = KDParser().parse_path(
        source,
        page_numbers=page_numbers,
        render_scheme=not args.no_scheme,
        scheme_zoom=args.zoom,
    )
    page = result.pages[0]
    page_label = page.page_number if result.source_format == "pdf" else "dxf"

    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.output_dir / f"{source.stem}_p{page_label}_kd.json"
    scheme_path = args.output_dir / f"{source.stem}_p{page_label}_scheme.png"

    report_payload = result.model_dump(exclude={"pages": {"__all__": {"scheme_png_base64"}}})
    report_payload["pages"] = [
        {
            **page.model_dump(exclude={"scheme_png_base64"}),
            "scheme_saved_to": str(scheme_path) if page.scheme_png_base64 else None,
        }
    ]
    report_path.write_text(json.dumps(report_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if page.scheme_png_base64:
        import base64

        scheme_path.write_bytes(base64.b64decode(page.scheme_png_base64))

    print("=== KD parse result ===")
    print(f"Source: {source}")
    print(f"Format: {result.source_format}")
    print(f"Page: {page.page_number} ({page.width:.0f}x{page.height:.0f} pt)")
    print(f"Scan: {page.is_scan}, requires_ocr: {page.requires_ocr}")
    print(f"Title block chars: {page.title_block.char_count} ({page.title_block.method})")
    print(f"Drawing chars: {page.drawing.char_count}")
    print(f"ESKD text sample: {page.eskd_text[:300]!r}")
    if result.metadata.get("detected_designation"):
        print(f"Detected designation: {result.metadata['detected_designation']}")
    print(f"Report: {report_path}")
    if page.scheme_png_base64:
        print(f"Scheme PNG: {scheme_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
