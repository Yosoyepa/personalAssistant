"""CLI argument parsing and formatting helpers for the durable outbox worker."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import datetime

from personal_assistant.application.dto.events import OutboxMessage


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m personal_assistant.infrastructure.worker"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run-once")
    commands.add_parser("list-uncertain")
    resolve = commands.add_parser("resolve-uncertain")
    resolve.add_argument("--message-id", required=True)
    resolve.add_argument("--resolution", choices=("delivered", "retry"), required=True)
    resolve.add_argument("--confirm", required=True)
    return parser


def _safe_message(message: OutboxMessage) -> dict[str, object]:
    error = message.last_error
    return {
        "message_id": message.id,
        "status": message.dispatch_status.value,
        "attempts": message.attempts,
        "claimed_until": _timestamp(message.claimed_until),
        "next_attempt_at": _timestamp(message.next_attempt_at),
        "sending_at": _timestamp(message.sending_at),
        "published_at": _timestamp(message.published_at),
        "error_category": error.category.value if error is not None else None,
        "error_code": error.code.value if error is not None else None,
        "provider_code": error.provider_code if error is not None else None,
    }


def _timestamp(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _print_rows(rows: Sequence[dict[str, object]]) -> None:
    print(json.dumps(list(rows), sort_keys=True, separators=(",", ":")))
