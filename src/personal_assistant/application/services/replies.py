"""Build user-facing replies from locale catalogs."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
import json
from pathlib import Path
from typing import Any


CatalogValue = str | list[str]

_DEFAULT_LOCALE = "es"
_CATALOG_CACHE: dict[str, dict[str, CatalogValue]] = {}


class AssistantReplies:
    """Build user-facing copy without mixing it into business decisions."""

    def __init__(self, locale: str = _DEFAULT_LOCALE, *, catalog: dict[str, CatalogValue] | None = None) -> None:
        self._catalog = dict(catalog) if catalog is not None else _load_catalog(locale)

    @classmethod
    def from_catalog(cls, catalog: dict[str, CatalogValue]) -> "AssistantReplies":
        return cls(catalog=catalog)

    def start(self) -> str:
        return self._text("start")

    def help(self) -> str:
        return "\n".join(self._lines("help"))

    def unsupported(self) -> str:
        return self._text("unsupported")

    def status(self, *, pending_count: int, state_count: int, event_count: int, outbox_count: int) -> str:
        return self._format(
            "status",
            pending_count=pending_count,
            state_count=state_count,
            event_count=event_count,
            outbox_count=outbox_count,
        )

    def runtime_request_received(self) -> str:
        return self._text("runtime_request_received")

    def agenda_empty(self) -> str:
        return self._text("agenda_empty")

    def agenda(self, rows: Iterable[tuple[datetime, str, str]]) -> str:
        lines = [self._text("agenda_header")]
        for starts_at, title, event_id in rows:
            lines.append(
                self._format(
                    "agenda_row",
                    starts_at=starts_at.isoformat(),
                    title=title,
                    event_id=event_id,
                )
            )
        return "\n".join(lines)

    def pending_empty(self) -> str:
        return self._text("pending_empty")

    def pending_approvals(self, rows: Iterable[tuple[str, str, str]]) -> str:
        lines = [self._text("pending_header")]
        for approval_id, action, request_text in rows:
            lines.append(
                self._format(
                    "pending_row",
                    approval_id=approval_id,
                    action=action,
                    request_text=request_text,
                )
            )
        lines.append(self._text("pending_footer"))
        return "\n".join(lines)

    def reminder_missing_text(self) -> str:
        return self._text("reminder_missing_text")

    def reminder_duplicate(self) -> str:
        return self._text("reminder_duplicate")

    def reminder_needs_datetime(self) -> str:
        return self._text("reminder_needs_datetime")

    def reminder_ambiguous_hour(self) -> str:
        return self._text("reminder_ambiguous_hour")

    def reminder_missing_date(self) -> str:
        return self._text("reminder_missing_date")

    def reminder_nonexistent_local_time(self) -> str:
        return self._text("reminder_nonexistent_local_time")

    def reminder_ambiguous_local_time(self) -> str:
        return self._text("reminder_ambiguous_local_time")

    def reminder_invalid_timezone(self) -> str:
        return self._text("reminder_invalid_timezone")

    def reminder_replay_conflict(self) -> str:
        return self._text("reminder_replay_conflict")

    def reminder_needs_approval(self, title: str) -> str:
        return self._format("reminder_needs_approval", title=title)

    def approval_command_hint(self, approval_id: str) -> str:
        return self._format("approval_command_hint", approval_id=approval_id)

    def approval_reason_calendar_create_event(self) -> str:
        return self._text("approval_reason_calendar_create_event")

    def reminder_notification_body(self, title: str) -> str:
        return self._format("reminder_notification_body", title=title)

    def reminder_created(self, *, title: str, minutes_before: int, direct_notice: bool = False) -> str:
        if direct_notice:
            return self._format("reminder_created_direct", title=title)
        return self._format(
            "reminder_created_with_notice",
            title=title,
            minutes_label=self._minutes_label(minutes_before),
        )

    def approve_missing_id(self) -> str:
        return self._text("approve_missing_id")

    def approval_not_found(self) -> str:
        return self._text("approval_not_found")

    def approval_type_unsupported(self) -> str:
        return self._text("approval_type_unsupported")

    def approval_failed(self) -> str:
        return self._text("approval_failed")

    def cancel_missing_id(self) -> str:
        return self._text("cancel_missing_id")

    def approval_cancel_failed(self) -> str:
        return self._text("approval_cancel_failed")

    def approval_cancelled(self) -> str:
        return self._text("approval_cancelled")

    def telegram_audio_missing_file_id(self) -> str:
        return self._text("telegram_audio_missing_file_id")

    def telegram_transcription_not_configured(self) -> str:
        return self._text("telegram_transcription_not_configured")

    def telegram_token_missing_for_audio(self) -> str:
        return self._text("telegram_token_missing_for_audio")

    def telegram_audio_too_large(self) -> str:
        return self._text("telegram_audio_too_large")

    def telegram_audio_download_too_large(self) -> str:
        return self._text("telegram_audio_download_too_large")

    def telegram_file_path_missing(self) -> str:
        return self._text("telegram_file_path_missing")

    def telegram_transcription_failed(self) -> str:
        return self._text("telegram_transcription_failed")

    def _minutes_label(self, minutes: int) -> str:
        if minutes == 1:
            return self._text("minutes_singular")
        return self._format("minutes_plural", minutes=minutes)

    def _text(self, key: str) -> str:
        value = self._catalog[key]
        if not isinstance(value, str):
            raise TypeError(f"Reply copy key {key!r} must be a string.")
        return value

    def _lines(self, key: str) -> list[str]:
        value = self._catalog[key]
        if isinstance(value, str):
            return [value]
        if not isinstance(value, list) or not all(isinstance(line, str) for line in value):
            raise TypeError(f"Reply copy key {key!r} must be a list of strings.")
        return value

    def _format(self, key: str, **values: object) -> str:
        return self._text(key).format(**values)


def _load_catalog(locale: str) -> dict[str, CatalogValue]:
    if locale not in _CATALOG_CACHE:
        catalog_path = _catalog_path(locale)
        raw_catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        if not isinstance(raw_catalog, dict):
            raise TypeError(f"Reply catalog {catalog_path} must contain a JSON object.")
        _CATALOG_CACHE[locale] = {str(key): _catalog_value(value) for key, value in raw_catalog.items()}
    return _CATALOG_CACHE[locale]


def _catalog_value(value: Any) -> CatalogValue:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    raise TypeError("Reply catalog values must be strings or lists of strings.")


def _catalog_path(locale: str) -> Path:
    filename = f"{locale}.json"
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "locales" / filename
        if candidate.is_file():
            return candidate
    candidate = Path.cwd() / "locales" / filename
    if candidate.is_file():
        return candidate
    raise FileNotFoundError(f"Reply catalog not found: locales/{filename}")
