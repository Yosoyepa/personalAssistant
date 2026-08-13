from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import UTC, datetime
from http.client import HTTPConnection, HTTPSConnection
from pathlib import Path
from typing import Any
from urllib import parse

ALLOWED_EVENTS = {
    "gate.completed",
    "gate.failed",
    "hardening.completed",
    "integrity.changed",
}


def payload(event: str, project: str, data: dict[str, Any]) -> bytes:
    if event not in ALLOWED_EVENTS:
        raise ValueError(f"evento no permitido: {event}")
    document = {
        "schema_version": 1,
        "event": event,
        "project": project,
        "occurred_at": datetime.now(UTC).isoformat(),
        "data": data,
    }
    return json.dumps(document, separators=(",", ":"), sort_keys=True).encode()


def signature(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def deliver(
    url: str,
    body: bytes,
    *,
    secret: str,
    timeout: float = 5.0,
) -> int:
    parsed = parse.urlparse(url)
    local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not (parsed.scheme == "http" and local):
        raise ValueError("el webhook requiere HTTPS; HTTP solo se permite en localhost")
    if parsed.username or parsed.password:
        raise ValueError("no incluyas credenciales en la URL del webhook")
    if not parsed.hostname:
        raise ValueError("la URL del webhook no tiene host")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "well-code-template/0.1",
        "X-WCT-Signature-256": signature(body, secret),
    }
    connection_type = HTTPSConnection if parsed.scheme == "https" else HTTPConnection
    connection = connection_type(parsed.hostname, parsed.port, timeout=timeout)
    target = parsed.path or "/"
    if parsed.query:
        target = f"{target}?{parsed.query}"
    try:
        connection.request("POST", target, body=body, headers=headers)
        return int(connection.getresponse().status)
    except OSError as exc:
        raise RuntimeError(f"webhook delivery failed: {exc}") from exc
    finally:
        connection.close()


def send_from_environment(root: Path, event: str, data: dict[str, Any]) -> int:
    url = os.environ.get("WCT_WEBHOOK_URL")
    secret = os.environ.get("WCT_WEBHOOK_SECRET")
    if not url or not secret:
        raise ValueError("WCT_WEBHOOK_URL y WCT_WEBHOOK_SECRET son obligatorios")
    body = payload(event, root.name, data)
    return deliver(url, body, secret=secret)
