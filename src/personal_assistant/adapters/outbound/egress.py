"""Outbound egress allowlist: deny-by-default network target validation.

ADR-004 layer A. Every network-capable adapter validates its target against
this allowlist before opening a connection. Matching is exact on
``scheme + hostname``: no wildcards, no subdomain globbing, no ports. Entries
are bare hostnames (which imply ``https``) or ``scheme://hostname`` pairs.

The module lives at the adapter boundary on purpose: secrets enter only
through adapter constructors, and this allowlist is consulted exactly there,
before any socket is opened. Error messages carry hostnames only, never URLs
that might embed credentials.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode

DEFAULT_TELEGRAM_API_HOST = "api.telegram.org"
DEFAULT_TELEGRAM_API_URL = f"https://{DEFAULT_TELEGRAM_API_HOST}"


class EgressNotAllowedError(AssistantError):
    """Raised when an adapter target is absent from the egress allowlist."""

    def __init__(self, *, host: str) -> None:
        super().__init__(
            ErrorCode.GUARDRAIL_BLOCKED,
            f"egress target host is not allowlisted: {host or 'unknown'}",
        )


def _parse_entry(value: str) -> tuple[str, str]:
    """Parse one allowlist entry into a normalized ``(scheme, host)`` pair."""
    candidate = value.strip()
    if not candidate:
        raise ValueError("egress allowlist entries must be non-empty")
    if "://" in candidate:
        scheme, _, host = candidate.partition("://")
    else:
        scheme, host = "https", candidate
    scheme = scheme.strip().lower()
    host = host.strip().lower()
    if not scheme.isalpha():
        raise ValueError(f"invalid egress allowlist entry scheme: {value!r}")
    if (
        not host
        or "*" in host
        or "/" in host
        or ":" in host
        or "@" in host
        or not host.isprintable()
    ):
        raise ValueError(f"invalid egress allowlist entry host: {value!r}")
    return scheme, host


def _url_authority(url: str) -> tuple[str, str] | None:
    """Extract the ``(scheme, hostname)`` authority of a URL, fail-closed."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    scheme = (parts.scheme or "").strip().lower()
    host = (parts.hostname or "").strip().lower()
    if not scheme or not host:
        return None
    return scheme, host


@dataclass(frozen=True, slots=True)
class EgressAllowlist:
    """Exact ``(scheme, host)`` allowlist consulted by network adapters."""

    entries: frozenset[tuple[str, str]] = field(default_factory=frozenset)

    @classmethod
    def from_entries(cls, raw_entries: Iterable[str]) -> EgressAllowlist:
        """Build an allowlist from raw host or ``scheme://host`` entries."""
        return cls(entries=frozenset(_parse_entry(value) for value in raw_entries))

    def allows(self, url: str) -> bool:
        """Return True only when the URL authority is exactly allowlisted."""
        authority = _url_authority(url)
        return authority is not None and authority in self.entries

    def require(self, url: str) -> None:
        """Raise ``EgressNotAllowedError`` before any connection is opened."""
        if not self.allows(url):
            authority = _url_authority(url)
            raise EgressNotAllowedError(host=authority[1] if authority else "")

    def audit_hosts(self) -> tuple[str, ...]:
        """Sorted unique hostnames for the startup audit record; no schemes,
        no URLs, and therefore no embedded credentials."""
        return tuple(sorted({host for _, host in self.entries}))


def derive_egress_entries(
    *,
    llm_base_url: str | None,
    transcription_base_url: str | None,
    tts_base_url: str | None,
    telegram_bot_token_configured: bool,
) -> frozenset[str]:
    """Derive default allowlist entries from configured provider targets.

    Only syntactically valid absolute URLs contribute an entry; an invalid
    configured URL is left out so the fail-closed startup coverage check can
    reject it instead of silently allowing it.
    """
    entries: set[str] = set()
    for base_url in (llm_base_url, transcription_base_url, tts_base_url):
        if base_url is None or not base_url.strip():
            continue
        authority = _url_authority(base_url)
        if authority is None:
            continue
        entries.add(f"{authority[0]}://{authority[1]}")
    if telegram_bot_token_configured:
        entries.add(DEFAULT_TELEGRAM_API_URL)
    return frozenset(entries)


def require_startup_coverage(
    allowlist: EgressAllowlist,
    required: Mapping[str, str],
) -> None:
    """Fail closed when an enabled provider target is not allowlisted.

    ``required`` maps a configuration label (for example ``LLM_PROVIDER``) to
    the target URL that provider needs. The error message names the label and
    the target hostname only.
    """
    for label, url in required.items():
        if allowlist.allows(url):
            continue
        authority = _url_authority(url)
        host = authority[1] if authority else "unknown"
        raise ValueError(
            f"EGRESS_ALLOWED_HOSTS does not cover the {label} target host: {host}"
        )
