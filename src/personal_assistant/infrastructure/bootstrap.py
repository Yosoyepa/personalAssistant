"""Application composition for the local-first assistant MVP."""

from __future__ import annotations

from dataclasses import dataclass

from personal_assistant.adapters.outbound.calendar.local import LocalCalendarTool
from personal_assistant.adapters.outbound.llm.anthropic import AnthropicCompatibleLLMProvider
from personal_assistant.adapters.outbound.llm.minimax import MiniMaxLLMProvider
from personal_assistant.adapters.outbound.transcription.openai_compatible import OpenAICompatibleTranscriptionProvider
from personal_assistant.adapters.outbound.tts.minimax import MiniMaxTTSProvider
from personal_assistant.application.ports.notifications import NotificationPort
from personal_assistant.application.ports.services import AudioSynthesisProvider, AudioTranscriptionProvider, LLMProvider
from personal_assistant.application.use_cases.commands import ConversationCommandService
from personal_assistant.application.use_cases.documents import DocumentService
from personal_assistant.application.use_cases.reminder_notifications import DispatchDueReminders
from personal_assistant.application.use_cases.reminders import ReminderWorkflow
from personal_assistant.adapters.observability.local import TraceRecorder
from personal_assistant.adapters.persistence.in_memory import (
    InMemoryApprovalStore,
    InMemoryEventStore,
    InMemoryOutbox,
    InMemoryWorkflowStateStore,
)
from personal_assistant.adapters.persistence.memory import TenantMemoryStore
from personal_assistant.adapters.outbound.notifications.local import LocalNotificationTool
from personal_assistant.adapters.outbound.scheduler.local import ReminderScheduler
from personal_assistant.infrastructure.config import AppSettings
from personal_assistant.infrastructure.worker import ReminderWorker, RuntimeNotificationApprovalPolicy


@dataclass(slots=True)
class AppContainer:
    approvals: InMemoryApprovalStore
    calendar: LocalCalendarTool
    commands: ConversationCommandService
    documents: DocumentService
    event_store: InMemoryEventStore
    llm: LLMProvider | None
    memory: TenantMemoryStore
    notifications: NotificationPort
    outbox: InMemoryOutbox
    reminder_notifications: DispatchDueReminders
    reminder_worker: ReminderWorker
    reminder_workflow: ReminderWorkflow
    scheduler: ReminderScheduler
    states: InMemoryWorkflowStateStore
    transcription: AudioTranscriptionProvider | None
    tts: AudioSynthesisProvider | None
    traces: TraceRecorder


def build_llm_provider(settings: AppSettings) -> LLMProvider | None:
    if settings.llm_provider in {"", "disabled", "none"}:
        return None
    if settings.llm_provider in {"minimax", "minimax_anthropic", "minimax-anthropic"}:
        return MiniMaxLLMProvider(
            api_key=settings.llm_api_key or "",
            base_url=settings.llm_base_url or "https://api.minimax.io/anthropic",
            model=settings.llm_model or "MiniMax-M3",
            timeout_seconds=settings.llm_timeout_seconds,
        )
    if settings.llm_provider not in {"anthropic_compatible", "anthropic-compatible", "aerolink"}:
        raise ValueError(f"unsupported LLM_PROVIDER: {settings.llm_provider}")
    return AnthropicCompatibleLLMProvider(
        api_key=settings.llm_api_key or "",
        base_url=settings.llm_base_url or "",
        model=settings.llm_model or "",
        anthropic_version=settings.llm_anthropic_version,
        auth_header=settings.llm_auth_header,
        timeout_seconds=settings.llm_timeout_seconds,
    )


def build_transcription_provider(settings: AppSettings) -> AudioTranscriptionProvider | None:
    if settings.transcription_provider in {"", "disabled", "none"}:
        return None
    if settings.transcription_provider not in {"openai_compatible", "openai-compatible"}:
        raise ValueError(f"unsupported TRANSCRIPTION_PROVIDER: {settings.transcription_provider}")
    return OpenAICompatibleTranscriptionProvider(
        api_key=settings.transcription_api_key or "",
        base_url=settings.transcription_base_url or "",
        model=settings.transcription_model or "",
        timeout_seconds=settings.transcription_timeout_seconds,
    )


def build_tts_provider(settings: AppSettings) -> AudioSynthesisProvider | None:
    if settings.tts_provider in {"", "disabled", "none"}:
        return None
    if settings.tts_provider not in {"minimax", "minimax_tts", "minimax-tts"}:
        raise ValueError(f"unsupported TTS_PROVIDER: {settings.tts_provider}")
    return MiniMaxTTSProvider(
        api_key=settings.tts_api_key or "",
        base_url=settings.tts_base_url or "https://api.minimax.io",
        model=settings.tts_model or "speech-2.8-turbo",
        voice_id=settings.tts_voice_id,
        audio_format=settings.tts_audio_format,
        timeout_seconds=settings.tts_timeout_seconds,
    )


def build_container(
    *,
    llm: LLMProvider | None = None,
    notifications: NotificationPort | None = None,
    transcription: AudioTranscriptionProvider | None = None,
    tts: AudioSynthesisProvider | None = None,
    approve_reminder_notifications: bool = False,
    reminder_minutes_before: int = 30,
) -> AppContainer:
    """Build in-memory adapters for local development and tests."""
    notification_adapter = notifications or LocalNotificationTool()
    approvals = InMemoryApprovalStore()
    calendar = LocalCalendarTool()
    event_store = InMemoryEventStore()
    outbox = InMemoryOutbox()
    scheduler = ReminderScheduler()
    states = InMemoryWorkflowStateStore()
    traces = TraceRecorder()
    reminder_notifications = DispatchDueReminders(scheduler=scheduler, notifications=notification_adapter)
    reminder_workflow = ReminderWorkflow(
        calendar=calendar,
        scheduler=scheduler,
        event_store=event_store,
        outbox=outbox,
        states=states,
        traces=traces,
        llm=llm,
        reminder_minutes_before=reminder_minutes_before,
    )
    commands = ConversationCommandService(
        approvals=approvals,
        calendar=calendar,
        reminder_workflow=reminder_workflow,
        states=states,
        event_store=event_store,
        outbox=outbox,
        llm=llm,
        traces=traces,
    )
    return AppContainer(
        approvals=approvals,
        calendar=calendar,
        commands=commands,
        documents=DocumentService(),
        event_store=event_store,
        llm=llm,
        memory=TenantMemoryStore(),
        notifications=notification_adapter,
        outbox=outbox,
        reminder_notifications=reminder_notifications,
        reminder_worker=ReminderWorker(
            dispatcher=reminder_notifications,
            approval_policy=RuntimeNotificationApprovalPolicy(
                approve_notifications=approve_reminder_notifications,
            ),
        ),
        reminder_workflow=reminder_workflow,
        scheduler=scheduler,
        states=states,
        transcription=transcription,
        tts=tts,
        traces=traces,
    )
