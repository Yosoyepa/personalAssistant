"""Multi-channel notification router implementing NotificationPort."""

from __future__ import annotations

from collections.abc import Mapping

from personal_assistant.application.ports.notifications import (
    NotificationPort,
    NotificationRequest,
    NotificationResult,
)
from personal_assistant.domain.common.identity import (
    Principal,
    require_trusted_principal,
)
from personal_assistant.domain.common.permissions import ApprovalGrant


class ChannelNotificationRouter:
    """Routes notification requests to channel-specific notification tools."""

    def __init__(self, channels: Mapping[str, NotificationPort]) -> None:
        self._channels = dict(channels)

    def send(
        self,
        principal: Principal,
        request: NotificationRequest,
        *,
        approval: ApprovalGrant | None = None,
    ) -> NotificationResult:
        tool = self._channels.get(request.channel)
        if tool is None:
            return NotificationResult(
                channel=request.channel,
                idempotency_key=request.idempotency_key,
                outcome="permanent",
                provider_code=400,
            )
        return tool.send(principal, request, approval=approval)

    def list_sent(self, principal: Principal) -> list[NotificationResult]:
        require_trusted_principal(principal)
        results: list[NotificationResult] = []
        for tool in self._channels.values():
            if hasattr(tool, "list_sent"):
                results.extend(tool.list_sent(principal))
        return results
