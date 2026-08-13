"""Local admin boundary helpers: loopback checks and limit clamping."""

from __future__ import annotations

from ipaddress import ip_address, ip_network

from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import PermissionTier

DEFAULT_LIMIT = 50


MAX_LIMIT = 200


_LOCAL_NETWORKS = (
    ip_network("127.0.0.0/8"),
    ip_network("::1/128"),
)


def local_admin_principal(
    *,
    tenant_id: str,
    principal_id: str = "local-admin",
    permission_tier: PermissionTier = PermissionTier.P0,
) -> Principal:
    """Create the trusted principal used by the local-only admin boundary."""
    principal = Principal(
        principal_id=principal_id,
        tenant_id=tenant_id,
        auth_subject=principal_id,
        auth_provider="local-admin",
        permission_tier=permission_tier,
    )
    principal.mark_trusted("local-admin")
    return principal


def clamp_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_LIMIT
    return max(1, min(limit, MAX_LIMIT))


def is_local_client(host: str | None) -> bool:
    """Return true only for loopback clients used by the local admin UI."""
    normalized = _normalize_host(host)
    if normalized is None:
        return False
    if normalized == "localhost":
        return True
    try:
        client_ip = ip_address(normalized)
    except ValueError:
        return False
    return any(client_ip in network for network in _LOCAL_NETWORKS)


def _normalize_host(host: str | None) -> str | None:
    if host is None:
        return None
    value = host.strip().lower()
    if not value:
        return None
    if value.startswith("[") and "]" in value:
        return value[1 : value.index("]")]
    if value.count(":") == 1:
        host_part, port_part = value.rsplit(":", 1)
        if port_part.isdigit():
            return host_part
    return value
