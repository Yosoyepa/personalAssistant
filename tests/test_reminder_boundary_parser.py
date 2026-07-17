"""Adversarial calendar boundaries for deterministic reminder parsing."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from personal_assistant.domain.reminders.models import (
    ParsedReminder,
    ReminderClarificationReason,
    ReminderNeedsClarification,
    ReminderUnsupportedReason,
    UnsupportedReminder,
)
from personal_assistant.domain.reminders.parser import extract_reminder


def _starts_at(result: object) -> datetime:
    assert isinstance(result, ParsedReminder)
    return result.extraction.starts_at


@pytest.mark.parametrize(
    ("timezone", "now", "text", "expected"),
    [
        pytest.param(
            "UTC",
            datetime(2026, 12, 31, 23, 59, tzinfo=UTC),
            "recuérdame mañana a las 00:00 cerrar el año",
            datetime(2027, 1, 1, 0, 0, tzinfo=UTC),
            id="utc-year-boundary",
        ),
        pytest.param(
            "America/Bogota",
            datetime(2027, 1, 1, 4, 59, tzinfo=UTC),
            "recuérdame mañana a la medianoche llamar a casa",
            datetime(2027, 1, 1, 5, 0, tzinfo=UTC),
            id="bogota-local-day-lags-utc",
        ),
        pytest.param(
            "America/New_York",
            datetime(2026, 3, 8, 4, 59, tzinfo=UTC),
            "recuérdame mañana a las 3:00 am revisar el despliegue",
            datetime(2026, 3, 8, 7, 0, tzinfo=UTC),
            id="new-york-spring-transition",
        ),
        pytest.param(
            "America/New_York",
            datetime(2026, 11, 1, 3, 59, tzinfo=UTC),
            "recuérdame mañana a las 2:00 am revisar el despliegue",
            datetime(2026, 11, 1, 7, 0, tzinfo=UTC),
            id="new-york-fall-transition",
        ),
    ],
)
def test_tomorrow_and_midnight_are_resolved_from_the_local_calendar(
    timezone: str,
    now: datetime,
    text: str,
    expected: datetime,
) -> None:
    result = extract_reminder(text, now, timezone=timezone)

    assert _starts_at(result) == expected


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        pytest.param(
            datetime(2026, 3, 8, 6, 59, tzinfo=UTC),
            datetime(2026, 3, 8, 7, 1, tzinfo=UTC),
            id="spring-gap-skips-local-hour",
        ),
        pytest.param(
            datetime(2026, 11, 1, 5, 59, tzinfo=UTC),
            datetime(2026, 11, 1, 6, 1, tzinfo=UTC),
            id="fall-fold-repeats-local-hour",
        ),
    ],
)
def test_relative_delay_preserves_elapsed_time_across_dst(
    now: datetime, expected: datetime
) -> None:
    result = extract_reminder(
        "recuérdame en 2 minutos revisar la alarma",
        now,
        timezone="America/New_York",
    )

    assert isinstance(result, ParsedReminder)
    assert result.extraction.starts_at == expected
    assert result.extraction.notify_at == expected
    assert result.extraction.timezone == "America/New_York"


@pytest.mark.parametrize(
    ("timezone", "now", "text", "reason"),
    [
        pytest.param(
            "America/New_York",
            datetime(2026, 3, 7, 12, tzinfo=UTC),
            "recuérdame mañana a las 2:30 am revisar la alarma",
            ReminderClarificationReason.nonexistent_local_time,
            id="new-york-gap",
        ),
        pytest.param(
            "America/New_York",
            datetime(2026, 10, 31, 12, tzinfo=UTC),
            "recuérdame mañana a las 1:30 am revisar la alarma",
            ReminderClarificationReason.ambiguous_local_time,
            id="new-york-fold",
        ),
        pytest.param(
            "Europe/Madrid",
            datetime(2026, 3, 28, 12, tzinfo=UTC),
            "recuérdame mañana a las 2:30 am revisar la alarma",
            ReminderClarificationReason.nonexistent_local_time,
            id="madrid-gap",
        ),
        pytest.param(
            "Europe/Madrid",
            datetime(2026, 10, 24, 12, tzinfo=UTC),
            "recuérdame mañana a las 2:30 am revisar la alarma",
            ReminderClarificationReason.ambiguous_local_time,
            id="madrid-fold",
        ),
    ],
)
def test_dst_gap_and_fold_never_choose_an_instant_silently(
    timezone: str,
    now: datetime,
    text: str,
    reason: ReminderClarificationReason,
) -> None:
    result = extract_reminder(text, now, timezone=timezone)

    assert isinstance(result, ReminderNeedsClarification)
    assert result.reason == reason
    assert result.timezone == timezone


@pytest.mark.parametrize(
    ("timezone", "now"),
    [
        ("UTC", datetime(2026, 6, 20, 18, tzinfo=UTC)),
        ("America/Bogota", datetime(2026, 6, 20, 23, tzinfo=UTC)),
        ("America/New_York", datetime(2026, 6, 20, 22, tzinfo=UTC)),
    ],
)
def test_today_at_an_elapsed_local_hour_is_rejected(
    timezone: str, now: datetime
) -> None:
    result = extract_reminder(
        "recuérdame hoy a las 17 cerrar caja", now, timezone=timezone
    )

    assert isinstance(result, UnsupportedReminder)
    assert result.reason == ReminderUnsupportedReason.past_time


def test_same_weekday_after_its_local_time_rolls_to_the_next_week() -> None:
    result = extract_reminder(
        "recuérdame el sábado a las 23 cerrar caja",
        datetime(2026, 6, 21, 4, 30, tzinfo=UTC),
        timezone="America/Bogota",
    )

    assert _starts_at(result) == datetime(2026, 6, 28, 4, 0, tzinfo=UTC)
