"""Centralized user-facing Spanish replies for application workflows."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime


class AssistantReplies:
    """Build user-facing copy without mixing it into business decisions."""

    def start(self) -> str:
        return "Asistente personal activo. Usa /help para ver comandos."

    def help(self) -> str:
        return "\n".join(
            [
                "Comandos disponibles:",
                "/start - inicia la conversación.",
                "/help - muestra esta ayuda.",
                "/recordar <texto> - crea un recordatorio con aprobación.",
                "/agenda - lista eventos locales.",
                "/pendientes - muestra aprobaciones pendientes.",
                "/aprobar <id> - aprueba una acción pendiente.",
                "/cancelar <id> - cancela una aprobación pendiente.",
                "/status - muestra el estado local del asistente.",
            ]
        )

    def unsupported(self) -> str:
        return "No reconocí ese comando. Usa /help para ver opciones."

    def status(self, *, pending_count: int, state_count: int, event_count: int, outbox_count: int) -> str:
        return (
            "Estado local: activo. "
            f"Pendientes: {pending_count}. Workflows: {state_count}. Eventos: {event_count}. Outbox: {outbox_count}."
        )

    def agenda_empty(self) -> str:
        return "No hay eventos locales registrados."

    def agenda(self, rows: Iterable[tuple[datetime, str, str]]) -> str:
        lines = ["Agenda local:"]
        for starts_at, title, event_id in rows:
            lines.append(f"- {starts_at.isoformat()} | {title} ({event_id})")
        return "\n".join(lines)

    def pending_empty(self) -> str:
        return "No tienes aprobaciones pendientes."

    def pending_approvals(self, rows: Iterable[tuple[str, str, str]]) -> str:
        lines = ["Aprobaciones pendientes:"]
        for approval_id, action, request_text in rows:
            lines.append(f"- {approval_id}: {action} para '{request_text}'")
        lines.append("Usa /aprobar <id> o /cancelar <id>.")
        return "\n".join(lines)

    def reminder_missing_text(self) -> str:
        return "Indica qué quieres recordar: /recordar <texto>"

    def reminder_duplicate(self) -> str:
        return "Ya tenía ese recordatorio registrado."

    def reminder_needs_datetime(self) -> str:
        return "Necesito una fecha y hora claras para crear el recordatorio."

    def reminder_needs_approval(self, title: str) -> str:
        return f"Puedo crear '{title}', pero necesito aprobación para escribir en calendario."

    def reminder_created(self, *, title: str, minutes_before: int, direct_notice: bool = False) -> str:
        if direct_notice:
            return f"Listo. Te recordaré {title} en el momento indicado."
        return f"Listo. Te recordaré {title} {self._minutes_label(minutes_before)} antes."

    def approve_missing_id(self) -> str:
        return "Indica el id: /aprobar <id>"

    def approval_not_found(self) -> str:
        return "No encontré esa aprobación."

    def approval_type_unsupported(self) -> str:
        return "Ese tipo de aprobación todavía no está soportado."

    def cancel_missing_id(self) -> str:
        return "Indica el id: /cancelar <id>"

    def approval_cancelled(self) -> str:
        return "Aprobación cancelada."

    def telegram_audio_missing_file_id(self) -> str:
        return "Recibí un audio, pero Telegram no envió un file_id utilizable."

    def telegram_transcription_not_configured(self) -> str:
        return (
            "Recibí tu audio, pero falta configurar transcripción. "
            "Activa TRANSCRIPTION_PROVIDER y TRANSCRIPTION_API_KEY en el backend."
        )

    def telegram_token_missing_for_audio(self) -> str:
        return "Recibí tu audio, pero falta TELEGRAM_BOT_TOKEN para descargarlo desde Telegram."

    def telegram_audio_too_large(self) -> str:
        return "El audio supera el límite local de 20MB."

    def telegram_audio_download_too_large(self) -> str:
        return "El audio descargado supera el límite local de 20MB."

    def telegram_file_path_missing(self) -> str:
        return "No pude resolver el archivo de audio en Telegram."

    def telegram_transcription_failed(self) -> str:
        return "No pude transcribir ese audio. Intenta reenviarlo o escribe el recordatorio en texto."

    def _minutes_label(self, minutes: int) -> str:
        return "1 minuto" if minutes == 1 else f"{minutes} minutos"
