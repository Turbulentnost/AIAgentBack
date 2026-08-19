from __future__ import annotations

import asyncio
import base64
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.db.base import Base
from app.db.session import AsyncSessionLocal, engine
from app.models.wechat_message import WechatMessage

_tables_ready = False
_tables_lock = asyncio.Lock()


def _media_dir() -> Path:
    configured = (getattr(settings, "WECHAT_MEDIA_DIR", "") or "").strip()
    root = Path(configured) if configured else Path(__file__).resolve().parents[2] / "data" / "aveon" / "wechat_media"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _file_kind(message_type: str | None, mime: str | None) -> str | None:
    kind = (message_type or "").strip().lower()
    mime_type = (mime or "").strip().lower()
    if kind in {"image", "pic", "picture", "img"} or mime_type.startswith("image/"):
        return "image"
    if kind in {"video"} or mime_type.startswith("video/"):
        return "video"
    if kind in {"voice", "audio", "ptt"} or mime_type.startswith("audio/"):
        return "voice"
    if mime_type or kind:
        return "file"
    return None


def _safe_suffix(filename: str | None) -> str:
    suffix = Path(filename or "").suffix
    cleaned = "".join(char for char in suffix if char.isalnum() or char == ".")
    return cleaned[:16]


async def ensure_wechat_tables() -> None:
    global _tables_ready
    if _tables_ready:
        return
    async with _tables_lock:
        if _tables_ready:
            return

        def _create(sync_conn) -> None:
            Base.metadata.create_all(sync_conn, tables=[WechatMessage.__table__], checkfirst=True)

        async with engine.begin() as conn:
            await conn.run_sync(_create)
        _tables_ready = True


_MEDIA_TYPES = {"file", "image", "audio", "video", "voice", "pic", "picture", "img", "ptt"}


def parse_wechat_appmsg(raw: str | None) -> dict[str, Any]:
    text = str(raw or "").strip()
    if "<appmsg" not in text:
        return {}
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return {}
    appmsg = root.find(".//appmsg")
    if appmsg is None:
        return {}
    app_type = (appmsg.findtext("type") or "").strip()
    title = (appmsg.findtext("title") or "").strip()
    fileext = (appmsg.findtext("appattach/fileext") or "").strip()
    if app_type == "57":
        return {"kind": "reply", "title": title}
    if app_type == "6" or fileext:
        name = title
        if fileext and name and not name.lower().endswith(f".{fileext.lower()}"):
            name = f"{name}.{fileext}"
        return {"kind": "file", "name": name, "ext": fileext}
    return {"kind": "other", "title": title, "app_type": app_type}


def extract_file_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    file_obj = payload.get("file") if isinstance(payload.get("file"), dict) else {}
    merged = dict(file_obj)
    msg_type = str(payload.get("type") or "").strip().lower()
    text = str(payload.get("text") or "").strip()
    parsed = parse_wechat_appmsg(str(payload.get("rawContent") or ""))
    if parsed.get("kind") == "reply":
        return None
    if parsed.get("kind") == "file" and parsed.get("name"):
        merged["name"] = parsed["name"]
    if not merged.get("name"):
        candidate = payload.get("fileName") or payload.get("file_name") or text
        if candidate and Path(str(candidate)).suffix:
            merged["name"] = candidate
    if not merged.get("url") and payload.get("url"):
        merged["url"] = payload.get("url")
    if not merged.get("path") and payload.get("path"):
        merged["path"] = payload.get("path")
    has_name = bool(merged.get("name") and Path(str(merged.get("name"))).suffix)
    if (
        merged.get("base64")
        or merged.get("url")
        or merged.get("path")
        or payload.get("hasFile")
        or has_name
        or msg_type in {"image", "pic", "picture", "img", "audio", "video", "voice", "ptt"}
    ):
        if not merged.get("name") and msg_type in {"image", "pic", "picture", "img"}:
            merged["name"] = f"{payload.get('id') or 'image'}.jpg"
        cleaned = {key: value for key, value in merged.items() if value not in {None, ""}}
        return cleaned or None
    return None


def _guess_mime(data: bytes, filename: str | None, declared: str | None) -> str | None:
    if declared:
        return str(declared)
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"GIF8"):
        return "image/gif"
    if data.startswith(b"RIFF") and b"WEBP" in data[:16]:
        return "image/webp"
    suffix = Path(filename or "").suffix.lower()
    return {
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xls": "application/vnd.ms-excel",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".doc": "application/msword",
        ".pdf": "application/pdf",
        ".mp3": "audio/mpeg",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }.get(suffix)


def _write_wechat_file_bytes(data: bytes, file_payload: dict[str, Any], message_id: uuid.UUID) -> dict[str, Any]:
    original_name = str(file_payload.get("name") or file_payload.get("fileName") or "wechat-file")
    stored_name = f"{message_id}{_safe_suffix(original_name)}"
    path = _media_dir() / stored_name
    path.write_bytes(data)
    return {
        "storage": "local",
        "path": str(path),
        "size": len(data),
        "name": original_name,
        "mime": file_payload.get("mimeType") or file_payload.get("mime"),
    }


async def store_wechat_file(
    file_payload: dict[str, Any],
    message_id: uuid.UUID,
    payload: dict[str, Any] | None = None,
    retries: int = 4,
) -> dict[str, Any]:
    raw_b64 = file_payload.get("base64")
    if raw_b64:
        try:
            data = base64.b64decode(str(raw_b64), validate=False)
        except Exception as exc:
            return {"error": f"не удалось декодировать base64: {exc}"}
        return _write_wechat_file_bytes(data, file_payload, message_id)

    from app.services.wechat_utility_connect import download_wechat_media_candidates

    data, error, download_log, source_url = await download_wechat_media_candidates(
        file_payload, payload, retries=retries
    )
    if error or data is None:
        return {
            "error": error or "не удалось скачать файл",
            "source_url": source_url,
            "download_log": download_log,
            "name": file_payload.get("name"),
            "mime": file_payload.get("mimeType") or file_payload.get("mime"),
        }

    if not file_payload.get("mimeType") and not file_payload.get("mime"):
        file_payload = {
            **file_payload,
            "mimeType": _guess_mime(data, str(file_payload.get("name") or ""), None),
        }
    result = _write_wechat_file_bytes(data, file_payload, message_id)
    result["source_url"] = source_url
    result["download_log"] = download_log
    return result


def is_wechat_chat_payload(payload: dict[str, Any]) -> bool:
    event = str(payload.get("event") or payload.get("type") or "").strip().lower()
    if event in {"hello", "error", "ping", "pong", "media-download"}:
        return False
    if event in {"message", "chat", "wechat", "text", "image", "video", "voice", "audio", "file"}:
        return True
    return bool(
        payload.get("text")
        or payload.get("sender")
        or payload.get("file")
        or payload.get("group")
        or payload.get("hasFile")
        or payload.get("fileName")
    )


def _apply_file_meta(
    row: WechatMessage,
    file_meta: dict[str, Any],
    payload: dict[str, Any],
    file_payload: dict[str, Any] | None,
) -> None:
    row.has_file = True
    row.file_name = file_meta.get("name") or (file_payload or {}).get("name") or row.file_name
    row.file_mime = file_meta.get("mime") or (file_payload or {}).get("mimeType") or row.file_mime
    row.file_size = file_meta.get("size") or (file_payload or {}).get("size") or row.file_size
    row.file_kind = _file_kind(payload.get("type") or row.message_type, row.file_mime)
    row.file_storage = file_meta.get("storage") or row.file_storage
    row.file_storage_path = file_meta.get("path") or row.file_storage_path
    row.file_error = file_meta.get("error")


async def persist_wechat_payload(payload: dict[str, Any]) -> tuple[WechatMessage | None, dict[str, Any] | None]:
    if not is_wechat_chat_payload(payload):
        return None, None

    await ensure_wechat_tables()
    now = datetime.now(timezone.utc)
    external_id = str(payload.get("id") or "").strip() or None
    file_payload = extract_file_payload(payload)

    existing_id: uuid.UUID | None = None
    existing_has_storage = False
    async with AsyncSessionLocal() as db:
        if external_id:
            existing = await db.scalar(select(WechatMessage).where(WechatMessage.external_id == external_id))
            if existing is not None:
                existing_id = existing.id
                existing_has_storage = bool(existing.file_storage_path)

    message_id = existing_id if existing_id is not None else uuid.uuid4()
    file_meta: dict[str, Any] = {}
    needs_download = bool(file_payload) and not existing_has_storage
    if needs_download and file_payload:
        file_meta = await store_wechat_file(file_payload, message_id, payload)
        if file_meta.get("error"):
            print(f"[WeChat media] {file_meta['error']}", flush=True)

    download_log = file_meta.get("download_log") if isinstance(file_meta.get("download_log"), dict) else None

    async with AsyncSessionLocal() as db:
        if existing_id is not None:
            row = await db.get(WechatMessage, existing_id)
            if row is None:
                return None, download_log
            if file_meta:
                _apply_file_meta(row, file_meta, payload, file_payload)
                await db.commit()
                await db.refresh(row)
            return row, download_log

        row = WechatMessage(
            id=message_id,
            external_id=external_id,
            received_at=now,
            message_time=_parse_time(payload.get("time")),
            text=str(payload.get("text") or "") or None,
            sender=str(payload.get("sender") or "") or None,
            sender_id=str(payload.get("senderId") or "") or None,
            group_name=str(payload.get("group") or "") or None,
            group_id=str(payload.get("groupId") or "") or None,
            message_type=str(payload.get("type") or "") or None,
            has_file=bool(file_payload),
            file_name=file_meta.get("name") or (file_payload or {}).get("name"),
            file_mime=file_meta.get("mime") or (file_payload or {}).get("mimeType"),
            file_size=file_meta.get("size") or (file_payload or {}).get("size"),
            file_kind=_file_kind(payload.get("type"), file_meta.get("mime") or (file_payload or {}).get("mimeType")),
            file_storage=file_meta.get("storage"),
            file_storage_path=file_meta.get("path"),
            file_error=file_meta.get("error"),
        )
        db.add(row)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            return None, download_log
        await db.refresh(row)
        return row, download_log


async def backfill_missing_wechat_files() -> int:
    """Докачивает вложения у уже сохранённых сообщений type=file/image без файла на диске."""
    await ensure_wechat_tables()
    async with AsyncSessionLocal() as db:
        rows = (
            await db.scalars(
                select(WechatMessage)
                .where(
                    WechatMessage.message_type.in_(tuple(_MEDIA_TYPES)),
                    WechatMessage.file_storage_path.is_(None),
                )
                .order_by(WechatMessage.received_at.desc())
                .limit(20)
            )
        ).all()
        pending = [
            {
                "id": row.id,
                "external_id": row.external_id,
                "type": row.message_type,
                "text": row.text,
                "file_name": row.file_name,
            }
            for row in rows
        ]

    saved = 0
    for item in pending:
        payload = {
            "id": item["external_id"],
            "type": item["type"],
            "text": item["text"],
            "fileName": item["file_name"] or item["text"],
            "hasFile": True,
        }
        file_payload = extract_file_payload(payload)
        if not file_payload:
            continue
        file_meta = await store_wechat_file(file_payload, item["id"], payload, retries=1)
        if not file_meta.get("path"):
            continue
        async with AsyncSessionLocal() as db:
            row = await db.get(WechatMessage, item["id"])
            if row is None:
                continue
            _apply_file_meta(row, file_meta, payload, file_payload)
            await db.commit()
            saved += 1
    if pending:
        print(f"[WeChat media] backfill: {saved}/{len(pending)} файлов докачано", flush=True)
    return saved


def serialize_wechat_message(row: WechatMessage) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "externalId": row.external_id,
        "receivedAt": row.received_at.isoformat() if row.received_at else None,
        "time": row.message_time.isoformat() if row.message_time else None,
        "text": row.text,
        "sender": row.sender,
        "senderId": row.sender_id,
        "group": row.group_name,
        "groupId": row.group_id,
        "type": row.message_type,
        "hasFile": bool(row.has_file and row.file_storage_path),
        "file": (
            {
                "name": row.file_name,
                "kind": row.file_kind,
                "mimeType": row.file_mime,
                "size": row.file_size,
                "storage": row.file_storage,
                "path": row.file_storage_path,
                "error": row.file_error,
            }
            if row.has_file or row.file_name or row.file_error
            else None
        ),
    }


async def list_wechat_messages(limit: int = 500) -> list[dict[str, Any]]:
    await ensure_wechat_tables()
    async with AsyncSessionLocal() as db:
        rows = (
            await db.scalars(
                select(WechatMessage)
                .order_by(WechatMessage.message_time.asc().nulls_last(), WechatMessage.received_at.asc())
                .limit(max(1, min(limit, 2000)))
            )
        ).all()
    return [serialize_wechat_message(row) for row in rows]


def _group_key(group_id: str | None, group_name: str | None) -> str | None:
    value = (group_id or "").strip() or (group_name or "").strip()
    return value or None


async def list_wechat_groups() -> list[dict[str, Any]]:
    items = await list_wechat_messages(limit=2000)
    groups: dict[str, dict[str, Any]] = {}
    for item in items:
        key = _group_key(item.get("groupId"), item.get("group"))
        group_id = str(item.get("groupId") or "")
        group_name = str(item.get("group") or "")
        is_named_group = bool(group_name) and not group_name.startswith("wxid_")
        is_chatroom = "@chatroom" in group_id
        if not key or not (is_named_group or is_chatroom):
            continue
        current = groups.get(key)
        last_at = item.get("time") or item.get("receivedAt")
        preview = (item.get("text") or "").strip() or (
            f"файл: {item['file']['name']}" if item.get("file") and item["file"].get("name") else "вложение"
        )
        if current is None:
            groups[key] = {
                "id": key,
                "groupId": item.get("groupId"),
                "name": item.get("group") or key,
                "messageCount": 1,
                "lastMessageAt": last_at,
                "lastSender": item.get("sender"),
                "lastPreview": preview,
            }
            continue
        current["messageCount"] += 1
        if last_at and (not current["lastMessageAt"] or last_at >= current["lastMessageAt"]):
            current["lastMessageAt"] = last_at
            current["lastSender"] = item.get("sender")
            current["lastPreview"] = preview
            current["name"] = item.get("group") or current["name"]
            current["groupId"] = item.get("groupId") or current["groupId"]
    return sorted(
        groups.values(),
        key=lambda row: row.get("lastMessageAt") or "",
        reverse=True,
    )


async def list_wechat_group_messages(group_id: str | None = None, group_name: str | None = None) -> list[dict[str, Any]]:
    wanted_id = (group_id or "").strip()
    wanted_name = (group_name or "").strip()
    if not wanted_id and not wanted_name:
        return []
    items = await list_wechat_messages(limit=2000)
    return [
        item
        for item in items
        if (wanted_id and item.get("groupId") == wanted_id)
        or (not wanted_id and wanted_name and item.get("group") == wanted_name)
    ]


def resolve_wechat_file_path(storage_path: str | None) -> Path | None:
    if not storage_path:
        return None
    path = Path(storage_path)
    if not path.is_file():
        return None
    media_root = _media_dir().resolve()
    try:
        path.resolve().relative_to(media_root)
    except ValueError:
        return None
    return path
