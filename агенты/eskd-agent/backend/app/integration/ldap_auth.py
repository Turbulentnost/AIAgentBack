from __future__ import annotations

import logging

from app.config import settings

_log = logging.getLogger("eskd.auth.ldap")


def authenticate_ldap(username: str, password: str) -> tuple[bool, list[str]]:
    """Optional LDAP/AD bind. Returns (ok, roles)."""
    if settings.auth_mode != "ldap" or not settings.ldap_server:
        return False, []

    try:
        import ldap3
    except ImportError:
        _log.warning("ldap3 not installed; LDAP auth disabled")
        return False, []

    server = ldap3.Server(settings.ldap_server)
    user_filter = settings.ldap_user_filter.format(username=username)
    try:
        conn = ldap3.Connection(
            server,
            user=settings.ldap_bind_dn,
            password=settings.ldap_bind_password,
            auto_bind=True,
        )
        conn.search(
            settings.ldap_base_dn,
            user_filter,
            attributes=["memberOf", "cn"],
        )
        if not conn.entries:
            return False, []
        entry = conn.entries[0]
        groups = [str(g) for g in entry.memberOf.values] if "memberOf" in entry else []
        mapping = settings.ldap_group_role_mapping
        roles = []
        for group_dn in groups:
            for group_name, role in mapping.items():
                if group_name in group_dn:
                    roles.append(role)
        conn.unbind()
        return True, roles or settings.dev_roles_list
    except Exception as exc:
        _log.warning("LDAP auth failed for %s: %s", username, exc)
        return False, []
