"""WhatsApp integration settings and dataclass."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class WhatsAppSettings:
    enabled: bool = False
    app_secret: str = field(default="", repr=False)
    verify_token: str = field(default="", repr=False)
    allowed_user_ids: frozenset[str] = field(default=frozenset(), repr=False)
    access_token: str | None = field(default=None, repr=False)
    phone_number_id: str = field(default="")
