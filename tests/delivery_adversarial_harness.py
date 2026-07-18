"""Deterministic test doubles for durable notification delivery."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from threading import Event, Lock

from personal_assistant.application.ports.notifications import (
    NotificationRequest,
    NotificationResult,
)
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import ApprovalGrant


class InjectedDeliveryCrash(BaseException):
    """A process-failure surrogate that intentionally bypasses ``Exception``."""


@dataclass(slots=True)
class FakeClock:
    """Thread-safe, manually advanced UTC clock; tests never wait on wall time."""

    current: datetime
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.current.tzinfo is None or self.current.utcoffset() is None:
            raise ValueError("fake clock requires a timezone-aware instant")
        self.current = self.current.astimezone(UTC)

    def __call__(self) -> datetime:
        with self._lock:
            return self.current

    def advance(self, delta: timedelta) -> datetime:
        if delta < timedelta(0):
            raise ValueError("fake clock cannot move backwards")
        with self._lock:
            self.current += delta
            return self.current


@dataclass(slots=True)
class FaultInjector:
    """Raises exactly at an armed named boundary, without timing races."""

    armed_at: str | None = None
    reached: list[str] = field(default_factory=list)

    def hit(self, point: str) -> None:
        self.reached.append(point)
        if point == self.armed_at:
            raise InjectedDeliveryCrash(f"injected crash at {point}")


ProviderObservation = Callable[[Principal, NotificationRequest], None]
ProviderOutcomeFactory = Callable[[NotificationRequest], NotificationResult]
ProviderScript = NotificationResult | BaseException | ProviderOutcomeFactory


@dataclass(frozen=True, slots=True)
class ProviderCall:
    """Non-secret provider invocation evidence."""

    tenant_id: str
    idempotency_key: str
    channel: str


@dataclass(slots=True)
class ScriptedNotificationProvider:
    """Finite fake provider with deterministic outcomes and call observations."""

    outcomes: Iterable[ProviderScript]
    observe_before_result: ProviderObservation | None = None
    calls: list[ProviderCall] = field(default_factory=list, init=False)
    _remaining: list[ProviderScript] = field(init=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        self._remaining = list(self.outcomes)

    def send(
        self,
        principal: Principal,
        request: NotificationRequest,
        *,
        approval: ApprovalGrant | None = None,
    ) -> NotificationResult:
        del approval
        with self._lock:
            self.calls.append(
                ProviderCall(
                    tenant_id=principal.tenant_id,
                    idempotency_key=request.idempotency_key,
                    channel=request.channel,
                )
            )
            if not self._remaining:
                raise InjectedDeliveryCrash(
                    "provider called more times than scripted"
                )
            outcome = self._remaining.pop(0)
        if self.observe_before_result is not None:
            self.observe_before_result(principal, request)
        if isinstance(outcome, BaseException):
            raise outcome
        if callable(outcome):
            return outcome(request)
        return outcome

    def assert_exhausted(self) -> None:
        assert self._remaining == []


@dataclass(slots=True)
class BlockingNotificationProvider:
    """Provider held at an explicit barrier until the test releases it."""

    result: NotificationResult | ProviderOutcomeFactory
    entered: Event = field(default_factory=Event, init=False)
    release: Event = field(default_factory=Event, init=False)
    calls: list[ProviderCall] = field(default_factory=list, init=False)

    def send(
        self,
        principal: Principal,
        request: NotificationRequest,
        *,
        approval: ApprovalGrant | None = None,
    ) -> NotificationResult:
        del approval
        self.calls.append(
            ProviderCall(
                tenant_id=principal.tenant_id,
                idempotency_key=request.idempotency_key,
                channel=request.channel,
            )
        )
        self.entered.set()
        if not self.release.wait(timeout=10):
            raise AssertionError("test did not release blocked provider")
        if callable(self.result):
            return self.result(request)
        return self.result
