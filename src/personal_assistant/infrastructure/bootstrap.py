"""Application composition for the local-first assistant MVP."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Any

from personal_assistant.adapters.observability.local import TraceRecorder
from personal_assistant.adapters.outbound.calendar.local import LocalCalendarTool
from personal_assistant.adapters.outbound.llm.anthropic import (
    AnthropicCompatibleLLMProvider,
)
from personal_assistant.adapters.outbound.llm.minimax import MiniMaxLLMProvider
from personal_assistant.adapters.outbound.notifications.local import (
    LocalNotificationTool,
)
from personal_assistant.adapters.outbound.scheduler.local import ReminderScheduler
from personal_assistant.adapters.outbound.transcription.openai_compatible import (
    OpenAICompatibleTranscriptionProvider,
)
from personal_assistant.adapters.outbound.tts.minimax import MiniMaxTTSProvider
from personal_assistant.application.ports.approvals import ApprovalStorePort
from personal_assistant.application.ports.calendar import CalendarPort
from personal_assistant.application.ports.events import EventStorePort, OutboxPort
from personal_assistant.application.ports.notifications import NotificationPort
from personal_assistant.application.ports.observability import TraceRecorderPort
from personal_assistant.application.ports.prompts import PromptCatalogPort
from personal_assistant.application.ports.scheduler import ReminderSchedulerWorkerPort
from personal_assistant.application.ports.services import (
    AudioSynthesisProvider,
    AudioTranscriptionProvider,
    LLMProvider,
    MemoryPort,
)
from personal_assistant.application.ports.workflow_state import WorkflowStateStorePort
from personal_assistant.application.services.replies import AssistantReplies
from personal_assistant.application.use_cases.commands import ConversationCommandService
from personal_assistant.application.use_cases.documents import DocumentService
from personal_assistant.application.use_cases.reminder_notifications import (
    DispatchDueReminders,
)
from personal_assistant.application.use_cases.reminders import ReminderWorkflow
from personal_assistant.adapters.persistence.in_memory import (
    InMemoryApprovalStore,
    InMemoryEventStore,
    InMemoryOutbox,
    InMemoryWorkflowStateStore,
)
from personal_assistant.adapters.persistence.memory import TenantMemoryStore
from personal_assistant.infrastructure.config import (
    AppSettings,
    load_database_settings_from_env,
    load_persistence_settings_from_env,
)
from personal_assistant.infrastructure.prompts import build_prompt_catalog
from personal_assistant.infrastructure.worker import (
    ReminderWorker,
    RuntimeNotificationApprovalPolicy,
)


@dataclass(slots=True)
class PersistenceAdapters:
    approvals: ApprovalStorePort
    calendar: CalendarPort
    event_store: EventStorePort
    memory: MemoryPort
    outbox: OutboxPort
    scheduler: ReminderSchedulerWorkerPort
    states: WorkflowStateStorePort
    traces: TraceRecorderPort


@dataclass(slots=True)
class AppContainer:
    approvals: ApprovalStorePort
    calendar: CalendarPort
    commands: ConversationCommandService
    documents: DocumentService
    event_store: EventStorePort
    llm: LLMProvider | None
    memory: MemoryPort
    notifications: NotificationPort
    outbox: OutboxPort
    prompt_catalog: PromptCatalogPort
    reminder_notifications: DispatchDueReminders
    reminder_worker: ReminderWorker
    reminder_workflow: ReminderWorkflow
    scheduler: ReminderSchedulerWorkerPort
    states: WorkflowStateStorePort
    transcription: AudioTranscriptionProvider | None
    tts: AudioSynthesisProvider | None
    traces: TraceRecorderPort


def build_persistence_adapters(
    *,
    settings: AppSettings | None = None,
    persistence_backend: str | None = None,
    database_url: str | None = None,
    database_schema: str | None = None,
) -> PersistenceAdapters:
    env_backend = "memory"
    env_database_url: str | None = None
    if settings is None and (persistence_backend is None or database_url is None):
        env_backend, env_database_url = load_persistence_settings_from_env()

    selected_backend = persistence_backend
    if selected_backend is None:
        selected_backend = (
            settings.persistence_backend if settings is not None else env_backend
        )
    selected_database_url = database_url
    if selected_database_url is None:
        selected_database_url = (
            settings.database_url if settings is not None else env_database_url
        )
    selected_database_schema = database_schema
    if selected_database_schema is None:
        selected_database_schema = (
            settings.database_schema if settings is not None else None
        )

    backend = selected_backend.strip().lower() or "memory"
    if backend == "memory":
        return _build_in_memory_persistence()
    if backend == "postgres":
        if selected_database_schema is None:
            _, selected_database_schema = load_database_settings_from_env()
        return _build_postgres_persistence(
            selected_database_url,
            schema=selected_database_schema,
        )
    raise ValueError(f"unsupported PERSISTENCE_BACKEND: {selected_backend}")


def _build_in_memory_persistence() -> PersistenceAdapters:
    return PersistenceAdapters(
        approvals=InMemoryApprovalStore(),
        calendar=LocalCalendarTool(),
        event_store=InMemoryEventStore(),
        memory=TenantMemoryStore(),
        outbox=InMemoryOutbox(),
        scheduler=ReminderScheduler(),
        states=InMemoryWorkflowStateStore(),
        traces=TraceRecorder(),
    )


def _build_postgres_persistence(
    database_url: str | None,
    *,
    schema: str,
) -> PersistenceAdapters:
    if database_url is None or not database_url.strip():
        raise ValueError("DATABASE_URL is required when PERSISTENCE_BACKEND=postgres")
    try:
        module = import_module("personal_assistant.adapters.persistence.postgres")
    except ModuleNotFoundError as exc:
        missing_module = exc.name or "unknown"
        if missing_module == "personal_assistant.adapters.persistence.postgres":
            raise RuntimeError(
                "PERSISTENCE_BACKEND=postgres was selected, but "
                "personal_assistant.adapters.persistence.postgres is not available yet"
            ) from exc
        raise RuntimeError(
            "PERSISTENCE_BACKEND=postgres could not import "
            "personal_assistant.adapters.persistence.postgres because dependency "
            f"{missing_module!r} is missing. Install the optional postgres extra, "
            "for example: pip install 'personal-assistant[postgres]'."
        ) from exc

    factory = getattr(module, "build_postgres_persistence", None)
    if callable(factory):
        return _coerce_persistence_adapters(
            factory(database_url=database_url, schema=schema)
        )

    persistence_class = getattr(module, "PostgresPersistence", None)
    if callable(persistence_class):
        return _coerce_persistence_adapters(
            persistence_class(dsn=database_url, schema=schema)
        )

    raise RuntimeError(
        "personal_assistant.adapters.persistence.postgres must expose "
        "build_postgres_persistence(database_url=..., schema=...) or "
        "PostgresPersistence(dsn=..., schema=...)"
    )


def _coerce_persistence_adapters(candidate: Any) -> PersistenceAdapters:
    return PersistenceAdapters(
        approvals=_persistence_member(candidate, "approvals"),
        calendar=_persistence_member(candidate, "calendar"),
        event_store=_persistence_member(candidate, "event_store"),
        memory=_persistence_member(candidate, "memory"),
        outbox=_persistence_member(candidate, "outbox"),
        scheduler=_persistence_member(candidate, "scheduler"),
        states=_persistence_member(candidate, "states"),
        traces=_persistence_member(candidate, "traces"),
    )


def _persistence_member(candidate: Any, name: str) -> Any:
    if isinstance(candidate, dict):
        if name in candidate:
            return candidate[name]
    elif hasattr(candidate, name):
        return getattr(candidate, name)
    raise RuntimeError(f"Postgres persistence adapter is missing {name!r}")


def build_llm_provider(
    settings: AppSettings,
    *,
    prompt_catalog: PromptCatalogPort | None = None,
) -> LLMProvider | None:
    if settings.llm_provider in {"", "disabled", "none"}:
        return None
    prompts = prompt_catalog or build_prompt_catalog()
    if settings.llm_provider in {"minimax", "minimax_anthropic", "minimax-anthropic"}:
        return MiniMaxLLMProvider(
            api_key=settings.llm_api_key or "",
            prompt_catalog=prompts,
            base_url=settings.llm_base_url or "",
            model=settings.llm_model or "",
            timeout_seconds=settings.llm_timeout_seconds,
        )
    if settings.llm_provider not in {
        "anthropic_compatible",
        "anthropic-compatible",
        "aerolink",
    }:
        raise ValueError(f"unsupported LLM_PROVIDER: {settings.llm_provider}")
    return AnthropicCompatibleLLMProvider(
        api_key=settings.llm_api_key or "",
        base_url=settings.llm_base_url or "",
        model=settings.llm_model or "",
        prompt_catalog=prompts,
        anthropic_version=settings.llm_anthropic_version,
        auth_header=settings.llm_auth_header,
        timeout_seconds=settings.llm_timeout_seconds,
    )


def build_transcription_provider(
    settings: AppSettings,
) -> AudioTranscriptionProvider | None:
    if settings.transcription_provider in {"", "disabled", "none"}:
        return None
    if settings.transcription_provider not in {
        "openai_compatible",
        "openai-compatible",
    }:
        raise ValueError(
            f"unsupported TRANSCRIPTION_PROVIDER: {settings.transcription_provider}"
        )
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
        base_url=settings.tts_base_url or "",
        model=settings.tts_model or "",
        voice_id=settings.tts_voice_id,
        audio_format=settings.tts_audio_format,
        timeout_seconds=settings.tts_timeout_seconds,
    )


def build_container(
    *,
    settings: AppSettings | None = None,
    persistence_backend: str | None = None,
    database_url: str | None = None,
    database_schema: str | None = None,
    llm: LLMProvider | None = None,
    notifications: NotificationPort | None = None,
    transcription: AudioTranscriptionProvider | None = None,
    tts: AudioSynthesisProvider | None = None,
    prompt_catalog: PromptCatalogPort | None = None,
    approve_reminder_notifications: bool = False,
    reminder_minutes_before: int = 30,
) -> AppContainer:
    """Build application adapters for local development, tests, and runtime startup."""
    notification_adapter = notifications or LocalNotificationTool()
    prompts = prompt_catalog or build_prompt_catalog()
    replies = (
        AssistantReplies(locale=settings.reply_locale)
        if settings is not None
        else AssistantReplies()
    )
    persistence = build_persistence_adapters(
        settings=settings,
        persistence_backend=persistence_backend
        or ("memory" if settings is None else None),
        database_url=database_url,
        database_schema=database_schema,
    )
    approvals = persistence.approvals
    calendar = persistence.calendar
    event_store = persistence.event_store
    outbox = persistence.outbox
    scheduler = persistence.scheduler
    states = persistence.states
    traces = persistence.traces
    reminder_notifications = DispatchDueReminders(
        scheduler=scheduler, notifications=notification_adapter
    )
    reminder_workflow = ReminderWorkflow(
        calendar=calendar,
        scheduler=scheduler,
        event_store=event_store,
        outbox=outbox,
        states=states,
        traces=traces,
        llm=llm,
        prompt_catalog=prompts,
        replies=replies,
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
        prompt_catalog=prompts,
        traces=traces,
        replies=replies,
    )
    return AppContainer(
        approvals=approvals,
        calendar=calendar,
        commands=commands,
        documents=DocumentService(),
        event_store=event_store,
        llm=llm,
        memory=persistence.memory,
        notifications=notification_adapter,
        outbox=outbox,
        prompt_catalog=prompts,
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
