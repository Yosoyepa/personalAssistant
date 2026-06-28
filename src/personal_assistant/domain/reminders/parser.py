"""Pure reminder extraction rules."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta

from personal_assistant.domain.reminders.models import ReminderExtraction


SPANISH_WEEKDAYS = {
    "lunes": 0,
    "martes": 1,
    "miercoles": 2,
    "miércoles": 2,
    "jueves": 3,
    "viernes": 4,
    "sabado": 5,
    "sábado": 5,
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
TITLE_STOPWORDS_RE = (
    r"\b(recu[eé]rdame|recuerdame|recuerdes|recordarme|recordatorio|ag[eé]ndame|"
    r"agendame|agendarme|agenda|agendar|necesito|quiero|que|me|el|la|los|las|"
    r"este|esta|de|para)\b"
)


def _fold_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _next_weekday(now: datetime, target_weekday: int, *, hour: int, minute: int) -> datetime:
    days_ahead = (target_weekday - now.weekday()) % 7
    candidate = (now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=days_ahead)).replace(
        hour=hour,
        minute=minute,
    )
    if candidate <= now:
        candidate = candidate + timedelta(days=7)
    return candidate


def _today_or_tomorrow(now: datetime, *, hour: int, minute: int) -> datetime:
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate = candidate + timedelta(days=1)
    return candidate


def _clean_title(text: str) -> str:
    title = re.sub(TITLE_STOPWORDS_RE, " ", text, flags=re.I)
    for day in SPANISH_WEEKDAYS:
        title = re.sub(day, " ", title, flags=re.I)
    return re.sub(r"\s+", " ", title).strip(" .,:;-") or "Recordatorio"


def _parse_amount(raw: str) -> int | None:
    if raw.isdigit():
        return int(raw)
    return SPANISH_NUMBER_WORDS.get(raw)


def extract_reminder(text: str, now: datetime) -> ReminderExtraction | None:
    lowered = _fold_text(text)
    if not any(trigger in lowered for trigger in REMINDER_TRIGGERS):
        return None

    relative_match = re.search(
        r"\b(?:en|dentro\s+de)\s+(\d{1,4}|[a-z]+)\s*(minutos?|mins?|horas?|h)\b",
        lowered,
    )
    if relative_match is not None:
        amount = _parse_amount(relative_match.group(1))
        if amount is None or amount < 1:
            return None
        unit = relative_match.group(2)
        minutes = amount * 60 if unit.startswith(("hora", "h")) else amount
        starts_at = now + timedelta(minutes=minutes)
        title_source = re.sub(
            r"\b(?:en|dentro\s+de)\s+(?:\d{1,4}|[a-z]+)\s*(?:minutos?|mins?|horas?|h)\b",
            " ",
            text,
            flags=re.I,
        )
        return ReminderExtraction(title=_clean_title(title_source), starts_at=starts_at, notify_at=starts_at, confidence=0.88)

    weekday = next((value for name, value in SPANISH_WEEKDAYS.items() if _fold_text(name) in lowered), None)
    hour_match = re.search(r"\b(?:a las|las|a)\s+(\d{1,2})(?::(\d{2}))?\b", lowered)
    if hour_match is None:
        return None

    hour = int(hour_match.group(1))
    minute = int(hour_match.group(2) or 0)
    if not 0 <= minute <= 59:
        return None
    if "pm" in lowered and hour < 12:
        hour += 12
    if "am" in lowered and hour == 12:
        hour = 0
    if "am" not in lowered and "pm" not in lowered and weekday is None and 1 <= hour <= 7:
        possible_pm = hour + 12
        if possible_pm <= 23 and now.replace(hour=possible_pm, minute=minute, second=0, microsecond=0) > now:
            hour = possible_pm
    if not 0 <= hour <= 23:
        return None

    starts_at = (
        _next_weekday(now, weekday, hour=hour, minute=minute)
        if weekday is not None
        else _today_or_tomorrow(now, hour=hour, minute=minute)
    )
    title = re.sub(r"\b(?:a las|las|a)\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?\b", " ", text, flags=re.I)
    return ReminderExtraction(title=_clean_title(title), starts_at=starts_at, confidence=0.86)
