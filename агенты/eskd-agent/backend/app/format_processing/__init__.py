"""Постобработка форматов КД после загрузки."""

from app.format_processing.pipeline import process_bytes, process_uploads

__all__ = ["process_bytes", "process_uploads"]
