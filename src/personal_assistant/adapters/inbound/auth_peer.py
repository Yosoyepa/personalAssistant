"""Socket peer validation and bearer token parsing helpers."""

from __future__ import annotations

import re
from collections.abc import Mapping
from ipaddress import ip_address

from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode

_BEARER_AUTHORIZATION = re.compile(
    r"Bearer ([A-Za-z0-9._~+/\-]+=*)",
    flags=re.ASCII | re.IGNORECASE,
)
_LOCAL_AUTH_SOURCE = "local-bearer"


def is_loopback_peer(peer_host: str | None) -> bool:
    """Accept only a numeric IPv4/IPv6 loopback socket peer."""
    if not isinstance(peer_host, str) or not peer_host:
        return False
    if peer_host != peer_host.strip():
        return False
    try:
        peer_ip = ip_address(peer_host)
    except ValueError:
        return False
    mapped_ipv4 = getattr(peer_ip, "ipv4_mapped", None)
    return peer_ip.is_loopback or (mapped_ipv4 is not None and mapped_ipv4.is_loopback)


def _strict_bearer_token(headers: Mapping[str, str]) -> str | None:
    """Extract exactly one valid bearer token from HTTP headers."""
    authorization_values = [
        value
        for name, value in headers.items()
        if isinstance(name, str) and name.lower() == "authorization"
    ]
    if len(authorization_values) != 1:
        return None
    authorization = authorization_values[0]
    if not isinstance(authorization, str):
        return None
    matched = _BEARER_AUTHORIZATION.fullmatch(authorization)
    if matched is None:
        return None
    return matched.group(1)


def _validate_configured_token(token: str | None) -> None:
    """Ensure ADMIN_TOKEN is non-empty and well-formed."""
    if token is None:
        raise ValueError("ADMIN_TOKEN must be configured for local authentication")
    if (
        not isinstance(token, str)
        or _BEARER_AUTHORIZATION.fullmatch(f"Bearer {token}") is None
    ):
        raise ValueError("ADMIN_TOKEN must be a non-empty bearer token")


def _validate_identity_setting(value: str, *, name: str, max_length: int) -> None:
    """Ensure identity string settings are printable and bounded in length."""
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be a non-blank string without outer whitespace")
    if len(value) > max_length or not value.isprintable():
        raise ValueError(f"{name} contains invalid identity text")


def _invalid_local_credentials() -> AssistantError:
    """Construct a standard authentication failure error."""
    return AssistantError(
        ErrorCode.AUTHENTICATION_REQUIRED,
        "valid local bearer credentials are required",
    )
