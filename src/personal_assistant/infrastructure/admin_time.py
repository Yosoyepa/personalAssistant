"""Datetime ordering and formatting helpers for the admin dashboard."""

from __future__ import annotations

from datetime import UTC, datetime

from personal_assistant.application.ports.calendar import CalendarEventResult
from personal_assistant.application.ports.scheduler import ScheduledReminder


def _ordered_agenda_events(events: list[CalendarEventResult], *, now: datetime) -> list[CalendarEventResult]:
    upcoming = sorted(
        (event for event in events if _is_on_or_after(event.starts_at, now)),
        key=lambda event: (_datetime_sort_value(event.starts_at), event.event_id),
    )
    past = sorted(
        (event for event in events if not _is_on_or_after(event.starts_at, now)),
        key=lambda event: (_datetime_sort_value(event.starts_at), event.event_id),
        reverse=True,
    )
    return [*upcoming, *past]


def _ordered_reminder_jobs(jobs: list[ScheduledReminder], *, now: datetime) -> list[ScheduledReminder]:
    due = sorted(
        (job for job in jobs if _reminder_due(job, now)),
        key=lambda job: (_datetime_sort_value(job.notify_at), job.reminder_id),
    )
    pending = sorted(
        (job for job in jobs if not job.sent and not _reminder_due(job, now)),
        key=lambda job: (_datetime_sort_value(job.notify_at), job.reminder_id),
    )
    sent = sorted(
        (job for job in jobs if job.sent),
        key=lambda job: (_datetime_sort_value(job.notify_at), job.reminder_id),
        reverse=True,
    )
    return [*due, *pending, *sent]


def _reminder_due(job: ScheduledReminder, now: datetime) -> bool:
    return not job.sent and _is_on_or_before(job.notify_at, now)


def _is_on_or_after(value: datetime, reference: datetime) -> bool:
    return _datetime_sort_value(value) >= _datetime_sort_value(reference)


def _is_on_or_before(value: datetime, reference: datetime) -> bool:
    return _datetime_sort_value(value) <= _datetime_sort_value(reference)


def _is_same_day(value: datetime, reference: datetime) -> bool:
    reference_tz = reference.tzinfo or UTC
    return _aware_datetime(value).astimezone(reference_tz).date() == _aware_datetime(reference).date()


def _datetime_sort_value(value: datetime) -> float:
    return _aware_datetime(value).timestamp()


def _aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()
