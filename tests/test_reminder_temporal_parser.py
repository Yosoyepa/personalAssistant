"""Timezone and ambiguity contracts for deterministic reminder extraction."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import TypeAdapter, ValidationError

from personal_assistant.domain.reminders.models import (
    ParsedReminder,
    ReminderClarificationReason,
    ReminderExtraction,
    ReminderNeedsClarification,
    ReminderParseResult,
    ReminderUnsupportedReason,
    UnsupportedReminder,
)
from personal_assistant.domain.reminders.parser import extract_reminder


def parsed(result: object) -> ReminderExtraction:
    assert isinstance(result, ParsedReminder)
    assert result.status == "parsed"
    return result.extraction


def test_bogota_wall_clock_is_canonical_utc_and_keeps_iana_timezone() -> None:
    result = extract_reminder(
        "recuérdame clase el martes a las 17",
        datetime(2026, 6, 20, 12, tzinfo=UTC),
        timezone="America/Bogota",
    )

    extraction = parsed(result)
    assert extraction.starts_at == datetime(2026, 6, 23, 22, tzinfo=UTC)
    assert extraction.starts_at.tzinfo is UTC
    assert extraction.timezone == "America/Bogota"


def test_utc_wall_clock_stays_utc() -> None:
    extraction = parsed(
        extract_reminder(
            "recuérdame mañana a las 23",
            datetime(2026, 6, 20, 12, tzinfo=UTC),
            timezone="UTC",
        )
    )

    assert extraction.starts_at == datetime(2026, 6, 21, 23, tzinfo=UTC)
    assert extraction.timezone == "UTC"


@pytest.mark.parametrize(
    ("clock", "expected_hour"),
    [("1 am", 1), ("12 am", 0), ("1 pm", 13), ("12 pm", 12)],
)
def test_explicit_meridiem_is_unambiguous(clock: str, expected_hour: int) -> None:
    extraction = parsed(
        extract_reminder(
            f"recuérdame mañana a las {clock}",
            datetime(2026, 6, 20, 12, tzinfo=UTC),
            timezone="UTC",
        )
    )

    assert extraction.starts_at.hour == expected_hour


@pytest.mark.parametrize("hour", [1, 5, 12])
def test_hours_one_through_twelve_without_meridiem_need_clarification(
    hour: int,
) -> None:
    result = extract_reminder(
        f"recuérdame mañana a las {hour}",
        datetime(2026, 6, 20, 12, tzinfo=UTC),
        timezone="America/Bogota",
    )

    assert isinstance(result, ReminderNeedsClarification)
    assert result.status == "needs_clarification"
    assert result.reason == ReminderClarificationReason.ambiguous_hour


@pytest.mark.parametrize("hour", [13, 17, 23])
def test_hours_thirteen_through_twenty_three_are_unambiguous(hour: int) -> None:
    result = extract_reminder(
        f"recuérdame mañana a las {hour}",
        datetime(2026, 6, 20, 12, tzinfo=UTC),
        timezone="UTC",
    )

    assert parsed(result).starts_at.hour == hour


def test_numeric_midnight_and_word_midnight_are_supported() -> None:
    numeric = parsed(
        extract_reminder(
            "recuérdame mañana a las 00:00",
            datetime(2026, 6, 20, 12, tzinfo=UTC),
            timezone="UTC",
        )
    )
    word = parsed(
        extract_reminder(
            "recuérdame mañana a la medianoche",
            datetime(2026, 6, 20, 12, tzinfo=UTC),
            timezone="America/Bogota",
        )
    )

    assert numeric.starts_at == datetime(2026, 6, 21, 0, tzinfo=UTC)
    assert word.starts_at == datetime(2026, 6, 21, 5, tzinfo=UTC)


def test_relative_expression_uses_received_utc_instant_across_day_boundary() -> None:
    now = datetime(2026, 6, 20, 23, 59, tzinfo=UTC)
    extraction = parsed(
        extract_reminder(
            "recuérdame en 2 minutos pagar el arriendo",
            now,
            timezone="America/Bogota",
        )
    )

    assert extraction.starts_at == datetime(2026, 6, 21, 0, 1, tzinfo=UTC)
    assert extraction.notify_at == now + timedelta(minutes=2)
    assert extraction.timezone == "America/Bogota"


def test_relative_expression_preserves_instant_when_now_has_non_utc_offset() -> None:
    bogota_now = datetime(2026, 6, 20, 18, 59, tzinfo=ZoneInfo("America/Bogota"))
    extraction = parsed(
        extract_reminder(
            "recuérdame en 2 minutos pagar el arriendo",
            bogota_now,
            timezone="America/Bogota",
        )
    )

    assert extraction.starts_at == datetime(2026, 6, 21, 0, 1, tzinfo=UTC)


def test_dst_gap_requires_clarification() -> None:
    result = extract_reminder(
        "recuérdame mañana a las 2:30 am",
        datetime(2026, 3, 7, 12, tzinfo=UTC),
        timezone="America/New_York",
    )

    assert isinstance(result, ReminderNeedsClarification)
    assert result.reason == ReminderClarificationReason.nonexistent_local_time


def test_dst_fold_requires_clarification() -> None:
    result = extract_reminder(
        "recuérdame mañana a la 1:30 am",
        datetime(2026, 10, 31, 12, tzinfo=UTC),
        timezone="America/New_York",
    )

    assert isinstance(result, ReminderNeedsClarification)
    assert result.reason == ReminderClarificationReason.ambiguous_local_time


def test_invalid_timezone_requires_clarification() -> None:
    result = extract_reminder(
        "recuérdame mañana a las 17",
        datetime(2026, 6, 20, 12, tzinfo=UTC),
        timezone="Mars/Olympus_Mons",
    )

    assert isinstance(result, ReminderNeedsClarification)
    assert result.reason == ReminderClarificationReason.invalid_timezone
    assert result.timezone == "Mars/Olympus_Mons"


def test_wall_clock_without_date_does_not_infer_today_or_tomorrow() -> None:
    result = extract_reminder(
        "recuérdame la cita a las 17",
        datetime(2026, 6, 20, 12, tzinfo=UTC),
        timezone="America/Bogota",
    )

    assert isinstance(result, ReminderNeedsClarification)
    assert result.reason == ReminderClarificationReason.missing_date


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("recuérdame la cita", ReminderClarificationReason.missing_datetime),
        ("recuérdame mañana", ReminderClarificationReason.missing_time),
        ("recuérdame mañana a las 24", ReminderUnsupportedReason.invalid_time),
        ("recuérdame mañana a las 17:5", ReminderUnsupportedReason.invalid_time),
        ("recuérdame mañana a las 17:999", ReminderUnsupportedReason.invalid_time),
        ("recuérdame mañana a las 17:05x", ReminderUnsupportedReason.invalid_time),
        ("recuérdame mañana a las 17:05-18", ReminderUnsupportedReason.invalid_time),
        ("recuérdame mañana a las 17:05/18", ReminderUnsupportedReason.invalid_time),
    ],
)
def test_negative_results_have_typed_reasons(
    text: str,
    reason: ReminderClarificationReason | ReminderUnsupportedReason,
) -> None:
    result = extract_reminder(
        text, datetime(2026, 6, 20, 12, tzinfo=UTC), timezone="UTC"
    )

    assert result.reason == reason
    if isinstance(reason, ReminderClarificationReason):
        assert isinstance(result, ReminderNeedsClarification)
    else:
        assert isinstance(result, UnsupportedReminder)


@pytest.mark.parametrize(
    "text",
    [
        "recuérdame en 2 minutos mañana a las 17",
        "recuérdame en 2 minutos mañana",
        "recuérdame en 2 minutos a las 17",
        "recuérdame en 2 minutos y en 3 minutos",
    ],
)
def test_relative_expression_rejects_conflicting_date_or_wall_clock(text: str) -> None:
    result = extract_reminder(
        text,
        datetime(2026, 6, 20, 12, tzinfo=UTC),
        timezone="America/Bogota",
    )

    assert isinstance(result, UnsupportedReminder)
    assert result.reason == ReminderUnsupportedReason.conflicting_temporal_expression


@pytest.mark.parametrize(
    "text",
    [
        "recuérdame mañana a las 17 y a las 18",
        "recuérdame mañana a las 17 y a las 18:5",
        "recuérdame mañana a las 17 y a las 18:05x",
        "recuérdame el lunes y el martes a las 17",
    ],
)
def test_incompatible_absolute_expressions_are_typed_conflicts(text: str) -> None:
    result = extract_reminder(
        text,
        datetime(2026, 6, 20, 12, tzinfo=UTC),
        timezone="America/Bogota",
    )

    assert isinstance(result, UnsupportedReminder)
    assert result.reason == ReminderUnsupportedReason.conflicting_temporal_expression


def test_naive_reference_instant_is_typed_unsupported() -> None:
    result = extract_reminder(
        "recuérdame mañana a las 17",
        datetime(2026, 6, 20, 12),
        timezone="UTC",
    )

    assert isinstance(result, UnsupportedReminder)
    assert result.status == "unsupported"
    assert result.reason == ReminderUnsupportedReason.invalid_reference_instant


def test_canonical_extraction_round_trips_with_utc_and_timezone() -> None:
    extraction = ReminderExtraction(
        title="clase",
        timezone="America/Bogota",
        starts_at=datetime(2026, 6, 20, 17, tzinfo=ZoneInfo("America/Bogota")),
        notify_at=datetime(2026, 6, 20, 16, 30, tzinfo=ZoneInfo("America/Bogota")),
        confidence=0.9,
    )

    restored = ReminderExtraction.model_validate_json(extraction.model_dump_json())
    assert restored.starts_at == datetime(2026, 6, 20, 22, tzinfo=UTC)
    assert restored.notify_at == datetime(2026, 6, 20, 21, 30, tzinfo=UTC)
    assert restored.starts_at.utcoffset() == timedelta(0)
    assert restored.timezone == "America/Bogota"


def test_parse_result_union_is_discriminated_by_status() -> None:
    adapter = TypeAdapter(ReminderParseResult)

    result = adapter.validate_python(
        {"status": "needs_clarification", "reason": "ambiguous_hour", "timezone": "UTC"}
    )

    assert isinstance(result, ReminderNeedsClarification)
    assert result.reason == ReminderClarificationReason.ambiguous_hour


def test_extraction_rejects_non_iana_timezone() -> None:
    with pytest.raises(ValidationError, match="valid IANA timezone"):
        ReminderExtraction(
            title="clase",
            timezone="UTC-5",
            starts_at=datetime(2026, 6, 20, 22, tzinfo=UTC),
            confidence=0.9,
        )
