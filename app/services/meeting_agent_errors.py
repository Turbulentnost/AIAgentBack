from __future__ import annotations

import re


def format_onec_load_error(exc: BaseException) -> str:
    raw = _compact(str(exc))
    lowered = raw.lower()

    if "401" in lowered or "403" in lowered or "unauthorized" in lowered:
        return (
            "Нет доступа к 1С OData (ошибка авторизации). "
            "Проверьте ONEC_ODATA_USER / ONEC_ODATA_PASSWORD."
        )
    if "404" in lowered or "not found" in lowered:
        return "Документ служебной записки не найден в 1С или недоступен по OData."
    if any(token in lowered for token in ("timeout", "timed out", "connection", "connect")):
        return "Не удалось подключиться к 1С OData. Проверьте ONEC_ODATA_URL и доступность сервера."
    if raw:
        return f"Не удалось загрузить карточку служебной записки из 1С: {raw}"
    return "Не удалось загрузить карточку служебной записки из 1С."


def format_participants_missing_error() -> str:
    return (
        "В служебной записке не указаны участники, инициатор или руководитель. "
        "Заполните заявку в 1С и нажмите «Обновить» на dashboard."
    )


def format_missing_emails_error(missing_fios: list[str]) -> str:
    names = ", ".join(missing_fios)
    return (
        f"Не найден корпоративный e-mail (@turbo-don.ru) для: {names}. "
        "Проверьте ФИО и наличие адреса в адресной книге Exchange."
    )


def format_email_lookup_error(exc: BaseException) -> str:
    network_error = _format_exchange_network_error(exc)
    if network_error:
        return network_error
    raw = _compact(str(exc))
    if raw:
        return f"Не удалось получить e-mail участников из Exchange. Причина: {raw}"
    return "Не удалось получить e-mail участников из Exchange. Проверьте OUTLOOK_* и GAL."


def format_calendar_error(exc: BaseException) -> str:
    network_error = _format_exchange_network_error(exc)
    if network_error:
        return network_error
    raw = _compact(str(exc))
    lowered = raw.lower()

    if "exchange не вернул занятость" in lowered or "freebusyview" in lowered:
        return (
            "Exchange не вернул занятость одного из участников. "
            "Проверьте e-mail в Exchange GAL и права Postagent на чтение календарей."
        )
    if any(token in lowered for token in ("timeout", "timed out")):
        return "Exchange/Outlook не ответил вовремя при проверке календарей. Попробуйте ещё раз."
    if any(token in lowered for token in ("401", "403", "unauthorized", "forbidden")):
        return "Нет доступа к календарям Exchange (EWS). Проверьте OUTLOOK_EMAIL / OUTLOOK_PASSWORD."
    if raw:
        return f"Не удалось проверить занятость календарей участников: {raw}"
    return "Не удалось проверить занятость календарей участников."


def format_no_slot_error(*, max_days: int = 7) -> str:
    return (
        f"Свободный общий слот для всех участников не найден в ближайшие {max_days} дн. "
        "Измените желаемое время в СЗ или проверьте календари вручную."
    )


def format_slot_preview_timeout_error(*, timeout_seconds: int) -> str:
    return (
        f"Проверка календарей Exchange не завершилась за {timeout_seconds} с. "
        "Попробуйте ещё раз или сократите список участников."
    )


def _compact(text: str, *, limit: int = 240) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"


def _format_exchange_network_error(exc: BaseException) -> str | None:
    raw = _compact(str(exc))
    lowered = raw.lower()
    if any(
        token in lowered
        for token in (
            "nameresolutionerror",
            "failed to resolve",
            "getaddrinfo failed",
            "name or service not known",
            "nodename nor servname provided",
            "temporary failure in name resolution",
            "no address associated with hostname",
        )
    ):
        return (
            "Не удалось подключиться к Exchange (mail.turbo-don.ru): имя сервера не разрешается DNS. "
            "Подключитесь к корпоративной сети или VPN и проверьте OUTLOOK_SERVER в .env."
        )
    if any(token in lowered for token in ("connection refused", "network is unreachable")):
        return (
            "Exchange (mail.turbo-don.ru) недоступен по сети. "
            "Проверьте VPN/корпоративную сеть и OUTLOOK_SERVER в .env."
        )
    return None
