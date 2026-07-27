"""Локальный staging файлов перед загрузкой в 1С (аудит, round-trip проверка)."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import structlog

from agent_pochta.config import PROJECT_ROOT, get_settings

logger = structlog.get_logger(__name__)

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass(frozen=True)
class StagedAttachment:
    """Файл, записанный на диск агента перед OData POST."""

    path: Path
    filename: str
    size_bytes: int
    sha256: str
    manifest_path: Path


def _safe_segment(value: str, *, fallback: str = "unknown") -> str:
    cleaned = _UNSAFE.sub("_", (value or "").strip())
    return cleaned[:120] or fallback


def resolve_staging_root() -> Path:
    settings = get_settings()
    raw = (settings.odata_attach_staging_dir or "data/temp/erp_attach_staging").strip()
    root = Path(raw)
    if not root.is_absolute():
        root = PROJECT_ROOT / root
    return root


def stage_attachment_bytes(
    content: bytes,
    filename: str,
    *,
    document_ref_key: str,
    document_number: str | None = None,
    message_id: str | None = None,
) -> StagedAttachment:
    """Сохраняет байты вложения локально перед отправкой в 1С."""
    if not content:
        raise ValueError("stage_attachment_bytes: empty content")

    root = resolve_staging_root()
    doc_part = _safe_segment(document_number or document_ref_key[:8], fallback="doc")
    msg_part = _safe_segment(message_id or document_ref_key, fallback="msg")
    target_dir = root / doc_part / msg_part
    target_dir.mkdir(parents=True, exist_ok=True)

    safe_name = _UNSAFE.sub("_", filename.strip()) or "attachment.bin"
    path = target_dir / safe_name
    path.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()

    manifest_path = path.with_suffix(path.suffix + ".manifest.json")
    manifest = {
        "filename": filename,
        "local_path": str(path),
        "size_bytes": len(content),
        "sha256": digest,
        "document_ref_key": document_ref_key,
        "document_number": document_number,
        "message_id": message_id,
        "staged_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info(
        "erp_attachment_staged",
        path=str(path),
        size_bytes=len(content),
        sha256=digest[:16],
        document_number=document_number,
    )
    return StagedAttachment(
        path=path,
        filename=filename,
        size_bytes=len(content),
        sha256=digest,
        manifest_path=manifest_path,
    )


def read_staged_bytes(path: Path) -> bytes:
    """Перечитывает staged-файл с диска (то, что реально уйдёт в OData)."""
    data = path.read_bytes()
    if not data:
        raise ValueError(f"staged file is empty: {path}")
    return data


def write_roundtrip_report(
    staged: StagedAttachment,
    *,
    ref_key: str,
    odata_bytes: bytes,
    storage_kind: str,
    extra: dict | None = None,
) -> Path:
    """Сохраняет отчёт сравнения локального файла и байт из OData."""
    report_path = staged.path.with_suffix(staged.path.suffix + ".roundtrip.json")
    local = staged.path.read_bytes()
    report = {
        "ref_key": ref_key,
        "storage_kind": storage_kind,
        "local_size": len(local),
        "odata_size": len(odata_bytes),
        "local_sha256": hashlib.sha256(local).hexdigest(),
        "odata_sha256": hashlib.sha256(odata_bytes).hexdigest() if odata_bytes else "",
        "bytes_match": local == odata_bytes,
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        **(extra or {}),
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report_path


def cleanup_staged_attachment(staged: StagedAttachment) -> None:
    """Удаляет staged-файл и manifest после успешной загрузки."""
    for path in (staged.path, staged.manifest_path):
        try:
            if path.is_file():
                path.unlink()
        except OSError as exc:
            logger.warning("erp_attachment_staging_cleanup_failed", path=str(path), error=str(exc))

    parent = staged.path.parent
    try:
        if parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
            grand = parent.parent
            if grand.is_dir() and not any(grand.iterdir()):
                grand.rmdir()
    except OSError:
        pass

    logger.info("erp_attachment_staging_cleaned", path=str(staged.path))
