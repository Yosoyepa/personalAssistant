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

- `notification_id: string | null` (present only for confirmed success)
- `channel: string`
- `idempotency_key: string`
- `outcome: success | known-transient | permanent | unknown-outcome`
- `provider_code: integer | null` (sanitized provider/HTTP code)
- `retry_after: positive integer | null` (sanitized seconds)
- `provider_message_id: positive integer | null` (confirmed success only)
- `reused: boolean`

The result never contains the recipient, notification body, media, provider
description/body, bot token, request URL, or raw exception details.

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
- Duplicate idempotency key with the same payload returns a cached terminal
  result; a different payload fails with a conflict.
- `unknown-outcome` is not sent again implicitly because delivery may already
  have happened.
- Tool call is traced and auditable.

## Failure Cases

- Missing or untrusted approval grant: fail closed.
- Permission tier too low: fail closed.
- Telegram `429` and explicit HTTP `5xx`: `known-transient`; a valid provider
  `Retry-After` is exposed for a later worker to compare with its own backoff.
- Telegram HTTP `4xx`, except `429`: `permanent`.
- Network failure after request initiation or an ambiguous provider payload:
  `unknown-outcome`; do not convert it into an automatic retry.

## Audit Requirements

Record tenant, principal, recipient summary, idempotency key, approval status, and
trace id. Never log secrets or opaque approval token values.
