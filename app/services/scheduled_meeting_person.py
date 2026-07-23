from __future__ import annotations

import asyncio
import secrets
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.position import Position
from app.models.user import User
from app.services.enterprise_positions_report import (
    lookup_fios_by_position_title,
    lookup_positions_by_fio,
    normalize_position_title,
    primary_position_for_fio,
)
from app.services.position_service import PositionService
from app.services.scheduled_meeting_outlook import _is_invitable_attendee_email
from app.utils.department_classification import normalize_position_name

DIRECTORY_SOURCE_SYSTEM = "directory"
OUTLOOK_USER_ID_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


def outlook_user_id_for_email(email: str) -> uuid.UUID:
    return uuid.uuid5(OUTLOOK_USER_ID_NAMESPACE, email.strip().lower())


class ScheduledMeetingPersonError(Exception):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class ResolvedPerson:
    user_id: uuid.UUID
    fio: str
    email: str
    position_id: uuid.UUID | None = None
    position_name: str | None = None


@dataclass(frozen=True)
class EmployeeOption:
    id: uuid.UUID
    fio: str
    email: str
    position_name: str | None = None
    position_id: uuid.UUID | None = None


def _person_fio_from_dict(person: dict[str, Any] | None) -> str:
    if not isinstance(person, dict):
        return ""
    for key in ("full_name", "fio", "ФИО", "Description"):
        value = person.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _person_position_from_dict(person: dict[str, Any] | None) -> str:
    if not isinstance(person, dict):
        return ""
    value = person.get("position")
    return value.strip() if isinstance(value, str) else ""


async def _find_position_by_title(db: AsyncSession, title: str) -> Position | None:
    normalized_target = normalize_position_title(title)
    if not normalized_target:
        return None
    result = await db.execute(
        select(Position)
        .where(
            Position.is_active.is_(True),
            Position.normalized_name == normalized_target,
        )
        .limit(1)
    )
    exact = result.scalar_one_or_none()
    if exact is not None:
        return exact

    candidates = await PositionService(db).list(search=title, limit=20)
    exact_matches = [
        candidate
        for candidate in candidates
        if normalize_position_title(candidate.name) == normalized_target
    ]
    if len(exact_matches) == 1:
        return exact_matches[0]
    if len(exact_matches) > 1:
        return exact_matches[0]
    if len(candidates) == 1:
        return candidates[0]
    return None


async def resolve_position_id_for_title(
    db: AsyncSession,
    title: str | None,
) -> uuid.UUID | None:
    normalized = (title or "").strip()
    if not normalized:
        return None
    position = await _find_position_by_title(db, normalized)
    return position.id if position is not None else None


async def resolve_position_id_for_user(db: AsyncSession, user: User) -> uuid.UUID | None:
    title = (user.position or "").strip()
    if not title:
        fio = (user.full_name or "").strip()
        if fio:
            title = primary_position_for_fio(fio) or ""
    return await resolve_position_id_for_title(db, title)


async def resolve_position_id_for_person(
    db: AsyncSession,
    person: ResolvedPerson,
) -> uuid.UUID | None:
    if person.position_id is not None:
        return person.position_id
    title = (person.position_name or "").strip() or primary_position_for_fio(person.fio) or ""
    return await resolve_position_id_for_title(db, title)


async def _invitable_email_for_user(user: User) -> str | None:
    email = (user.email or "").strip()
    if email and _is_invitable_attendee_email(email):
        return email

    fio = (user.full_name or "").strip()
    if not fio:
        return None

    gal_match = await _resolve_gal_person(fio)
    if gal_match is None:
        return None
    _resolved_fio, gal_email = gal_match
    return gal_email if _is_invitable_attendee_email(gal_email) else None


async def resolve_user_by_id(db: AsyncSession, user_id: uuid.UUID) -> User:
    user = await db.get(User, user_id)
    if user is None or user.deleted_at is not None or not user.is_active:
        raise ScheduledMeetingPersonError(
            f"Сотрудник не найден: {user_id}",
            status_code=404,
        )
    email = await _invitable_email_for_user(user)
    if not email:
        raise ScheduledMeetingPersonError(
            f"У сотрудника «{user.full_name or user_id}» нет корпоративного e-mail",
            status_code=400,
        )
    return user


async def _list_users_by_fio(db: AsyncSession, fio: str) -> list[User]:
    normalized = fio.strip()
    if not normalized:
        return []

    result = await db.execute(
        select(User).where(
            User.deleted_at.is_(None),
            User.is_active.is_(True),
            User.email.is_not(None),
            User.full_name.ilike(normalized),
        )
    )
    exact = list(result.scalars().all())
    if exact:
        return exact

    parts = normalized.split()
    if len(parts) >= 2:
        surname = parts[0]
        result = await db.execute(
            select(User).where(
                User.deleted_at.is_(None),
                User.is_active.is_(True),
                User.email.is_not(None),
                User.full_name.ilike(f"{surname}%"),
            )
        )
        candidates = list(result.scalars().all())
        narrowed = [
            user
            for user in candidates
            if (user.full_name or "").strip().casefold() == normalized.casefold()
        ]
        if narrowed:
            return narrowed
        if len(candidates) == 1:
            return candidates
    return []


async def _resolve_user_by_fio(db: AsyncSession, fio: str) -> User | None:
    users = await _list_users_by_fio(db, fio)
    if len(users) == 1:
        return users[0]
    if len(users) > 1:
        raise ScheduledMeetingPersonError(
            f"Неоднозначное ФИО «{fio}»: найдено {len(users)} сотрудников",
            status_code=400,
        )
    return None


async def _resolve_user_by_email(db: AsyncSession, email: str) -> User | None:
    normalized = email.strip().lower()
    if not normalized:
        return None
    result = await db.execute(
        select(User).where(
            User.deleted_at.is_(None),
            User.is_active.is_(True),
            User.email.ilike(normalized),
        )
    )
    users = list(result.scalars().all())
    if len(users) == 1:
        return users[0]
    return None


def _directory_external_id(fio: str, email: str) -> str:
    from app.tools.onec.lookup_user_ref import normalize_name

    key = normalize_name(fio) or email.split("@", 1)[0]
    return key[:128]


async def _ensure_user_for_gal_candidate(
    db: AsyncSession,
    *,
    fio: str,
    email: str,
) -> User | None:
    normalized_fio = fio.strip()
    normalized_email = email.strip()
    if not normalized_fio or not _is_invitable_attendee_email(normalized_email):
        return None

    try:
        matched = await _resolve_user_by_fio(db, normalized_fio)
        if matched is not None:
            return matched
    except ScheduledMeetingPersonError:
        pass

    matched = await _resolve_user_by_email(db, normalized_email)
    if matched is not None:
        return matched

    if not lookup_positions_by_fio(normalized_fio):
        return None

    from app.services.employee_sync_service import _parse_full_name
    from app.tools.onec.lookup_user_ref import normalize_name

    external_id = _directory_external_id(normalized_fio, normalized_email)
    result = await db.execute(
        select(User).where(
            User.source_system == DIRECTORY_SOURCE_SYSTEM,
            User.external_id == external_id,
        )
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        if existing.deleted_at is not None:
            existing.deleted_at = None
        existing.is_active = True
        if not _is_invitable_attendee_email((existing.email or "").strip()):
            existing.email = normalized_email
        if not (existing.full_name or "").strip():
            existing.full_name = normalized_fio
        if not (existing.position or "").strip():
            existing.position = primary_position_for_fio(normalized_fio)
        await db.flush()
        return existing

    result = await db.execute(select(User).where(User.email.ilike(normalized_email)))
    email_matches = [
        user
        for user in result.scalars().all()
        if user.deleted_at is None and user.is_active
    ]
    if len(email_matches) == 1:
        return email_matches[0]
    if email_matches:
        return None

    names = _parse_full_name(normalized_fio)
    position = primary_position_for_fio(normalized_fio)
    username_base = (
        normalize_name(normalized_fio).replace(" ", "-")[:100]
        or normalized_email.split("@", 1)[0][:100]
    )
    username = username_base
    suffix = 1
    while True:
        result = await db.execute(select(User).where(User.username == username))
        if result.scalar_one_or_none() is None:
            break
        suffix += 1
        username = f"{username_base}-{suffix}"[:128]

    user = User(
        email=normalized_email,
        username=username,
        hashed_password=hash_password(secrets.token_urlsafe(24)),
        last_name=names["last_name"],
        first_name=names["first_name"],
        middle_name=names["middle_name"],
        full_name=normalized_fio,
        position=position,
        source_system=DIRECTORY_SOURCE_SYSTEM,
        external_id=external_id,
        is_active=True,
        is_verified=False,
        must_change_password=True,
    )
    db.add(user)
    await db.flush()
    return user


async def _ensure_user_from_outlook(
    db: AsyncSession,
    *,
    fio: str,
    email: str,
) -> User:
    normalized_fio = fio.strip()
    normalized_email = email.strip()
    if not normalized_fio or not _is_invitable_attendee_email(normalized_email):
        raise ScheduledMeetingPersonError(
            f"У сотрудника «{normalized_fio or email}» нет корпоративного e-mail",
            status_code=400,
        )

    try:
        matched = await _resolve_user_by_fio(db, normalized_fio)
        if matched is not None:
            invitable = await _invitable_email_for_user(matched)
            if invitable:
                return matched
    except ScheduledMeetingPersonError:
        pass

    matched = await _resolve_user_by_email(db, normalized_email)
    if matched is not None:
        return matched

    created = await _ensure_user_for_gal_candidate(
        db,
        fio=normalized_fio,
        email=normalized_email,
    )
    if created is not None:
        return created

    from app.services.employee_sync_service import _parse_full_name
    from app.tools.onec.lookup_user_ref import normalize_name

    external_id = _directory_external_id(normalized_fio, normalized_email)
    names = _parse_full_name(normalized_fio)
    position = primary_position_for_fio(normalized_fio)
    username_base = (
        normalize_name(normalized_fio).replace(" ", "-")[:100]
        or normalized_email.split("@", 1)[0][:100]
    )
    username = username_base
    suffix = 1
    while True:
        result = await db.execute(select(User).where(User.username == username))
        if result.scalar_one_or_none() is None:
            break
        suffix += 1
        username = f"{username_base}-{suffix}"[:128]

    user = User(
        email=normalized_email,
        username=username,
        hashed_password=hash_password(secrets.token_urlsafe(24)),
        last_name=names["last_name"],
        first_name=names["first_name"],
        middle_name=names["middle_name"],
        full_name=normalized_fio,
        position=position,
        source_system=DIRECTORY_SOURCE_SYSTEM,
        external_id=external_id,
        is_active=True,
        is_verified=False,
        must_change_password=True,
    )
    db.add(user)
    await db.flush()
    return user


async def _resolve_person_from_outlook(
    db: AsyncSession,
    *,
    fio: str,
    email: str,
) -> ResolvedPerson:
    normalized_fio = fio.strip()
    normalized_email = email.strip()
    gal_match = await _resolve_gal_person(normalized_fio)
    if gal_match is not None:
        gal_fio, gal_email = gal_match
        if gal_email.lower() == normalized_email.lower():
            normalized_fio = gal_fio
        elif not _is_invitable_attendee_email(normalized_email):
            normalized_fio = gal_fio
            normalized_email = gal_email

    user = await _ensure_user_from_outlook(
        db,
        fio=normalized_fio,
        email=normalized_email,
    )
    position_name = (user.position or "").strip() or primary_position_for_fio(normalized_fio)
    position_id = await resolve_position_id_for_user(db, user)
    if position_id is None and position_name:
        position_id = await resolve_position_id_for_title(db, position_name)
    invitable_email = await _invitable_email_for_user(user)
    if not invitable_email:
        raise ScheduledMeetingPersonError(
            f"У сотрудника «{normalized_fio}» нет корпоративного e-mail",
            status_code=400,
        )
    return ResolvedPerson(
        user_id=user.id,
        fio=(user.full_name or normalized_fio).strip(),
        email=invitable_email,
        position_id=position_id,
        position_name=position_name or None,
    )


async def _resolve_gal_person(fio: str) -> tuple[str, str] | None:
    from app.tools.onec.exchange_gal_lookup import (
        dispatch_search_exchange_gal_users,
        pick_exact_exchange_gal_user,
    )

    query = fio.strip()
    if not query:
        return None
    candidates = await asyncio.to_thread(dispatch_search_exchange_gal_users, query)
    exact = pick_exact_exchange_gal_user(query, candidates)
    if exact is None:
        if len(candidates) == 1:
            exact = candidates[0]
        else:
            return None
    email = (exact.get("email") or "").strip()
    resolved_fio = (exact.get("fio") or query).strip()
    if not email or not _is_invitable_attendee_email(email):
        return None
    return resolved_fio, email


async def resolve_person_by_fio(db: AsyncSession, fio: str) -> ResolvedPerson:
    normalized = fio.strip()
    if not normalized:
        raise ScheduledMeetingPersonError("Укажите ФИО сотрудника", status_code=400)

    user = await _resolve_user_by_fio(db, normalized)
    if user is not None:
        email = (user.email or "").strip()
        position_id = await resolve_position_id_for_user(db, user)
        position_name = (user.position or "").strip() or None
        return ResolvedPerson(
            user_id=user.id,
            fio=(user.full_name or normalized).strip(),
            email=email,
            position_id=position_id,
            position_name=position_name,
        )

    gal_match = await _resolve_gal_person(normalized)
    if gal_match is not None:
        resolved_fio, email = gal_match
        return await _resolve_person_from_outlook(
            db,
            fio=resolved_fio,
            email=email,
        )

    raise ScheduledMeetingPersonError(
        f"Не удалось найти сотрудника «{normalized}»",
        status_code=404,
    )


async def resolve_person(
    db: AsyncSession,
    *,
    user_id: uuid.UUID | None = None,
    fio: str | None = None,
    email: str | None = None,
    memo_person: dict[str, Any] | None = None,
) -> ResolvedPerson:
    if user_id is not None:
        user = await db.get(User, user_id)
        if user is not None and user.deleted_at is None and user.is_active:
            invitable_email = await _invitable_email_for_user(user)
            if invitable_email:
                position_name = (user.position or "").strip() or primary_position_for_fio(
                    (user.full_name or fio or "").strip()
                )
                position_id = await resolve_position_id_for_user(db, user)
                if position_id is None and position_name:
                    position_id = await resolve_position_id_for_title(db, position_name)
                return ResolvedPerson(
                    user_id=user.id,
                    fio=(user.full_name or "").strip() or str(user.id),
                    email=invitable_email,
                    position_id=position_id,
                    position_name=position_name or None,
                )

        normalized_fio = (fio or "").strip()
        normalized_email = (email or "").strip()
        if normalized_fio and normalized_email:
            return await _resolve_person_from_outlook(
                db,
                fio=normalized_fio,
                email=normalized_email,
            )

        raise ScheduledMeetingPersonError(
            f"Сотрудник не найден: {user_id}",
            status_code=404,
        )

    if memo_person is not None:
        memo_fio = _person_fio_from_dict(memo_person)
        if memo_fio:
            try:
                return await resolve_person_by_fio(db, memo_fio)
            except ScheduledMeetingPersonError:
                gal_match = await _resolve_gal_person(memo_fio)
                if gal_match is not None:
                    resolved_fio, email = gal_match
                    title = _person_position_from_dict(memo_person)
                    if not title:
                        title = primary_position_for_fio(resolved_fio) or ""
                    position_id = None
                    position_name = title or None
                    if title:
                        position = await _find_position_by_title(db, title)
                        if position is not None:
                            position_id = position.id
                            position_name = position.name
                    person = await _resolve_person_from_outlook(
                        db,
                        fio=resolved_fio,
                        email=email,
                    )
                    return ResolvedPerson(
                        user_id=person.user_id,
                        fio=resolved_fio,
                        email=person.email,
                        position_id=position_id or person.position_id,
                        position_name=position_name or person.position_name,
                    )
                raise
        title = _person_position_from_dict(memo_person)
        if title:
            position = await _find_position_by_title(db, title)
            if position is not None:
                raise ScheduledMeetingPersonError(
                    f"Не удалось определить сотрудника для должности «{title}»",
                    status_code=400,
                )
        raise ScheduledMeetingPersonError("Не указаны данные сотрудника", status_code=400)

    if fio:
        return await resolve_person_by_fio(db, fio)

    raise ScheduledMeetingPersonError("Укажите сотрудника серии", status_code=400)


async def list_employee_options(
    db: AsyncSession,
    *,
    search: str | None = None,
    limit: int = 20,
) -> list[EmployeeOption]:
    from app.tools.onec.exchange_gal_lookup import dispatch_search_exchange_gal_users

    query_text = (search or "").strip()
    if len(query_text) < 3:
        return []

    options: list[EmployeeOption] = []
    seen_emails: set[str] = set()

    gal_candidates = await asyncio.to_thread(
        dispatch_search_exchange_gal_users,
        query_text,
    )
    for item in gal_candidates:
        if len(options) >= limit:
            break
        email = (item.get("email") or "").strip()
        fio = (item.get("fio") or "").strip()
        if not email or not fio or not _is_invitable_attendee_email(email):
            continue
        email_key = email.lower()
        if email_key in seen_emails:
            continue
        seen_emails.add(email_key)
        position_name = primary_position_for_fio(fio)
        position_id = await resolve_position_id_for_title(db, position_name)
        options.append(
            EmployeeOption(
                id=outlook_user_id_for_email(email),
                fio=fio,
                email=email,
                position_name=position_name,
                position_id=position_id,
            )
        )

    return options[:limit]


async def _user_to_employee_option(db: AsyncSession, user: User) -> EmployeeOption | None:
    email = await _invitable_email_for_user(user)
    if not email:
        return None
    return EmployeeOption(
        id=user.id,
        fio=(user.full_name or email).strip(),
        email=email,
        position_name=(user.position or "").strip() or None,
    )


async def _load_users_by_position_key(db: AsyncSession) -> dict[str, list[User]]:
    result = await db.execute(
        select(User).where(
            User.deleted_at.is_(None),
            User.is_active.is_(True),
            User.email.is_not(None),
        )
    )
    grouped: dict[str, list[User]] = {}
    for user in result.scalars().all():
        position_key = normalize_position_title(
            normalize_position_name(user.position or "")
        )
        if not position_key:
            continue
        grouped.setdefault(position_key, []).append(user)
    return grouped


@dataclass(frozen=True)
class PositionResolveResult:
    position_id: uuid.UUID
    position_name: str
    status: str  # resolved | ambiguous | empty | not_found
    employee: EmployeeOption | None = None
    candidates: tuple[EmployeeOption, ...] = ()


async def _candidates_for_position_name(
    db: AsyncSession,
    *,
    position_name: str,
    users_by_position: dict[str, list[User]],
) -> list[EmployeeOption]:
    position_key = normalize_position_title(position_name)
    candidates: list[EmployeeOption] = []
    seen_user_ids: set[uuid.UUID] = set()

    for user in users_by_position.get(position_key, []) if position_key else []:
        option = await _user_to_employee_option(db, user)
        if option is None or option.id in seen_user_ids:
            continue
        seen_user_ids.add(option.id)
        candidates.append(option)

    if candidates:
        return candidates

    for fio in lookup_fios_by_position_title(position_name):
        for user in await _list_users_by_fio(db, fio):
            option = await _user_to_employee_option(db, user)
            if option is None or option.id in seen_user_ids:
                continue
            seen_user_ids.add(option.id)
            candidates.append(option)

    return candidates


async def resolve_users_for_position_ids(
    db: AsyncSession,
    position_ids: list[uuid.UUID],
) -> list[PositionResolveResult]:
    if not position_ids:
        return []

    users_by_position = await _load_users_by_position_key(db)
    results: list[PositionResolveResult] = []
    seen_ids: set[uuid.UUID] = set()

    for position_id in position_ids:
        if position_id in seen_ids:
            continue
        seen_ids.add(position_id)

        position = await db.get(Position, position_id)
        if position is None or not position.is_active:
            results.append(
                PositionResolveResult(
                    position_id=position_id,
                    position_name=position.name if position is not None else str(position_id),
                    status="not_found",
                )
            )
            continue

        position_name = position.name.strip()
        candidates = await _candidates_for_position_name(
            db,
            position_name=position_name,
            users_by_position=users_by_position,
        )

        if not candidates:
            results.append(
                PositionResolveResult(
                    position_id=position_id,
                    position_name=position_name,
                    status="empty",
                )
            )
        elif len(candidates) == 1:
            results.append(
                PositionResolveResult(
                    position_id=position_id,
                    position_name=position_name,
                    status="resolved",
                    employee=candidates[0],
                    candidates=(candidates[0],),
                )
            )
        else:
            results.append(
                PositionResolveResult(
                    position_id=position_id,
                    position_name=position_name,
                    status="ambiguous",
                    candidates=tuple(candidates),
                )
            )

    return results
