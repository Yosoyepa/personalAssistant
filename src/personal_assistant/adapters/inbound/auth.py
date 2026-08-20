"""Inbound auth claim mapping, local principal provider, and claims DTOs."""

from __future__ import annotations

from personal_assistant.adapters.inbound.auth_claims import (
    AuthClaims,
    principal_from_auth_claims,
)
from personal_assistant.adapters.inbound.auth_local import (
    LocalPrincipalConfig,
    LocalPrincipalProvider,
    LocalPrincipalSettings,
)
from personal_assistant.adapters.inbound.auth_peer import is_loopback_peer

__all__ = [
    "AuthClaims",
    "LocalPrincipalConfig",
    "LocalPrincipalProvider",
    "LocalPrincipalSettings",
    "is_loopback_peer",
    "principal_from_auth_claims",
]
