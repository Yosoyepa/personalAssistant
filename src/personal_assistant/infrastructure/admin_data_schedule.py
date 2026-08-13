"""Scheduler, agenda, and reminder fetchers for the admin dashboard."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from personal_assistant.domain.common.identity import Principal
from personal_assistant.infrastructure.admin_auth import DEFAULT_LIMIT, clamp_limit
from personal_assistant.infrastructure.admin_items import (
    _agenda_item,
    _reminder_item,
    _scheduled_reminder_item,
)
from personal_assistant.infrastructure.admin_shared import (
    _tenant_calendar_events,
    _tenant_scheduler_jobs,
)
from personal_assistant.infrastructure.admin_time import (
    _is_on_or_after,
    _is_same_day,
    _ordered_agenda_events,
    _ordered_reminder_jobs,
    _reminder_due,
)
from personal_assistant.infrastructure.bootstrap import AppContainer


def fetch_scheduler(
    container: AppContainer,
    principal: Principal,
    *,
    now: datetime | None = None,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Summarize scheduled reminder jobs with due/sent/pending counts."""
    now = now or datetime.now(UTC)
    jobs = _ordered_reminder_jobs(_tenant_scheduler_jobs(container, principal.tenant_id), now=now)
    due = [job for job in jobs if _reminder_due(job, now)]
    counts = {
        "scheduled": len(jobs),
        "due": len(due),
        "sent": len([job for job in jobs if job.sent]),
        "pending": len([job for job in jobs if not job.sent and not _reminder_due(job, now)]),
    }
    return {
        "total": len(jobs),
        "counts": counts,
        "items": [_scheduled_reminder_item(job, now=now) for job in jobs[: clamp_limit(limit)]],
    }


def fetch_agenda(
    container: AppContainer,
    principal: Principal,
    *,
    now: datetime | None = None,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Summarize calendar events into upcoming/today/past dashboard buckets."""
    now = now or datetime.now(UTC)
    events = _ordered_agenda_events(_tenant_calendar_events(container, principal), now=now)
    upcoming = [event for event in events if _is_on_or_after(event.starts_at, now)]
    past = [event for event in events if not _is_on_or_after(event.starts_at, now)]
    today = [event for event in events if _is_same_day(event.starts_at, now)]
    return {
        "total": len(events),
        "upcoming_count": len(upcoming),
        "today_count": len(today),
        "past_count": len(past),
        "next_event": _agenda_item(upcoming[0], now=now) if upcoming else None,
        "items": [_agenda_item(event, now=now) for event in events[: clamp_limit(limit)]],
    }


def fetch_reminders(
    container: AppContainer,
    principal: Principal,
    *,
    now: datetime | None = None,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Summarize reminder jobs enriched with their calendar event context."""
    now = now or datetime.now(UTC)
    jobs = _ordered_reminder_jobs(_tenant_scheduler_jobs(container, principal.tenant_id), now=now)
    events_by_id = {event.event_id: event for event in _tenant_calendar_events(container, principal)}
    due = [job for job in jobs if _reminder_due(job, now)]
    counts = {
        "scheduled": len(jobs),
        "due": len(due),
        "sent": len([job for job in jobs if job.sent]),
        "pending": len([job for job in jobs if not job.sent and not _reminder_due(job, now)]),
    }
    return {
        "total": len(jobs),
        "counts": counts,
        "items": [
            _reminder_item(job, now=now, event=events_by_id.get(job.calendar_event_id))
            for job in jobs[: clamp_limit(limit)]
        ],
    }
