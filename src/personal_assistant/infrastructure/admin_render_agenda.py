"""Agenda and reminders section renderers for the admin dashboard."""

from __future__ import annotations

from typing import Any

from personal_assistant.infrastructure.admin_render_helpers import (
    _render_summary_cards,
    _render_table,
)


def _render_agenda(agenda: dict[str, Any]) -> str:
    next_event = agenda.get("next_event")
    cards = [
        {
            "label": "Calendar events",
            "value": agenda["total"],
            "detail": f'{agenda["upcoming_count"]} upcoming, {agenda["past_count"]} past',
        },
        {
            "label": "Today",
            "value": agenda["today_count"],
            "detail": "events matching generated date",
        },
        {
            "label": "Next event",
            "value": next_event["title"] if next_event else "None",
            "detail": next_event["starts_at"] if next_event else "No upcoming event",
            "tone": "ok" if next_event else "neutral",
        },
    ]
    return "\n".join(
        [
            '<section id="agenda">',
            '<div class="section-heading">',
            "<h2>Agenda</h2>",
            '<p class="section-note">Calendar events ordered by next action.</p>',
            "</div>",
            _render_summary_cards(cards),
            _render_table(
                ["starts_at", "status", "title", "event_id", "idempotency_key"],
                agenda["items"],
            ),
            "</section>",
        ]
    )


def _render_reminders(reminders: dict[str, Any]) -> str:
    counts = reminders["counts"]
    cards = [
        {
            "label": "Due",
            "value": counts.get("due", 0),
            "detail": "unsent reminders at or before now",
            "tone": "attention" if counts.get("due", 0) else "neutral",
        },
        {
            "label": "Pending",
            "value": counts.get("pending", 0),
            "detail": "scheduled for later",
        },
        {
            "label": "Sent",
            "value": counts.get("sent", 0),
            "detail": "already dispatched",
        },
        {
            "label": "Total",
            "value": reminders["total"],
            "detail": "scheduled reminder jobs",
        },
    ]
    return "\n".join(
        [
            '<section id="reminders">',
            '<div class="section-heading">',
            "<h2>Reminders</h2>",
            '<p class="section-note">Due and pending notifications are listed before sent jobs.</p>',
            "</div>",
            _render_summary_cards(cards),
            _render_table(
                [
                    "notify_at",
                    "status",
                    "event_title",
                    "event_starts_at",
                    "reminder_id",
                    "channel",
                    "recipient",
                    "body_preview",
                ],
                reminders["items"],
            ),
            "</section>",
        ]
    )
