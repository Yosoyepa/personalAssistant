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


def extract_reminder(text: str, now: datetime) -> ReminderExtraction | None:
    lowered = _fold_text(text)
    if "recuerd" not in lowered:
        return None

    weekday = next((value for name, value in SPANISH_WEEKDAYS.items() if _fold_text(name) in lowered), None)
    hour_match = re.search(r"\b(?:a las|las|a)\s+(\d{1,2})(?::(\d{2}))?\b", lowered)
    if weekday is None or hour_match is None:
        return None

    hour = int(hour_match.group(1))
    minute = int(hour_match.group(2) or 0)
    if not 0 <= minute <= 59:
        return None
    if "pm" in lowered and hour < 12:
        hour += 12
    if "am" in lowered and hour == 12:
        hour = 0
    if not 0 <= hour <= 23:
        return None

    starts_at = _next_weekday(now, weekday, hour=hour, minute=minute)
    title = re.sub(r"\b(recu[eé]rdame|recordarme|el|la|los|las|a|este|esta)\b", " ", text, flags=re.I)
    for day in SPANISH_WEEKDAYS:
        title = re.sub(day, " ", title, flags=re.I)
    title = re.sub(r"\s+", " ", title).strip(" .,:;-") or "Recordatorio"
    return ReminderExtraction(title=title, starts_at=starts_at, confidence=0.86)
