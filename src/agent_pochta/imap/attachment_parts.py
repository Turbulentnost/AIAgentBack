"""Разбор IMAP BODYSTRUCTURE → part-id вложений."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ImapAttachmentPart:
    part_id: str
    mime_type: str
    filename: str | None
    size_bytes: int | None = None


def _as_list(node: object) -> list:
    if isinstance(node, tuple):
        return list(node)
    if isinstance(node, list):
        return node
    return []


def _params_dict(params: object) -> dict[str, str]:
    if not isinstance(params, (list, tuple)):
        return {}
    items = list(params)
    out: dict[str, str] = {}
    for idx in range(0, len(items) - 1, 2):
        key = items[idx]
        value = items[idx + 1]
        if isinstance(key, str) and isinstance(value, str):
            out[key.lower()] = value
    return out


def _decode_str(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")


def _filename_from_params(params: object) -> str | None:
    parsed = _params_dict(params)
    for key in ("name", "filename"):
        value = parsed.get(key, "").strip()
        if value:
            return value
    return None


def _disposition_type(structure: list | tuple) -> str | None:
    if len(structure) < 9:
        return None
    disposition = structure[8]
    if disposition in (None, "NIL"):
        return None
    if isinstance(disposition, (list, tuple)) and disposition:
        return _decode_str(disposition[0]).lower()
    return None


def _is_attachment_leaf(structure: list | tuple, *, filename: str | None, mime_type: str) -> bool:
    if filename:
        return True
    disposition = _disposition_type(structure)
    if disposition == "attachment":
        return True
    maintype = mime_type.split("/", 1)[0]
    if maintype in {"application", "image", "audio", "video"}:
        return True
    return False


def _is_multipart_structure(node: list) -> bool:
    if len(node) < 2:
        return False
    if not isinstance(node[-1], str):
        return False
    return isinstance(node[0], (list, tuple))


def list_attachment_parts(structure: object) -> list[ImapAttachmentPart]:
    """Обходит BODYSTRUCTURE и возвращает part-id вложений в порядке обхода."""
    parts: list[ImapAttachmentPart] = []

    def walk(node: object, prefix: str = "") -> None:
        body = _as_list(node)
        if not body:
            return

        if len(body) == 1 and isinstance(body[0], (list, tuple)):
            walk(body[0], prefix)
            return

        if _is_multipart_structure(body):
            for index, child in enumerate(body[:-1], start=1):
                part_id = f"{prefix}.{index}" if prefix else str(index)
                walk(child, part_id)
            return

        if not isinstance(body[0], str):
            return

        maintype = _decode_str(body[0]).lower()
        subtype = _decode_str(body[1]).lower() if len(body) > 1 else "octet-stream"
        params = body[2] if len(body) > 2 else None
        filename = _filename_from_params(params)
        if filename is None and len(body) > 8:
            disposition = body[8]
            if isinstance(disposition, (list, tuple)) and len(disposition) > 1:
                filename = _filename_from_params(disposition[1])
        mime_type = f"{maintype}/{subtype}"
        if not _is_attachment_leaf(body, filename=filename, mime_type=mime_type):
            return
        size_bytes: int | None = None
        if len(body) > 6 and isinstance(body[6], int) and body[6] >= 0:
            size_bytes = int(body[6])
        part_id = prefix or "1"
        parts.append(
            ImapAttachmentPart(
                part_id=part_id,
                mime_type=mime_type,
                filename=filename,
                size_bytes=size_bytes,
            )
        )

    walk(structure)
    return parts
