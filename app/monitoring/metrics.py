from __future__ import annotations
from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

def setup_monitoring(app: FastAPI) -> None:
    Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
