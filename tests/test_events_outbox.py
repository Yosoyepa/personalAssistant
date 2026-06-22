from __future__ import annotations

import unittest

from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode
from personal_assistant.application.dto.events import CloudEvent, OutboxStatus
from personal_assistant.domain.common.permissions import PermissionTier
from personal_assistant.domain.common.identity import Principal
from personal_assistant.adapters.persistence.in_memory import InMemoryEventStore, InMemoryOutbox


class EventOutboxTests(unittest.TestCase):
    def principal(self, tenant_id: str) -> Principal:
        return Principal.for_test(principal_id=f"user-{tenant_id}", tenant_id=tenant_id, permission_tier=PermissionTier.P2)

    def test_event_ids_are_deduplicated_per_tenant(self) -> None:
        store = InMemoryEventStore()
        tenant_a = self.principal("tenant-a")
        tenant_b = self.principal("tenant-b")
        event_a = CloudEvent(id="same", type="test.created", source="test", tenant_id="tenant-a", data={"value": "a"})
        event_b = CloudEvent(id="same", type="test.created", source="test", tenant_id="tenant-b", data={"value": "b"})

        store.append(tenant_a, event_a)
        store.append(tenant_b, event_b)

        self.assertEqual(len(store.list_for_tenant(tenant_a)), 1)
        self.assertEqual(len(store.list_for_tenant(tenant_b)), 1)

    def test_event_id_conflict_is_not_silent(self) -> None:
        store = InMemoryEventStore()
        tenant = self.principal("tenant-a")
        first = CloudEvent(id="same", type="test.created", source="test", tenant_id="tenant-a", data={"value": "a"})
        conflict = CloudEvent(id="same", type="test.created", source="test", tenant_id="tenant-a", data={"value": "b"})
        store.append(tenant, first)

        with self.assertRaises(AssistantError) as ctx:
            store.append(tenant, conflict)

        self.assertEqual(ctx.exception.code, ErrorCode.CONFLICT)

    def test_outbox_claim_publish_requires_claim_token(self) -> None:
        outbox = InMemoryOutbox()
        tenant = self.principal("tenant-a")
        event = CloudEvent(type="test.created", source="test", tenant_id="tenant-a", data={"value": "a"})
        message = outbox.add(tenant, event, idempotency_key="key-1")

        claimed = outbox.claim(tenant, owner="worker-a")
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0].dispatch_status, OutboxStatus.claimed)
        with self.assertRaises(AssistantError):
            outbox.mark_published(tenant, message.id, claim_token="wrong")

        published = outbox.mark_published(tenant, message.id, claim_token=claimed[0].claim_token or "")
        self.assertEqual(published.dispatch_status, OutboxStatus.published)
        self.assertEqual(outbox.claim(tenant), [])

    def test_outbox_idempotency_conflict_is_not_silent(self) -> None:
        outbox = InMemoryOutbox()
        tenant = self.principal("tenant-a")
        first = CloudEvent(type="test.created", source="test", tenant_id="tenant-a", data={"value": "a"})
        conflict = CloudEvent(type="test.created", source="test", tenant_id="tenant-a", data={"value": "b"})
        outbox.add(tenant, first, idempotency_key="key-1")

        with self.assertRaises(AssistantError) as ctx:
            outbox.add(tenant, conflict, idempotency_key="key-1")

        self.assertEqual(ctx.exception.code, ErrorCode.CONFLICT)


if __name__ == "__main__":
    unittest.main()

