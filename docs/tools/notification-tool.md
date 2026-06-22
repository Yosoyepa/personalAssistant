# Tool Contract: notification.send

## Purpose

Send a user notification only after communication approval. The local adapter is
used for tests; Telegram/WhatsApp dispatchers can implement the same contract.

Application port: `src/personal_assistant/application/ports/notifications.py`  
Local adapter: `src/personal_assistant/adapters/outbound/notifications/local.py`

## Input Schema

- `channel: string`
- `recipient: string`
- `body: string`
- `send_at: datetime | null`
- `idempotency_key: string`

## Output Schema

- `notification_id: string`
- `channel: string`
- `recipient: string`
- `idempotency_key: string`
- `reused: boolean`

## Side Effects

Communication.

## Permission Tier

`P5`

## Preconditions

- `tenant_id` comes from `Principal`.
- Caller has at least P5 permission tier.
- Trusted P5 `ApprovalGrant` is supplied by the runtime approval service.
- Recipient is the approved recipient.

## Postconditions

- Notification is scoped to the principal tenant.
- Duplicate idempotency key returns the original notification.
- Tool call is traced and auditable.

## Failure Cases

- Missing or untrusted approval grant: fail closed.
- Permission tier too low: fail closed.
- Dispatcher unavailable: retry if idempotent.

## Audit Requirements

Record tenant, principal, recipient summary, idempotency key, approval status, and
trace id. Never log secrets or opaque approval token values.
