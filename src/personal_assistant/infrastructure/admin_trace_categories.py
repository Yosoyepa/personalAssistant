"""Heuristic categorization of trace error events for the admin dashboard."""

from __future__ import annotations

import json

from personal_assistant.application.dto.tracing import TraceEvent, TraceEventType
from personal_assistant.infrastructure.admin_text import _lower_text


def _trace_error_category(event: TraceEvent) -> str:
    explicit = _trace_error_explicit_category(event)
    if explicit is not None:
        return explicit

    tool_name = _lower_text(event.tool_call.get("name"))
    run_id = event.run_id.lower()
    model = (event.model or "").lower()
    input_keys = {str(key).lower() for key in event.input_summary}
    input_text = " ".join(str(value).lower() for value in event.input_summary.values())
    error_text = json.dumps(event.error, default=str, sort_keys=True).lower() if event.error else ""

    if (
        "audio" in tool_name
        or "transcrib" in tool_name
        or "tts" in tool_name
        or "audio" in run_id
        or "transcription" in run_id
        or {"media_kind", "media_mime_type", "media_file_size", "transcription_filename"} & input_keys
        or "audio" in input_text
        or "audio" in error_text
        or "transcrib" in error_text
        or "tts" in error_text
    ):
        return "audio"
    if (
        event.event_type == TraceEventType.llm_called
        or bool(model)
        or {"schema", "prompt_id", "prompt_version"} & input_keys
        or (run_id.startswith("command:") and run_id.endswith(":intent"))
        or "llm" in error_text
        or "model" in error_text
    ):
        return "llm"
    if event.event_type == TraceEventType.tool_called or event.tool_call or "tool" in error_text:
        return "tool"
    if event.event_type == TraceEventType.agent_failed or "workflow" in input_keys or "workflow" in run_id or "workflow" in error_text:
        return "workflow"
    return "unknown"


def _trace_error_explicit_category(event: TraceEvent) -> str | None:
    for key in ("category", "component", "source"):
        value = _lower_text(event.error.get(key))
        for category in ("audio", "llm", "tool", "workflow"):
            if value == category or value.startswith(f"{category}."):
                return category
    return None
