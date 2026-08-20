"""Admin browser authentication routes (login/logout) using secure cookies."""

from __future__ import annotations

from typing import Annotated, cast
from urllib.parse import parse_qs

from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import JSONResponse

from personal_assistant.adapters.inbound.auth import (
    LocalPrincipalProvider,
    is_loopback_peer,
)
from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode
from personal_assistant.domain.common.identity import Principal
from personal_assistant.infrastructure.http_auth import current_principal

_COOKIE_NAME = "admin_token"
_COOKIE_MAX_AGE = 43200
_COOKIE_PATH = "/admin"


def register_admin_auth_routes(app: FastAPI) -> None:
    """Register /admin/login and /admin/logout endpoints on the FastAPI app."""

    @app.post("/admin/login")
    async def admin_login(request: Request) -> Response:
        provider = cast(
            LocalPrincipalProvider | None,
            getattr(request.app.state, "local_principal_provider", None),
        )
        if provider is None:
            raise AssistantError(
                ErrorCode.AUTHENTICATION_REQUIRED,
                "valid local bearer credentials are required",
            )

        peer_host = request.client.host if request.client is not None else None
        if not provider.allow_remote and not is_loopback_peer(peer_host):
            raise AssistantError(
                ErrorCode.PERMISSION_DENIED,
                "local authentication requires a loopback peer",
            )

        body = await request.body()
        try:
            parsed = parse_qs(body.decode("utf-8"))
        except UnicodeDecodeError:
            raise AssistantError(
                ErrorCode.AUTHENTICATION_REQUIRED,
                "valid local bearer credentials are required",
            ) from None

        token_list = parsed.get("token", [])
        token = token_list[0] if token_list else None

        if not token or not provider.verify_token(token):
            raise AssistantError(
                ErrorCode.AUTHENTICATION_REQUIRED,
                "valid local bearer credentials are required",
            )

        response = JSONResponse(content={"status": "ok", "message": "logged in"})
        response.set_cookie(
            key=_COOKIE_NAME,
            value=token,
            max_age=_COOKIE_MAX_AGE,
            path=_COOKIE_PATH,
            httponly=True,
            secure=True,
            samesite="strict",
        )
        return response

    @app.post("/admin/logout")
    async def admin_logout(
        request: Request,
        _principal: Annotated[Principal, Depends(current_principal)],
    ) -> Response:
        response = JSONResponse(content={"status": "ok", "message": "logged out"})
        response.delete_cookie(
            key=_COOKIE_NAME,
            path=_COOKIE_PATH,
            httponly=True,
            secure=True,
            samesite="strict",
        )
        return response
