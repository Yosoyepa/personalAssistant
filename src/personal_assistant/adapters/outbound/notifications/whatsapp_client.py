"""HTTP client for WhatsApp Cloud API outbound message dispatch."""

from __future__ import annotations

import json
from http.client import HTTPException
from urllib import request as urllib_request
from urllib.error import HTTPError, URLError

from personal_assistant.adapters.outbound.egress import (
    DEFAULT_WHATSAPP_API_URL,
    EgressAllowlist,
)
from personal_assistant.adapters.outbound.notifications.whatsapp_models import (
    WhatsAppProviderResult,
)
from personal_assistant.adapters.outbound.notifications.whatsapp_parsing import (
    _classify_failure,
    _classify_response,
    _read_http_error_body,
    _retry_after_header,
)


class WhatsAppGraphApiClient:
    """Small stdlib WhatsApp Cloud API client for outbound notification dispatch."""

    def __init__(
        self,
        *,
        access_token: str,
        phone_number_id: str,
        api_version: str = "v21.0",
        timeout_seconds: float = 10.0,
        egress_allowlist: EgressAllowlist | None = None,
    ) -> None:
        if not access_token.strip():
            raise ValueError("whatsapp access token is required")
        if not phone_number_id.strip():
            raise ValueError("whatsapp phone number id is required")
        if egress_allowlist is not None:
            egress_allowlist.require(DEFAULT_WHATSAPP_API_URL)
        self._access_token = access_token.strip()
        self._phone_number_id = phone_number_id.strip()
        self._api_version = api_version.strip()
        self._timeout_seconds = timeout_seconds

    def send_message(self, *, recipient: str, text: str) -> WhatsAppProviderResult:
        payload = json.dumps(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": recipient,
                "type": "text",
                "text": {"preview_url": False, "body": text},
            }
        ).encode("utf-8")
        req = urllib_request.Request(
            f"https://graph.facebook.com/{self._api_version}/{self._phone_number_id}/messages",
            data=payload,
            headers={
                "Authorization": f"Bearer {self._access_token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        return self._send(req)

    def _send(self, req: urllib_request.Request) -> WhatsAppProviderResult:
        try:
            with urllib_request.urlopen(req, timeout=self._timeout_seconds) as response:
                raw = response.read()
                status = getattr(response, "status", None)
                headers = getattr(response, "headers", None)
        except HTTPError as exc:
            raw = _read_http_error_body(exc)
            return _classify_failure(
                provider_code=exc.code,
                raw=raw,
                retry_after_header=_retry_after_header(exc.headers),
            )
        except (
            TimeoutError,
            ConnectionResetError,
            HTTPException,
            URLError,
            OSError,
        ):
            return WhatsAppProviderResult(outcome="unknown-outcome")

        if status is not None and status >= 400:
            return _classify_failure(
                provider_code=status,
                raw=raw,
                retry_after_header=_retry_after_header(headers),
            )
        return _classify_response(raw)
