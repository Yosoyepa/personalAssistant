# Agent Contract: Reminder Outbox Worker

## 1. Mission

Deliver due Telegram reminder notifications from the durable outbox without blindly resending ambiguous outcomes.

## 2. Ownership

The worker owns one artifact: a durable transition of one `notification.requested` outbox message and its scheduler read-model mirror.

## 3. Non-Ownership

The worker does not schedule reminders, claim other event types, or infer delivery after ambiguity.

## 4. Inputs

An authenticated P5 worker principal, PostgreSQL UoW, trusted clock, Telegram notification port, and at most one due outbox message.

## 5. Required Context

Only tenant-scoped outbox state, the matching scheduler row, closed delivery statuses, retry policy, and sanitized provider result.

## 6. Optional Context

Provider `Retry-After` seconds and operator reconciliation approval.

## 7. Tools

- PostgreSQL delivery UoW (P2 internal state).
- `notification.send` (P5 communication, runtime approval required).
- Operator list/reconcile commands (P5).

## 8. Forbidden Actions

- Call the provider before committed `sending`.
- Reclaim or resend `sending`/`uncertain` work.
- Use scheduler due state as delivery authority.
- Commit outbox and scheduler separately while both rows exist. After provider
  I/O, a missing scheduler mirror is the explicit exception: commit the
  canonical outbox terminal transition without recreating the mirror.
- Log or print recipient, body, token, provider body, or raw exception.
- Claim an outbox event whose type is not `notification.requested`.

## 9. Output Schema

Worker output contains only message IDs, delivery statuses, attempts, timestamps, and sanitized error/provider codes.

## 10. Acceptance Criteria

- PostgreSQL integration test observes `sending` in both stores before fake provider I/O.
- An ambiguous pre-I/O commit produces zero provider calls.
- Expired `sending` becomes `uncertain` and is never reclaimed.
- Known transient attempts use 30 s, 2 m, and 5 m delays; attempt four is terminal.
- Manual reconciliation requires a resource-bound P5 approval.

## 11. Failure Modes

- Claim expires before I/O: another worker may reclaim it.
- Provider result is ambiguous: persist `uncertain`.
- Database result after provider I/O is ambiguous: leave/sweep to `uncertain`; never resend blindly.
- Scheduler mirror disappears after I/O: preserve canonical outbox terminal state.
- Invalid internal payload: fail pre-I/O with zero attempts.

## 12. Escalation Rules

Only an operator may resolve `uncertain` as `delivered` or `retry`, with exact CLI confirmation and a P5 approval grant. Retry is forbidden after four attempts.

## 13. Logging Requirements

Persist and expose only IDs, status, attempts, timestamps, closed error categories/codes, and numeric provider codes.
