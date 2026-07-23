from __future__ import annotations

import re
from pathlib import Path

DOCUMENT_CODE_RE = re.compile(
    r"(?:СТО|И|РГ|ПЛ|ДИ|РИ|ПП|ВС|П)(?:-[A-ZА-ЯЁ0-9]+)+",
    re.IGNORECASE,
)

SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".doc",
    ".xlsx",
    ".xls",
    ".txt",
    ".csv",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
}
SKIP_NAME_PREFIXES = ("~$", ".")
SKIP_NAME_PARTS = {"thumbs.db", "desktop.ini"}


class DocumentFolderScanError(ValueError):
    pass


def extract_document_code(*, title: str | None, original_filename: str | None) -> str | None:
    for source in (original_filename, title):
        if not source:
            continue
        matches = DOCUMENT_CODE_RE.findall(source)
        if matches:
            return matches[-1].upper()
    return None


def fallback_document_code(seed: str) -> str:
    return f"ND-{seed[:8].upper()}"


def iter_supported_files(root: Path, *, recursive: bool = True) -> list[Path]:
    iterator = root.rglob("*") if recursive else root.iterdir()
    files: list[Path] = []
    for path in iterator:
        if not path.is_file():
            continue
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        lowered_name = path.name.lower()
        if any(lowered_name.startswith(prefix) for prefix in SKIP_NAME_PREFIXES):
            continue
        if lowered_name in SKIP_NAME_PARTS:
            continue
        files.append(path)
    return sorted(files, key=lambda item: item.name.lower())


def resolve_document_code(
    *,
    title: str,
    filename: str,
    existing_codes: set[str] | None = None,
    checksum: str,
) -> str:
    known_codes = existing_codes or set()
    code = extract_document_code(title=title, original_filename=filename)
    if not code:
        stem = Path(filename).stem.upper().replace(" ", "-")
        code = stem[:64] if stem else fallback_document_code(checksum)
    code = code.strip().upper()[:64]
    if code not in known_codes:
        return code
    suffix = checksum[:8].upper()
    base_limit = max(1, 64 - len(suffix) - 1)
    base = code[:base_limit].rstrip("-")
    candidate = f"{base}-{suffix}"[:64]
    index = 2
    while candidate in known_codes:
        index_suffix = f"{suffix}-{index}"
        base = code[: max(1, 64 - len(index_suffix) - 1)].rstrip("-")
        candidate = f"{base}-{index_suffix}"[:64]
        index += 1
    return candidate


def scan_folder(folder_path: str | Path, *, recursive: bool = True) -> list[dict]:
    root = Path(folder_path)
    if not root.exists():
        raise DocumentFolderScanError(f"Папка не найдена: {root}")
    if not root.is_dir():
        raise DocumentFolderScanError(f"Указанный путь не является папкой: {root}")

    rows: list[dict] = []
    known_codes: set[str] = set()
    for file_path in iter_supported_files(root, recursive=recursive):
        title = file_path.stem.strip() or file_path.name
        document_code = resolve_document_code(
            title=title,
            filename=file_path.name,
            existing_codes=known_codes,
            checksum=file_path.name,
        )
        known_codes.add(document_code)
        rows.append(
            {
                "name": file_path.name,
                "relative_path": file_path.relative_to(root).as_posix(),
                "source_path": str(file_path),
                "size": file_path.stat().st_size,
                "document_code": document_code,
            }
        )
    return rows
