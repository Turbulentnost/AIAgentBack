"""API-шлюз для фронтенда ESKD Agent — проксирует запросы к GPU model-сервису."""

from __future__ import annotations

import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse

from app.api.check import router as check_router
from app.api.eskd_deps import OptionalEskdActor, get_optional_eskd_actor
from app.api.gost import router as gost_router
from app.api.history import router as history_router
from app.api.integration.v1 import (
    admin_router,
    auth_router,
    checks_router,
    erp_router,
    meta_router,
    sed_router,
    webhooks_router,
)
from app.api.knowledge_base import router as knowledge_base_router
from app.api.marking import router as marking_router
from app.api.users import router as users_router
from app.integration.queue_worker import IntegrationQueueWorker
from app.config import settings
from app.db.session import SessionLocal, init_db
from app.format_processing import process_uploads
from app.gost.aggregation import aggregate_from_check_response
from app.services.history_service import persist_check_run_safe, persist_check_uploads
from app.services.history_stream_service import handle_stream_history_event, new_stream_state
from app.services.marking_check_cache import MarkingCheckCacheService
from app.services.model_health import build_llm_status, build_vlm_status, check_llm_health
from app.services.user_service import UserService

_CACHED_CHECK_STATUSES = frozenset({"from_marking", "from_cache"})

_log = logging.getLogger("eskd.backend")

_worker = IntegrationQueueWorker()

SUPPORTED_FORMATS = [
    "docx", "xlsx", "xml", "pdf", "spw", "dxf", "dwg", "cdw",
    "png", "jpg", "jpeg", "zip",
]


@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        await init_db()
        async with SessionLocal() as db:
            await UserService(db).ensure_seed_users()
        _log.info("database initialized")
    except Exception as exc:
        _log.error("database init failed: %s", exc)
    await _worker.start()
    yield
    await _worker.stop()


def create_app() -> FastAPI:
    app = FastAPI(
        title="ESKD Agent API",
        description="Шлюз для проверки конструкторской документации по ЕСКД (Gemma-3n + LoRA)",
        version="1.2.0",
        lifespan=lifespan,
    )

    origins = settings.cors_origin_list or ["*"]
    allow_credentials = "*" not in origins

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(gost_router)
    app.include_router(check_router)
    app.include_router(history_router)
    app.include_router(users_router)
    app.include_router(marking_router)
    app.include_router(knowledge_base_router)
    app.include_router(checks_router)
    app.include_router(meta_router)
    app.include_router(admin_router)
    app.include_router(webhooks_router)
    app.include_router(erp_router)
    app.include_router(sed_router)
    app.include_router(auth_router)

    @app.get("/health")
    async def health() -> dict:
        model_payload: dict = {"reachable": False}
        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{settings.model_service_url.rstrip('/')}/health")
                resp.raise_for_status()
                ping_ms = round((time.perf_counter() - t0) * 1000, 1)
                model_payload = {"reachable": True, "ping_ms": ping_ms, **resp.json()}
        except Exception as exc:
            model_payload["ping_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            model_payload["error"] = str(exc)

        vlm_status = build_vlm_status(
            model_payload,
            ping_ms=float(model_payload.get("ping_ms") or 0),
            reachable=bool(model_payload.get("reachable")),
            service_url=settings.model_service_url,
        )
        llm_status = build_llm_status(model_payload, await check_llm_health())

        vlm_ready = vlm_status.get("reachable") and vlm_status.get("model_loaded")
        llm_required = bool(llm_status.get("required"))
        llm_ready = not llm_required or (llm_status.get("reachable") and llm_status.get("configured"))
        overall_ok = vlm_ready and llm_ready

        # Backward compatibility: `model` mirrors VLM status for older UI code.
        model_compat = {**vlm_status}

        return {
            "status": "ok" if overall_ok else "degraded",
            "gateway": "eskd-backend",
            "pipeline_mode": model_payload.get("pipeline_mode") or settings.eskd_pipeline_mode,
            "vlm": vlm_status,
            "llm": llm_status,
            "model": model_compat,
            "integration": {
                "worker_enabled": settings.integration_worker_enabled,
                "api_prefix": "/api/v1/checks",
            },
        }

    @app.get("/api/v1/info")
    async def info() -> dict:
        return {
            "agent": "eskd",
            "name": "Агент проверки КД по ЕСКД",
            "capabilities": SUPPORTED_FORMATS + ["streaming", "preprocess", "history", "marking", "stats", "knowledge_base"],
            "model_service": settings.model_service_url,
            "preprocess": {
                "text": ["docx", "xlsx", "xml", "spw"],
                "drawing_png": ["pdf", "dxf", "dwg", "cdw", "png", "jpg"],
                "external": {
                    "ODA_CONVERTER_PATH": "DWG → DXF → PNG",
                    "KOMPAS_EXPORT_CMD": "CDW → PNG/PDF/DXF (шаблон: {input} {png})",
                },
            },
        }

    async def _forward_multipart(
        path: str,
        *,
        files: list[tuple[str, tuple[str, bytes, str | None]]],
        data: dict[str, str],
        stream: bool = False,
    ) -> httpx.Response | AsyncIterator[bytes]:
        url = f"{settings.model_service_url.rstrip('/')}{path}"
        # SSE от model-сервиса: между событиями VLM может молчать 5–10 мин на лист.
        timeout = (
            httpx.Timeout(connect=30.0, read=None, write=settings.request_timeout_sec, pool=settings.request_timeout_sec)
            if stream
            else httpx.Timeout(settings.request_timeout_sec, connect=30.0)
        )
        client = httpx.AsyncClient(timeout=timeout)
        try:
            req = client.build_request("POST", url, files=files, data=data)
            resp = await client.send(req, stream=stream)
            if stream:

                async def _iter() -> AsyncIterator[bytes]:
                    try:
                        async for chunk in resp.aiter_bytes():
                            yield chunk
                    finally:
                        await resp.aclose()
                        await client.aclose()

                return _iter()
            body = await resp.aread()
            await client.aclose()
            return httpx.Response(status_code=resp.status_code, content=body, headers=resp.headers)
        except httpx.ConnectError as exc:
            await client.aclose()
            raise HTTPException(503, f"Model service недоступен: {exc}") from exc
        except httpx.TimeoutException as exc:
            await client.aclose()
            raise HTTPException(504, "Model service timeout") from exc

    def _check_upload_size(files: list[UploadFile]) -> None:
        limit = settings.max_upload_mb * 1024 * 1024
        total = 0
        for f in files:
            if f.size is not None:
                total += f.size
        if total > limit:
            raise HTTPException(413, f"Суммарный размер файлов > {settings.max_upload_mb} MB")

    async def _raw_uploads(upload_files: list[UploadFile]) -> list[tuple[str, bytes]]:
        out: list[tuple[str, bytes]] = []
        for uf in upload_files:
            out.append((uf.filename or "upload.bin", await uf.read()))
        return out

    async def _prepare_uploads_raw(
        raw: list[tuple[str, bytes]],
    ) -> tuple[
        list[tuple[str, tuple[str, bytes, str | None]]],
        list[dict],
        list[str],
        list[tuple[str, bytes]],
    ]:
        try:
            model_files, extracted, warnings = process_uploads(raw)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        multipart = [
            ("files", (name, data, mime))
            for name, data, mime in model_files
        ]
        return multipart, extracted, warnings, raw

    async def _prepare_uploads(upload_files: list[UploadFile]) -> tuple[
        list[tuple[str, tuple[str, bytes, str | None]]],
        list[dict],
        list[str],
        list[tuple[str, bytes]],
    ]:
        raw = await _raw_uploads(upload_files)
        return await _prepare_uploads_raw(raw)

    def _form_data(
        *,
        designation: str | None,
        all_pages: str | None,
        pages: str | None,
        page: int | None,
        page_from: int | None,
        page_to: int | None,
        extracted: list[dict] | None = None,
    ) -> dict[str, str]:
        data: dict[str, str] = {
            "all_pages": all_pages or "true",
            "pipeline_mode": settings.eskd_pipeline_mode,
        }
        if designation:
            data["designation"] = designation
        if pages:
            data["pages"] = pages
        if page is not None:
            data["page"] = str(page)
        if page_from is not None:
            data["page_from"] = str(page_from)
        if page_to is not None:
            data["page_to"] = str(page_to)
        if extracted:
            data["extracted_texts"] = json.dumps(extracted, ensure_ascii=False)
        return data

    def _check_params(
        *,
        designation: str | None,
        all_pages: str | None,
        pages: str | None,
        page: int | None,
        page_from: int | None,
        page_to: int | None,
    ) -> dict:
        return {
            "designation": designation,
            "all_pages": all_pages,
            "pages": pages,
            "page": page,
            "page_from": page_from,
            "page_to": page_to,
        }

    def _merge_check_payload(payload: dict, *, extracted: list[dict], warnings: list[str]) -> dict:
        if extracted:
            payload["extracted_texts"] = extracted
        if warnings:
            payload["preprocess_warnings"] = warnings
        payload["gost_summary"] = aggregate_from_check_response(payload)
        return payload

    async def _persist_payload(
        payload: dict,
        *,
        raw_uploads: list[tuple[str, bytes]],
        check_params: dict,
        actor: OptionalEskdActor | None = None,
        existing_run_id: uuid.UUID | None = None,
    ) -> dict:
        if payload.get("status") in _CACHED_CHECK_STATUSES:
            return payload
        async with SessionLocal() as db:
            run_id = await persist_check_run_safe(
                db,
                payload=payload,
                uploads=raw_uploads,
                check_params=check_params,
                actor=actor.actor if actor else None,
                existing_run_id=existing_run_id,
            )
        if run_id:
            payload["history_run_id"] = str(run_id)
        else:
            payload.setdefault("history_warnings", []).append("Не удалось сохранить в историю")
        return payload

    async def _try_cached_check(
        *,
        raw_uploads: list[tuple[str, bytes]],
        designation: str | None,
    ) -> dict | None:
        async with SessionLocal() as db:
            return await MarkingCheckCacheService(db).try_build_cached(
                uploads=raw_uploads,
                designation=designation,
            )

    def _text_only_response(extracted: list[dict], warnings: list[str]) -> dict:
        return {
            "job_id": str(uuid.uuid4()),
            "status": "text_only",
            "designation": None,
            "model": "",
            "adapter": "",
            "total_items": 0,
            "processed": 0,
            "failed": 0,
            "total_errors": 0,
            "total_warnings": 0,
            "total_infer_seconds": 0.0,
            "load_seconds": 0.0,
            "progress_percent": 100.0,
            "global_warnings": warnings,
            "items": [],
            "report_text": "",
            "extracted_texts": extracted,
            "preprocess_warnings": warnings,
            "summary": "Только текстовые документы — vision-проверка не выполнялась",
        }

    @app.post("/api/v1/eskd/preprocess")
    async def eskd_preprocess(files: list[UploadFile] = File(...)):
        """Конвертация без вызова модели — для отладки форматов."""
        _check_upload_size(files)
        _, extracted, warnings, _ = await _prepare_uploads(files)
        return {
            "extracted_texts": extracted,
            "warnings": warnings,
            "vision_files": len([e for e in extracted if e.get("format") in {"pdf", "dxf", "dwg", "cdw", "image"}]),
        }

    @app.post("/api/v1/eskd/check")
    async def eskd_check(
        files: list[UploadFile] = File(...),
        designation: str | None = Form(default=None),
        all_pages: str | None = Form(default="true"),
        pages: str | None = Form(default=None),
        page: int | None = Form(default=None),
        page_from: int | None = Form(default=None),
        page_to: int | None = Form(default=None),
        actor_ctx: OptionalEskdActor = Depends(get_optional_eskd_actor),
    ):
        _check_upload_size(files)
        raw_uploads = await _raw_uploads(files)
        persist_check_uploads(raw_uploads)
        cached = await _try_cached_check(raw_uploads=raw_uploads, designation=designation)
        if cached:
            return JSONResponse(_merge_check_payload(cached, extracted=[], warnings=[]))

        multipart, extracted, warnings, _ = await _prepare_uploads(files)
        params = _check_params(
            designation=designation,
            all_pages=all_pages,
            pages=pages,
            page=page,
            page_from=page_from,
            page_to=page_to,
        )
        data = _form_data(**params, extracted=extracted)

        if not multipart:
            return JSONResponse(_text_only_response(extracted, warnings))

        resp = await _forward_multipart("/api/v1/eskd/check", files=multipart, data=data, stream=False)
        assert isinstance(resp, httpx.Response)
        if resp.status_code >= 400:
            raise HTTPException(resp.status_code, resp.text)
        payload = _merge_check_payload(resp.json(), extracted=extracted, warnings=warnings)
        payload = await _persist_payload(
            payload,
            raw_uploads=raw_uploads,
            check_params=params,
            actor=actor_ctx,
        )
        return JSONResponse(content=payload, status_code=resp.status_code)

    @app.post("/api/v1/eskd/check/stream")
    async def eskd_check_stream(
        files: list[UploadFile] = File(...),
        designation: str | None = Form(default=None),
        all_pages: str | None = Form(default="true"),
        pages: str | None = Form(default=None),
        page: int | None = Form(default=None),
        page_from: int | None = Form(default=None),
        page_to: int | None = Form(default=None),
        actor_ctx: OptionalEskdActor = Depends(get_optional_eskd_actor),
    ):
        _check_upload_size(files)
        raw_uploads = await _raw_uploads(files)
        persist_check_uploads(raw_uploads)
        try:
            prepared = await _prepare_uploads_raw(raw_uploads)
        except HTTPException:
            raise
        multipart, extracted, warnings, stream_uploads = prepared
        params = _check_params(
            designation=designation,
            all_pages=all_pages,
            pages=pages,
            page=page,
            page_from=page_from,
            page_to=page_to,
        )
        data = _form_data(**params, extracted=extracted)

        async def _wrapped_stream() -> AsyncIterator[bytes]:
            cached = await _try_cached_check(raw_uploads=raw_uploads, designation=designation)
            if cached:
                prep = {
                    "from_cache": True,
                    "cached": True,
                    "from_marking": cached.get("status") == "from_marking",
                }
                yield f"event: preprocess\ndata: {json.dumps(prep, ensure_ascii=False)}\n\n".encode()
                done = _merge_check_payload(cached, extracted=[], warnings=[])
                yield f"event: complete\ndata: {json.dumps(done, ensure_ascii=False)}\n\n".encode()
                return

            prep = {
                "extracted_count": len(extracted),
                "vision_files": len(multipart),
                "warnings": warnings,
            }
            yield f"event: preprocess\ndata: {json.dumps(prep, ensure_ascii=False)}\n\n".encode()

            if not multipart:
                done = _text_only_response(extracted, warnings)
                yield f"event: complete\ndata: {json.dumps(done, ensure_ascii=False)}\n\n".encode()
                return

            stream = await _forward_multipart(
                "/api/v1/eskd/check/stream", files=multipart, data=data, stream=True
            )
            assert not isinstance(stream, httpx.Response)
            buffer = b""
            history_state = new_stream_state()
            actor = actor_ctx.actor if actor_ctx else None

            def _rewrite_sse_data(text: str, payload: dict) -> str:
                lines = text.split("\n")
                out: list[str] = []
                replaced = False
                for line in lines:
                    if line.startswith("data:") and not replaced:
                        out.append("data: " + json.dumps(payload, ensure_ascii=False))
                        replaced = True
                    else:
                        out.append(line)
                return "\n".join(out)

            async def _process_sse_block(text: str) -> str:
                payload: dict | None = None
                for line in text.split("\n"):
                    if line.startswith("data:"):
                        try:
                            payload = json.loads(line[5:].strip())
                        except json.JSONDecodeError:
                            payload = None
                if not isinstance(payload, dict):
                    return text

                event_type = payload.get("type")
                if event_type in {"start", "item", "progress"}:
                    async with SessionLocal() as db:
                        await handle_stream_history_event(
                            db,
                            payload,
                            state=history_state,
                            uploads=stream_uploads,
                            check_params=params,
                            actor=actor,
                        )
                    if event_type == "start" and history_state.get("run_id"):
                        payload = {**payload, "history_run_id": str(history_state["run_id"])}
                        text = _rewrite_sse_data(text, payload)

                if event_type == "complete":
                    payload = _merge_check_payload(payload, extracted=extracted, warnings=warnings)
                    existing_id = history_state.get("run_id")
                    payload = await _persist_payload(
                        payload,
                        raw_uploads=stream_uploads,
                        check_params=params,
                        actor=actor_ctx,
                        existing_run_id=existing_id,
                    )
                    text = _rewrite_sse_data(text, payload)
                return text

            async for chunk in stream:
                buffer += chunk
                while b"\n\n" in buffer:
                    block, buffer = buffer.split(b"\n\n", 1)
                    text = block.decode("utf-8", errors="replace")
                    text = await _process_sse_block(text)
                    yield (text + "\n\n").encode()
            if buffer.strip():
                text = buffer.decode("utf-8", errors="replace")
                text = await _process_sse_block(text)
                yield text.encode()

        return StreamingResponse(
            _wrapped_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/v1/eskd/check/cancel")
    async def eskd_check_cancel(job_id: str = Form(...)):
        timeout = httpx.Timeout(30.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{settings.model_service_url.rstrip('/')}/api/v1/eskd/check/cancel",
                data={"job_id": job_id},
            )
        if resp.status_code >= 400:
            raise HTTPException(resp.status_code, resp.text)
        return JSONResponse(content=resp.json())

    @app.post("/api/v1/eskd/check.txt")
    async def eskd_check_txt(
        files: list[UploadFile] = File(...),
        designation: str | None = Form(default=None),
        all_pages: str | None = Form(default="true"),
    ):
        _check_upload_size(files)
        multipart, extracted, warnings, _ = await _prepare_uploads(files)
        data: dict[str, str] = {"all_pages": all_pages or "true"}
        if designation:
            data["designation"] = designation

        header = ""
        if extracted:
            header = "\n\n".join(
                f"=== {t['source']} ({t['format']}) ===\n{t['text']}" for t in extracted
            ) + "\n\n"
        if warnings:
            header += "Предупреждения:\n" + "\n".join(f"- {w}" for w in warnings) + "\n\n"

        if not multipart:
            return PlainTextResponse(header or "Нет данных для проверки")

        resp = await _forward_multipart("/api/v1/eskd/check.txt", files=multipart, data=data, stream=False)
        assert isinstance(resp, httpx.Response)
        if resp.status_code >= 400:
            raise HTTPException(resp.status_code, resp.text)
        return PlainTextResponse(content=header + resp.text, status_code=resp.status_code)

    @app.post("/api/v1/eskd/pdf/info")
    async def pdf_info(file: UploadFile = File(...)):
        content = await file.read()
        multipart = [("file", (file.filename or "upload.pdf", content, file.content_type))]
        resp = await _forward_multipart("/api/v1/eskd/pdf/info", files=multipart, data={}, stream=False)
        assert isinstance(resp, httpx.Response)
        if resp.status_code >= 400:
            raise HTTPException(resp.status_code, resp.text)
        return JSONResponse(content=resp.json())

    return app


app = create_app()
