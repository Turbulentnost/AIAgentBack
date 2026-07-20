"""Управление шумным логированием exchangelib (naive datetime, Free/Busy и т.д.)."""

from __future__ import annotations

import logging

_EWS_LOGGER_NAMES = (
    "exchangelib",
    "exchangelib.fields",
    "exchangelib.util",
    "exchangelib.ewsdatetime",
    "exchangelib.services",
)

_EWS_NOISE_FRAGMENTS = (
    "Found naive datetime",
    "Assuming timezone",
)


class _EwsNoiseFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return not any(fragment in message for fragment in _EWS_NOISE_FRAGMENTS)


_ews_noise_filter = _EwsNoiseFilter()
_configured = False


def configure_exchangelib_logging(*, verbose: bool = False) -> None:
    """verbose=True — только при явном поиске слота (кнопка «Запустить агента»)."""
    global _configured
    level = logging.WARNING if verbose else logging.ERROR
    for name in _EWS_LOGGER_NAMES:
        ews_logger = logging.getLogger(name)
        ews_logger.setLevel(level)
        if _ews_noise_filter not in ews_logger.filters:
            ews_logger.addFilter(_ews_noise_filter)
    _configured = True


def ensure_exchangelib_logging_quiet() -> None:
    if not _configured:
        configure_exchangelib_logging(verbose=False)
