from __future__ import annotations

import asyncio
import contextlib
import json
import uuid

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status
from redis.asyncio import Redis
from sqlalchemy import select

from app.api.deps import authenticate_access_token
from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.enums import KnowledgeBaseAccessType
from app.models.knowledge_base import KnowledgeBaseIndexingJob
from app.services.knowledge_base_access_service import KnowledgeBaseAccessService
from app.services.knowledge_base_indexing_events import (
    build_indexing_payload,
    indexing_channel,
    is_indexing_active,
)
from app.services.knowledge_base_service import KnowledgeBaseService

router = APIRouter(prefix="/knowledge-bases", tags=["knowledge-bases"])


@router.websocket("/{knowledge_base_id}/index/ws")
async def knowledge_base_indexing_ws(
    websocket: WebSocket,
    knowledge_base_id: uuid.UUID,
    token: str = Query(...),
) -> None:
    await websocket.accept()

    async with AsyncSessionLocal() as db:
        try:
            user = await authenticate_access_token(db, token)
        except Exception:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        kb = await KnowledgeBaseService(db).get(knowledge_base_id)
        if kb is None:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        access = await KnowledgeBaseAccessService(db).can_access_knowledge_base(
            user=user,
            knowledge_base=kb,
            required_access=KnowledgeBaseAccessType.READ,
        )
        if not access.allowed:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        job = await db.scalar(
            select(KnowledgeBaseIndexingJob)
            .where(KnowledgeBaseIndexingJob.knowledge_base_id == knowledge_base_id)
            .order_by(KnowledgeBaseIndexingJob.created_at.desc())
            .limit(1)
        )
        await websocket.send_json(
            build_indexing_payload(
                event="snapshot",
                knowledge_base=kb,
                job=job,
                indexing_active=is_indexing_active(kb, job),
            )
        )

    redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    pubsub = redis.pubsub()
    await pubsub.subscribe(indexing_channel(knowledge_base_id))
    listener = asyncio.create_task(_forward_pubsub(pubsub, websocket))
    try:
        while True:
            message = await websocket.receive_text()
            if message == "ping":
                await websocket.send_json({"event": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        listener.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await listener
        await pubsub.unsubscribe(indexing_channel(knowledge_base_id))
        await pubsub.aclose()
        await redis.aclose()


async def _forward_pubsub(pubsub, websocket: WebSocket) -> None:
    async for message in pubsub.listen():
        if message.get("type") != "message":
            continue
        data = message.get("data")
        if not data:
            continue
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            payload = {"event": "progress", "raw": data}
        await websocket.send_json(payload)
