"""HTTP client for WhatsApp Cloud API outbound message dispatch."""

from __future__ import annotations

import json
from http.client import HTTPException
from typing import Any
from urllib import request as urllib_request
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin

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


class _NoRedirectHandler(urllib_request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib_request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl


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
        self._egress_allowlist = egress_allowlist

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

    def get_media_url(self, media_id: str) -> str:
        clean_id = media_id.strip()
        if not clean_id:
            raise ValueError("media_id is required")
        url = f"https://graph.facebook.com/{self._api_version}/{clean_id}"
        if self._egress_allowlist is not None:
            self._egress_allowlist.require(url)
        req = urllib_request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self._access_token}",
            },
            method="GET",
        )
        try:
            with urllib_request.urlopen(req, timeout=self._timeout_seconds) as response:
                raw = response.read()
        except HTTPError as exc:
            _read_http_error_body(exc)
            raise RuntimeError(
                f"WhatsApp get_media_url failed with status {exc.code}"
            ) from exc
        except (
            TimeoutError,
            ConnectionResetError,
            HTTPException,
            URLError,
            OSError,
        ) as exc:
            raise RuntimeError("WhatsApp get_media_url network error") from exc

        decoded = json.loads(raw.decode("utf-8"))
        if not isinstance(decoded, dict) or not decoded.get("url"):
            raise RuntimeError("WhatsApp get_media_url returned invalid response")
        return str(decoded["url"])

    def download_media(self, url: str) -> bytes:
        opener = urllib_request.build_opener(_NoRedirectHandler())
        current_url = url
        max_redirects = 5
        for _ in range(max_redirects):
            if self._egress_allowlist is not None:
                self._egress_allowlist.require(current_url)
            req = urllib_request.Request(
                current_url,
                headers={
                    "Authorization": f"Bearer {self._access_token}",
                    "User-Agent": "PersonalAssistant/1.0",
                },
                method="GET",
            )
            try:
                with opener.open(req, timeout=self._timeout_seconds) as response:
                    return response.read()
            except HTTPError as exc:
                if exc.code in (301, 302, 303, 307, 308):
                    location = exc.headers.get("Location")
                    if not location:
                        raise RuntimeError("Redirect without Location header") from exc
                    current_url = urljoin(current_url, location)
                    continue
                _read_http_error_body(exc)
                raise RuntimeError(
                    f"WhatsApp download_media failed with status {exc.code}"
                ) from exc
            except (
                TimeoutError,
                ConnectionResetError,
                HTTPException,
                URLError,
                OSError,
            ) as exc:
                raise RuntimeError("WhatsApp download_media network error") from exc
        raise RuntimeError("Too many redirects downloading media")

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
