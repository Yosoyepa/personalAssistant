# Tool Contract: calendar.create_event

## Purpose

Create a calendar event for the authenticated principal's tenant. In the MVP this
is a local adapter; Google Calendar can replace it behind the same contract.

Application port: `src/personal_assistant/application/ports/calendar.py`  
Local adapter: `src/personal_assistant/adapters/outbound/calendar/local.py`

## Input Schema

- `title: string`
- `starts_at: datetime`
- `ends_at: datetime | null`
- `timezone: string`
- `idempotency_key: string`

## Output Schema

- `event_id: string`
- `title: string`
- `starts_at: datetime`
- `idempotency_key: string`
- `reused: boolean`

## Side Effects

External write.

## Permission Tier

`P3`

## Preconditions

- `tenant_id` comes from `Principal`.
- Caller has at least P3 permission tier.
- Trusted P3 `ApprovalGrant` is supplied by the runtime approval service.
- Idempotency key is present.

## Postconditions

- Event is scoped to the principal tenant.
- Duplicate idempotency key returns the original event.
- Tool call is traced and auditable.

## Failure Cases

- Missing or untrusted approval grant: fail closed.
- Permission tier too low: fail closed.
- Invalid datetime: validation failure.

## Audit Requirements

Record tenant, principal, event id, idempotency key, approval status, and trace id.
