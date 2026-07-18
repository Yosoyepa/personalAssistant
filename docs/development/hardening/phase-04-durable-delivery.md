# Phase 04 - durable Telegram delivery

## Identity

| Field | Value |
|---|---|
| Status | `LOCAL_ACCEPTED` |
| Maintainer | `Yosoyepa <jandradeu@unal.edu.co>` |
| Phase branch | `codex/phase-4-durable-delivery` |
| Base commit | `a0117ec` |
| Accepted implementation head | `442e3df` |
| Local acceptance commit | `4ae3001` |
| Pull request | pending |
| Merge commit | pending |
| Date | `2026-07-17` |

## Objective and acceptance

Move reminder notification delivery from a scheduler-side effect to a durable
PostgreSQL outbox protocol. The accepted design must prevent concurrent claims,
commit the external-I/O boundary before calling Telegram, avoid blind resend
after an ambiguous outcome, and leave every crash boundary either recoverable
automatically or explicitly reconcilable by a P5 operator.

The phase was accepted locally only after multiple real PostgreSQL workers,
ambiguous commits, injected process crashes, blocked providers, mirror failures,
lease expiry, transient and permanent Telegram responses, and manual
reconciliation all preserved the documented state machine. No test contacted
Telegram or another external provider.

## Agent ledger

| Role | Goal | Commit(s) | Decision |
|---|---|---|---|
| P4-A1 | Persist explicit notification delivery state in outbox and scheduler | `defb41b` | accepted after transition invariants and legacy compatibility were closed |
| P4-A2 | Claim due PostgreSQL notifications with owner, token, lease, and `SKIP LOCKED` | `377eef5` | accepted after fencing, tenant isolation, expiry, and concurrency tests |
| P4-A3 | Return typed Telegram delivery outcomes and sanitized provider metadata | `108e3ed` | accepted after timeout, reset, HTTP classification, and `Retry-After` review |
| P4-A4 | Dispatch reminders only through the outbox and add operational recovery commands | `7a0b85d` | accepted after PostgreSQL runtime, CLI approval, and crash-boundary review |
| P4-A5 | Prove multiworker and crash semantics against PostgreSQL 16 | `d65ce59` | accepted after 13 adversarial integration cases and fail-hard provider doubles |
| P4-A5 gate rework | Restore differential coverage with meaningful operational tests | `95de428` | accepted after the unchanged 90% gate rose from 88% to 90% |
| P4-A5 characterization cleanup | Remove the obsolete expected-failure description after closing both delivery gaps | `f9e4bd6` | accepted; documentation-only test change |

Every implementation diff was reviewed before explicit-path staging. Accepted
branches were integrated with merge commits. No agent branch was rebased,
squashed, force-pushed, or pushed independently.

## Delivery state and ownership decisions

- The durable outbox is the canonical delivery authority. The scheduled
  reminder is a read-model mirror and never authorizes provider I/O by itself.
- The persisted state machine is `pending -> claimed -> sending`, followed by
  `published`, `failed`, `uncertain`, or a known-transient return to `pending`.
- `claimed` records a safe owner, random fencing token, and lease. Claims use
  `FOR UPDATE SKIP LOCKED`, are tenant-scoped, and do not consume an attempt.
- The worker claims one message per dispatch. A provider call can therefore not
  leave a queue of claimed messages aging behind it.
- `sending` is committed in the same PostgreSQL transaction as the scheduler
  mirror before Telegram is called. Entering `sending` increments the attempt.
- A claim that expires before `sending` is safe to reclaim. An expired
  `sending` message becomes `uncertain` and is never resent automatically.
- Provider success commits outbox `published` and scheduler `sent`. A known
  transient result is rescheduled; a permanent result becomes `failed`; a
  network result whose side effect cannot be known becomes `uncertain`.
- The provider idempotency key is `<message-id>:attempt:<attempt-number>`. It is
  stable for one attempt, while a deliberate later retry receives a new key.
- Provider result correlation must match both the request idempotency key and
  channel. A mismatch is treated as an unknown outcome.
- A malformed internal notification fails before I/O with zero attempts and a
  closed internal error code. Generic outbox event types are not claimed by the
  reminder worker.
- A missing scheduler mirror before I/O prevents delivery and records a safe
  failure. A mirror that disappears after provider I/O cannot roll back the
  canonical outbox terminal state.

## Telegram outcome and retry decisions

- Telegram `429` and explicit HTTP `5xx` responses are known transient
  outcomes. A valid numeric `Retry-After` is preserved without provider text.
- Telegram HTTP `4xx`, except `429`, is permanent.
- Timeout, connection reset, malformed provider response after dispatch, and
  other ambiguous network results are `unknown-outcome`.
- Known-transient delays are 30 seconds, 2 minutes, and 5 minutes. The fourth
  total attempt is terminal. A larger `Retry-After` value wins over local
  backoff.
- The implementation intentionally does not claim exactly-once delivery.
  Avoiding a duplicate is preferred over silently retrying an outcome that may
  already have reached Telegram.

## Operator recovery

The worker exposes only the following commands:

```powershell
uv run python -m personal_assistant.infrastructure.worker run-once
uv run python -m personal_assistant.infrastructure.worker list-uncertain
uv run python -m personal_assistant.infrastructure.worker resolve-uncertain --message-id <id> --resolution delivered --confirm <id>
uv run python -m personal_assistant.infrastructure.worker resolve-uncertain --message-id <id> --resolution retry --confirm <id>
```

Listing and resolution require a trusted P5 principal. Resolution also requires
a resource-bound approval grant and exact message-ID confirmation. `retry` is
rejected after four attempts. Operators must verify outside this application
that the message did not arrive before choosing `retry`.

CLI, traces, metrics, and persisted error metadata contain only identifiers,
states, attempts, timestamps, closed categories/codes, and numeric provider
codes. They never include recipient, body, provider diagnostics, tokens, or
URLs.

## Migration decisions

- `0003_durable_delivery_state.sql` adds typed state, attempts, retry time,
  lease/fencing metadata, safe error columns, transition constraints, and due
  indexes for outbox and scheduled reminders.
- `0004_scheduler_delivery_mirror.sql` makes typed delivery state canonical for
  the scheduler mirror and keeps the legacy JSON payload and `sent` column
  coherent for old readers.
- Migration constraints permit a pre-I/O `failed` state with zero attempts and
  no `sending_at`, and require attempt/sending evidence for post-I/O terminal
  or uncertain states.
- Both migrations are additive and checksummed. Startup still performs no DDL;
  the explicit migration CLI remains the only supported schema mutation path.
- A fresh PostgreSQL 16 schema passed `status -> apply 0001..0004 -> apply
  no-op -> status ready`, after which the acceptance schema was removed.

## Verification evidence

All external Telegram, LLM, transcription, and TTS implementations were fake.
PostgreSQL checks used an ephemeral `postgres:16-alpine` container reporting
server version `16.14`.

| Gate | Result |
|---|---|
| `uv lock --check` | pass; 76 packages resolved |
| `uv sync --frozen --all-extras --group dev` | pass; 75 packages checked |
| `uv run ruff check .` | pass |
| `uv run mypy src` | pass; 95 source files and zero diagnostics |
| `uv run pytest -q` | 570 passed, 3 allowlisted skips, 36 subtests passed |
| adversarial PostgreSQL delivery corpus | 13 passed against fresh schemas |
| coverage | 91% total line coverage, threshold 85% |
| diff-cover | 90% over 910 changed lines with 88 missing, threshold 90% |
| `uv run python -m compileall -q src` | pass |
| `uv build` | sdist and wheel built |
| `uv run pip-audit` | no known dependency vulnerabilities; unpublished local package not present on PyPI |
| `uv run pre-commit run --all-files` | all hooks passed |
| Gitleaks `v8.28.0` read-only container scan | no leaks in 90 commits / approximately 2.23 MB |
| `git diff --check` | pass |
| migration smoke | `status -> apply 0001..0004 -> apply no-op -> status ready`; schema removed |

The three skips are compatibility/environment probes: the adapter deliberately
does not expose module-level SQL constants or record-serializer helpers, and an
import-order isolation probe skips once psycopg has already been loaded by the
real PostgreSQL corpus. There are no expected failures in the delivery or
atomicity invariants.

## Adversarial and recovery evidence

- Three concurrent claimers obtained disjoint `SKIP LOCKED` sets. Expired
  pre-I/O claims were fenced and reclaimed with zero attempts consumed.
- Two dispatch coordinators produced one provider invocation per message. Any
  serializable conflict after provider I/O left `sending`, which a restart
  swept to `uncertain` without another call.
- A connection wrapper physically committed `sending` and then raised an
  operational disconnect. The dispatcher did not authorize provider I/O after
  the unknown commit outcome; recovery observed and swept the durable state.
- Process crashes before a provider response and after a simulated provider
  side effect both left `sending`, then became `uncertain` at lease expiry.
- Blocking the provider proved, from separate PostgreSQL connections, that the
  outbox and scheduler were both `sending` with no `published_at` before the
  result returned. Other due messages remained unclaimed.
- Terminal mirror failure rolled the terminal transaction back to `sending`.
  Restart recovery produced `uncertain` and made no second provider call.
- Concurrent sweepers selected disjoint expired sends, were idempotent, and did
  not touch another outbox event type.
- Manual reconciliation rejected missing approval, P3 authority, and a grant
  for the wrong resource. Accepted `delivered` and `retry` decisions updated
  both canonical outbox and scheduler mirror.
- Tenant and claim-token fencing survived fresh persistence instances and
  rejected stale or cross-tenant transitions.

## Data and privacy review

- No production credential, provider secret, real message, transcript, user
  identifier, or external recipient was added.
- Test database credentials were ephemeral, rotated during acceptance, and are
  absent from the repository.
- Failure doubles include private-looking diagnostics solely to prove that the
  runtime reduces them to closed codes before persistence or CLI output.
- The worker never prints or traces notification bodies or recipients.

## Risks and rollback

- Telegram offers no transaction shared with PostgreSQL. A crash after
  Telegram accepts a message but before the local terminal commit requires
  manual reconciliation; automatic resend is intentionally prohibited.
- A long-running provider call can outlive the sending lease. Another worker
  may classify it `uncertain`; the original terminal write is then fenced and
  cannot overwrite that decision. Operator review remains required.
- If the scheduler mirror is missing after I/O, the outbox remains authoritative
  and the read model must be repaired separately.
- The legacy scheduler `sent` field remains compatibility-only. New code must
  use the typed delivery status and timestamps.
- Starlette emits one upstream `TestClient` deprecation warning that does not
  affect runtime behavior.

Rollback is a revert of the eventual Phase 4 merge commit. Operators must stop
the worker before rollback and first reconcile all `sending` and `uncertain`
messages; reverting while provider outcomes are unresolved could invite an
unsafe legacy resend. Migrations `0003` and `0004` are additive and remain in
place. The prior binary must be verified to tolerate the added columns,
constraints, indexes, trigger, and JSON fields before it is restarted. No down
migration or data deletion is authorized.
