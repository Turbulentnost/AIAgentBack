from __future__ import annotations

import logging
import sys
import time as time_module
from contextlib import contextmanager
from typing import Any, Iterator

from app.tools.Outlook.ews_logging import configure_exchangelib_logging


logger = logging.getLogger("find_meeting_slot")

_timing_report: list[dict[str, Any]] = []

_run_started_at: float | None = None

def setup_logging(*, quiet: bool) -> None:
    # Не использовать logging.disable(): он глушит весь процесс (включая uvicorn/structlog).
    logging.disable(logging.NOTSET)
    configure_exchangelib_logging(verbose=not quiet)
    logger.propagate = False
    if quiet:
        logger.setLevel(logging.CRITICAL + 1)
        return
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            RelativeMsFormatter("%(levelname)s [+%(relative)7.0f ms] %(message)s")
        )
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)

class RelativeMsFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        global _run_started_at
        if _run_started_at is None:
            _run_started_at = time_module.perf_counter()
        record.relative = (time_module.perf_counter() - _run_started_at) * 1000  # type: ignore[attr-defined]
        return super().format(record)

def reset_timing_report() -> None:
    global _run_started_at
    _timing_report.clear()
    _run_started_at = time_module.perf_counter()


def get_timing_report() -> list[dict[str, Any]]:
    return list(_timing_report)

def record_timing(step: str, elapsed_ms: float, **details: Any) -> None:
    entry: dict[str, Any] = {"step": step, "elapsed_ms": round(elapsed_ms, 1)}
    entry.update(details)
    _timing_report.append(entry)

@contextmanager
def timed_step(step: str, **details: Any) -> Iterator[None]:
    started = time_module.perf_counter()
    detail_text = ", ".join(f"{key}={value}" for key, value in details.items())
    logger.info("→ %s%s", step, f" ({detail_text})" if detail_text else "")
    try:
        yield
    finally:
        elapsed_ms = (time_module.perf_counter() - started) * 1000
        record_timing(step, elapsed_ms, **details)
        logger.info("✓ %s: %.0f ms", step, elapsed_ms)

def log_timing_summary() -> None:
    if not _timing_report:
        return
    total_ms = sum(entry["elapsed_ms"] for entry in _timing_report)
    logger.info("--- сводка по времени (%.0f ms всего) ---", total_ms)
    for entry in _timing_report:
        share = (entry["elapsed_ms"] / total_ms * 100) if total_ms else 0.0
        detail_text = ", ".join(
            f"{key}={value}"
            for key, value in entry.items()
            if key not in {"step", "elapsed_ms"}
        )
        suffix = f" ({detail_text})" if detail_text else ""
        logger.info(
            "  %.0f ms (%5.1f%%) %s%s",
            entry["elapsed_ms"],
            share,
            entry["step"],
            suffix,
        )

