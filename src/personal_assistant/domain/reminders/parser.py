"""Pure, timezone-safe reminder extraction rules."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from personal_assistant.domain.reminders.models import (
    ParsedReminder,
    ReminderClarificationReason,
    ReminderExtraction,
    ReminderNeedsClarification,
    ReminderParseResult,
    ReminderUnsupportedReason,
    UnsupportedReminder,
)


SPANISH_WEEKDAYS = {
    "lunes": 0,
    "martes": 1,
    "miercoles": 2,
    "jueves": 3,
    "viernes": 4,
    "sabado": 5,
    "domingo": 6,
}
REMINDER_TRIGGERS = ("recuerd", "record", "agend", "cita", "recordatorio")
SPANISH_NUMBER_WORDS = {
    "un": 1,
    "una": 1,
    "uno": 1,
    "dos": 2,
    "tres": 3,
    "cuatro": 4,
    "cinco": 5,
    "seis": 6,
    "siete": 7,
    "ocho": 8,
    "nueve": 9,
    "diez": 10,
    "once": 11,
    "doce": 12,
    "quince": 15,
    "treinta": 30,
    "sesenta": 60,
}

RELATIVE_RE = re.compile(
    r"\b(?:en|dentro\s+de)\s+(\d{1,4}|[a-z]+)\s*(minutos?|mins?|horas?|h)\b",
    re.IGNORECASE,
)
WALL_CLOCK_RE = re.compile(
    r"\b(?:a\s+las?|las?|a)\s+"
    r"(?P<hour>\d{1,2})(?::(?P<minute>\d*))?"
    r"(?:\s*(?P<meridiem>(?:a|p)\s*\.?\s*m\.?))?(?![:\w])",
    re.IGNORECASE,
)
WALL_CLOCK_CANDIDATE_RE = re.compile(
    r"\b(?:a\s+las?|las?|a)\s+\d[^\s,;.!?)\]}]*", re.IGNORECASE
)
MIDNIGHT_RE = re.compile(r"\b(?:a\s+la\s+)?medianoche\b", re.IGNORECASE)
NOON_RE = re.compile(r"\b(?:al\s+)?mediodia\b", re.IGNORECASE)
TITLE_STOPWORDS_RE = re.compile(
    r"\b(recu[eé]rdame|recuerdame|recuerdes|recordarme|recordatorio|ag[eé]ndame|"
    r"agendame|agendarme|agenda|agendar|necesito|quiero|que|me|el|la|los|las|"
    r"este|esta|de|para)\b",
    re.IGNORECASE,
)
TITLE_DATE_RE = re.compile(
    r"\b(hoy|ma[nñ]ana|lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class _ClockTime:
    hour: int
    minute: int


def _fold_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _clean_title(text: str) -> str:
    title = RELATIVE_RE.sub(" ", text)
    title = WALL_CLOCK_RE.sub(" ", title)
    title = MIDNIGHT_RE.sub(" ", title)
    title = re.sub(r"\b(?:al\s+)?mediod[ií]a\b", " ", title, flags=re.IGNORECASE)
    title = TITLE_DATE_RE.sub(" ", title)
    title = TITLE_STOPWORDS_RE.sub(" ", title)
    return re.sub(r"\s+", " ", title).strip(" .,:;-") or "Recordatorio"


def _parse_amount(raw: str) -> int | None:
    if raw.isdigit():
        return int(raw)
    return SPANISH_NUMBER_WORDS.get(raw)


def _load_timezone(timezone: str) -> ZoneInfo | None:
    try:
        return ZoneInfo(timezone.strip())
    except (ValueError, ZoneInfoNotFoundError):
        return None


def _parse_clock(
    folded_text: str,
    *,
    timezone: str,
) -> _ClockTime | ReminderNeedsClarification | UnsupportedReminder | None:
    clock_candidates = tuple(WALL_CLOCK_CANDIDATE_RE.finditer(folded_text))
    midnight_matches = tuple(MIDNIGHT_RE.finditer(folded_text))
    noon_matches = tuple(NOON_RE.finditer(folded_text))
    if len(clock_candidates) + len(midnight_matches) + len(noon_matches) > 1:
        return UnsupportedReminder(
            reason=ReminderUnsupportedReason.conflicting_temporal_expression
        )

    if midnight_matches:
        return _ClockTime(hour=0, minute=0)
    if noon_matches:
        return _ClockTime(hour=12, minute=0)

    if not clock_candidates:
        return None

    candidate = clock_candidates[0]
    match = WALL_CLOCK_RE.search(folded_text)
    if (
        match is None
        or match.start() != candidate.start()
        or match.end() < candidate.end()
    ):
        return UnsupportedReminder(reason=ReminderUnsupportedReason.invalid_time)
    hour = int(match.group("hour"))
    minute_raw = match.group("minute")
    if minute_raw is not None and len(minute_raw) != 2:
        return UnsupportedReminder(reason=ReminderUnsupportedReason.invalid_time)
    minute = int(minute_raw or 0)
    meridiem_raw = match.group("meridiem")
    if not 0 <= minute <= 59:
        return UnsupportedReminder(reason=ReminderUnsupportedReason.invalid_time)

    if meridiem_raw is not None:
        if not 1 <= hour <= 12:
            return UnsupportedReminder(reason=ReminderUnsupportedReason.invalid_time)
        meridiem = meridiem_raw.replace(".", "").replace(" ", "")
        if meridiem == "am":
            hour = 0 if hour == 12 else hour
        else:
            hour = 12 if hour == 12 else hour + 12
    elif 1 <= hour <= 12:
        return ReminderNeedsClarification(
            reason=ReminderClarificationReason.ambiguous_hour,
            timezone=timezone,
        )
    elif not 0 <= hour <= 23:
        return UnsupportedReminder(reason=ReminderUnsupportedReason.invalid_time)

    return _ClockTime(hour=hour, minute=minute)


def _utc_candidates(
    local_datetime: datetime, timezone: ZoneInfo
) -> tuple[datetime, ...]:
    candidates: set[datetime] = set()
    for fold in (0, 1):
        local_candidate = local_datetime.replace(tzinfo=timezone, fold=fold)
        utc_candidate = local_candidate.astimezone(UTC)
        round_trip = utc_candidate.astimezone(timezone)
        if (
            round_trip.replace(tzinfo=None) == local_datetime
            and round_trip.fold == fold
        ):
            candidates.add(utc_candidate)
    return tuple(sorted(candidates))


def _weekdays_in(text: str) -> tuple[int, ...]:
    return tuple(
        weekday
        for name, weekday in SPANISH_WEEKDAYS.items()
        if re.search(rf"\b{re.escape(name)}\b", text) is not None
    )


def _has_date_selector(text: str) -> bool:
    return re.search(r"\b(?:hoy|manana)\b", text) is not None or bool(
        _weekdays_in(text)
    )


def _has_wall_clock(text: str) -> bool:
    return any(
        pattern.search(text) is not None
        for pattern in (
            WALL_CLOCK_CANDIDATE_RE,
            MIDNIGHT_RE,
            NOON_RE,
        )
    )


def _wall_clock_date(
    folded_text: str,
    *,
    clock: _ClockTime,
    reference_local: datetime,
) -> date | UnsupportedReminder | None:
    has_today = re.search(r"\bhoy\b", folded_text) is not None
    has_tomorrow = re.search(r"\bmanana\b", folded_text) is not None
    weekdays = _weekdays_in(folded_text)
    if len(weekdays) > 1:
        return UnsupportedReminder(
            reason=ReminderUnsupportedReason.conflicting_temporal_expression
        )
    weekday = weekdays[0] if weekdays else None
    selectors = int(has_today) + int(has_tomorrow) + int(weekday is not None)
    if selectors > 1:
        return UnsupportedReminder(
            reason=ReminderUnsupportedReason.conflicting_temporal_expression
        )

    reference_date = reference_local.date()
    if has_today:
        return reference_date
    if has_tomorrow:
        return reference_date + timedelta(days=1)

    if weekday is not None:
        days_ahead = (weekday - reference_local.weekday()) % 7
        target_date = reference_date + timedelta(days=days_ahead)
        local_candidate = datetime.combine(target_date, time(clock.hour, clock.minute))
        if local_candidate <= reference_local.replace(tzinfo=None):
            target_date += timedelta(days=7)
        return target_date
    return None


def extract_reminder(
    text: str,
    now: datetime,
    *,
    timezone: str = "America/Bogota",
) -> ReminderParseResult:
    """Extract one reminder without guessing an ambiguous local wall clock."""

    folded_text = _fold_text(text)
    if not any(trigger in folded_text for trigger in REMINDER_TRIGGERS):
        return UnsupportedReminder(reason=ReminderUnsupportedReason.not_a_reminder)
    if now.tzinfo is None or now.utcoffset() is None:
        return UnsupportedReminder(
            reason=ReminderUnsupportedReason.invalid_reference_instant
        )

    zone = _load_timezone(timezone)
    if zone is None:
        return ReminderNeedsClarification(
            reason=ReminderClarificationReason.invalid_timezone,
            timezone=timezone,
        )

    now_utc = now.astimezone(UTC)
    relative_matches = tuple(RELATIVE_RE.finditer(folded_text))
    if relative_matches:
        if len(relative_matches) > 1:
            return UnsupportedReminder(
                reason=ReminderUnsupportedReason.conflicting_temporal_expression
            )
        if _has_date_selector(folded_text) or _has_wall_clock(folded_text):
            return UnsupportedReminder(
                reason=ReminderUnsupportedReason.conflicting_temporal_expression
            )
        relative_match = relative_matches[0]
        amount = _parse_amount(relative_match.group(1))
        if amount is None or amount < 1:
            return UnsupportedReminder(
                reason=ReminderUnsupportedReason.invalid_relative_amount
            )
        unit = relative_match.group(2)
        minutes = amount * 60 if unit.startswith(("hora", "h")) else amount
        starts_at = now_utc + timedelta(minutes=minutes)
        return ParsedReminder(
            extraction=ReminderExtraction(
                title=_clean_title(text),
                timezone=zone.key,
                starts_at=starts_at,
                notify_at=starts_at,
                confidence=0.88,
            )
        )

    clock_result = _parse_clock(folded_text, timezone=zone.key)
    if isinstance(clock_result, (ReminderNeedsClarification, UnsupportedReminder)):
        return clock_result
    if clock_result is None:
        return ReminderNeedsClarification(
            reason=(
                ReminderClarificationReason.missing_time
                if _has_date_selector(folded_text)
                else ReminderClarificationReason.missing_datetime
            ),
            timezone=zone.key,
        )

    reference_local = now_utc.astimezone(zone)
    date_result = _wall_clock_date(
        folded_text,
        clock=clock_result,
        reference_local=reference_local,
    )
    if isinstance(date_result, UnsupportedReminder):
        return date_result
    if date_result is None:
        return ReminderNeedsClarification(
            reason=ReminderClarificationReason.missing_date,
            timezone=zone.key,
        )

    local_datetime = datetime.combine(
        date_result, time(clock_result.hour, clock_result.minute)
    )
    candidates = _utc_candidates(local_datetime, zone)
    if not candidates:
        return ReminderNeedsClarification(
            reason=ReminderClarificationReason.nonexistent_local_time,
            timezone=zone.key,
        )
    if len(candidates) > 1:
        return ReminderNeedsClarification(
            reason=ReminderClarificationReason.ambiguous_local_time,
            timezone=zone.key,
        )

    starts_at = candidates[0]
    if re.search(r"\bhoy\b", folded_text) is not None and starts_at <= now_utc:
        return UnsupportedReminder(reason=ReminderUnsupportedReason.past_time)

    return ParsedReminder(
        extraction=ReminderExtraction(
            title=_clean_title(text),
            timezone=zone.key,
            starts_at=starts_at,
            confidence=0.86,
        )
    )
