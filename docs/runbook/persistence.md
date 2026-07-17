# Persistence Runbook

This runbook covers the local-first persistence contract for the personal
assistant runtime. It documents the supported in-memory path, the optional
Postgres backend, the primary tables, idempotency behavior, and current
limitations.

## Implementation Boundary

Current branch behavior:

- `build_container()` in `personal_assistant.infrastructure.bootstrap` calls
  `build_persistence_adapters()`.
- `PERSISTENCE_BACKEND=memory` returns in-memory approval, event-store, outbox,
  workflow-state, memory, calendar, scheduler, and trace adapters.
- `PERSISTENCE_BACKEND=postgres` requires `DATABASE_URL`, imports
  `personal_assistant.adapters.persistence.postgres`, and initializes an
  idempotent SQL schema.
- `AppSettings.from_env()` loads `.env` through `APP_ENV_FILE` and the
  persistence-phase settings surface includes `PERSISTENCE_BACKEND` and
  `DATABASE_URL`.
- Postgres support is optional. Memory mode never imports `psycopg`.
- There is no migration history yet; schema creation is code-owned through
  `ensure_schema()`.

Persistence-phase contract:

- `PERSISTENCE_BACKEND=memory` selects the current local in-memory stores.
- `PERSISTENCE_BACKEND=postgres` selects durable Postgres stores for approvals,
  events, outbox, workflow state, memory, local calendar, scheduled reminders,
  and traces.
- `DATABASE_URL` is required only for the Postgres backend.
- If a runtime receives `PERSISTENCE_BACKEND=postgres` without `DATABASE_URL`,
  `psycopg`, or a reachable database, it fails startup instead of silently
  falling back to memory.

## Configuration

| Variable | Required When | Secret | Purpose |
|---|---|---:|---|
| `PERSISTENCE_BACKEND=memory` | Local tests, demos, disposable runtime | No | Use process-local stores. Data is lost when the process exits. |
| `PERSISTENCE_BACKEND=postgres` | Durable local or deployed runtime | No | Select Postgres stores through `personal_assistant.adapters.persistence.postgres`. |
| `DATABASE_URL` | `PERSISTENCE_BACKEND=postgres` | Yes | Postgres connection URL. Keep it in `.env` or the deployment secret store, never in committed docs. |
| `APP_ENV_FILE=.env` | Local runtime startup | No | Optional env file read by `AppSettings.from_env()`. Use `APP_ENV_FILE=disabled` for hermetic tests. |

Example local Postgres URL:

```bash
DATABASE_URL="postgresql://personal_assistant:personal_assistant@127.0.0.1:5432/personal_assistant"
```

Install the optional dependency before using Postgres:

```bash
.venv/bin/python -m pip install -e '.[postgres]'
```

## Run In Memory

Use the memory backend for tests, local smoke checks, and throwaway Telegram or
HTTP sessions.

```bash
export APP_ENV_FILE=disabled
export PERSISTENCE_BACKEND=memory
PYTHONPATH=src python3 -B -m pytest -q
```

Start the runtime API:

```bash
export APP_ENV_FILE=.env
export PERSISTENCE_BACKEND=memory
PYTHONPATH=src python3 -m uvicorn personal_assistant.infrastructure.http:app \
  --host 127.0.0.1 \
  --port 8000
```

Expected behavior:

- `/healthz` returns `ok`.
- `/readyz` returns `ready`.
- Runtime data exists only inside the current Python process.
- Restarting the process clears approvals, workflow states, events, outbox,
  scheduler jobs, local calendar events, sent notification records, traces, and
  tenant memory.

## Run With Postgres

Use Postgres when webhook retries, worker restarts, approval resumes, or local
admin inspection must survive process restarts.

Prerequisites:

- Postgres is running and reachable from the runtime process.
- The database and role exist.
- The runtime dependencies include `psycopg`, normally through
  `personal-assistant[postgres]`.
- The runtime user can create tables in the configured schema, or the schema has
  already been created with `ensure_schema()`.

Configure the runtime:

```bash
export APP_ENV_FILE=.env
export PERSISTENCE_BACKEND=postgres
export DATABASE_URL="postgresql://personal_assistant:personal_assistant@127.0.0.1:5432/personal_assistant"
PYTHONPATH=src python3 -m uvicorn personal_assistant.infrastructure.http:app \
  --host 127.0.0.1 \
  --port 8000
```

Postgres startup criteria:

- The runtime refuses to start when `DATABASE_URL` is missing.
- Missing `psycopg` raises a clear optional-dependency error.
- Unreachable Postgres fails during schema initialization.
- The container wires Postgres implementations for the same persistence ports
  used by the memory backend, including scheduler, local calendar, and traces.
- Replaying a previously accepted webhook after process restart reuses existing
  state instead of creating duplicate side effects.

## Main Tables

The current Postgres adapter uses the `assistant_*` tables below. Flexible DTO
payloads are stored as `JSONB`; tenancy, idempotency, status, timestamps, and
worker lease fields are typed columns.

| Table | Purpose | Required Uniqueness / Indexes |
|---|---|---|
| `assistant_workflow_states` | Durable-lite state for reminder and command workflows; `payload_fingerprint` is stored separately from the replay key. | Primary `(tenant_id, idempotency_key)`; unique `(tenant_id, workflow_id)`; index `(tenant_id, status, updated_at)`. |
| `assistant_events` | CloudEvents-style append store for tenant-scoped domain/application events. | Primary `(tenant_id, event_id)`; index `(tenant_id, occurred_at)`. |
| `assistant_outbox` | Transactional outbox records awaiting dispatch. | Primary `(tenant_id, idempotency_key)`; unique `(tenant_id, message_id)`; index `(tenant_id, dispatch_status, claimed_until, next_attempt_at, created_at)`. |
| `assistant_approvals` | Pending, approved, and cancelled P3+ approvals. | Primary `(tenant_id, approval_id)`; unique `(tenant_id, principal_id, workflow_kind, idempotency_key)`; index `(tenant_id, principal_id, status, created_at)`. |
| `assistant_memory_records` | Explicit tenant/user-scoped long-term memory items. | Primary `(tenant_id, memory_id)`; index `(tenant_id, user_id, kind, confirmed, created_at)`. |
| `assistant_scheduled_reminders` | Jobs due for reminder notifications. | Primary `(tenant_id, idempotency_key)`; unique `(tenant_id, reminder_id)`; index `(tenant_id, sent, notify_at, reminder_id)`. |
| `assistant_calendar_events` | Local calendar tool state until external calendar sync exists. | Primary `(tenant_id, idempotency_key)`; unique `(tenant_id, event_id)`; index `(tenant_id, starts_at)`. |
| `assistant_trace_events` | Trace records for admin/runtime inspection. | Primary `(tenant_id, trace_id)`; indexes `(tenant_id, run_id, timestamp)` and `(tenant_id, timestamp)`. |

Pre-P1-A4 JSONB rows are upgraded only while reading: approvals use their
legacy `message_id` as `source_event_id` and recompute the canonical reminder
fingerprint; scheduled reminders receive a deterministic legacy source and use
`UTC` because the original IANA zone was not persisted; calendar results recover
their timezone and any available identity metadata from the request payload
stored beside the result. New writes must include the strict fields and never
use these fallbacks.

### Trace privacy and legacy rows

Both trace backends enforce the same privacy boundary. Trace construction and
serialization sanitize recursively, the in-memory recorder stores and returns
safe copies, and the Postgres recorder computes its fingerprint and JSONB value
only from the sanitized copy. Postgres reads also sanitize legacy payloads
before returning them to runtime or admin surfaces.

The boundary retains only allowlisted identifiers, hashes, sizes/counts,
categories, and operational metadata. It redacts messages, transcripts,
prompts, credentials, URL credentials/query/fragment data, and binary/audio
content. Adding a new trace field therefore requires classifying it in the
central privacy policy; an unknown field is omitted by default.

This read boundary does not rewrite historical JSONB in place. Before deploying
over a database that may contain traces written by an older version, the
operator must apply the organization's retention policy to purge or separately
rewrite those rows. Until that cleanup completes, old raw values may remain at
rest even though current APIs no longer return them.

## Idempotency Rules

Every durable write must be tenant-scoped. A key from one tenant must never
reuse or conflict with another tenant's record.

Expected behavior by store:

- Event store: `(tenant_id, event.id)` is idempotent. Same payload returns the
  existing event; same key with a different payload raises conflict.
- Outbox: `(tenant_id, idempotency_key)` is idempotent. The fingerprint is the
  serialized event payload. A changed event for the same key raises conflict.
- Reminder workflow state: the key is `reminder:v2:<full SHA-256>` over
  versioned canonical JSON containing tenant, channel, principal, conversation,
  and source event. The independent `payload_fingerprint` hashes canonical
  `text`, `recipient`, and `timezone`; it is compared before replay and can
  never be changed or removed by `upsert`.
- Reminder registration uses atomic insert-or-replay. Same key and fingerprint
  returns persisted state; same key and changed fingerprint raises typed
  conflict without overwriting. A validated approval can atomically resume the
  matching `waiting_approval` step; only the winner transitions to `running`.
- Terminal workflow states (`completed` or `failed`) are immutable; attempted
  lifecycle mutation raises conflict.
- Approval requests: `(tenant_id, principal_id, workflow_kind,
  idempotency_key)` is idempotent. Same request returns the existing approval;
  different approval details raise conflict.
- Calendar events: `(tenant_id, idempotency_key)` is idempotent. Same approved
  request returns the existing event with replay metadata; changed request
  raises conflict.
- Notifications: `(tenant_id, idempotency_key)` is idempotent. Same approved
  request returns the existing delivery; changed request raises conflict.
- Scheduled reminders: `(tenant_id, idempotency_key)` is idempotent. Same key
  and effect-relevant payload returns the existing job; changed calendar,
  instant, timezone, source event, payload fingerprint, channel, recipient, or
  body raises conflict.
- Memory records currently do not use an idempotency key. Only explicit,
  tenant/user-scoped memory should be stored; do not dump raw chat transcripts
  into memory.

For Postgres, enforce these rules with unique constraints plus transactional
insert/read conflict handling. Do not implement idempotency as a read-then-write
sequence without a database constraint.

## Worker And Lease Semantics

The outbox and reminder worker paths must be safe under retries and restarts:

- Claiming an outbox message should atomically set `dispatch_status=claimed`,
  `claim_token`, `claim_owner`, `claimed_until`, and increment `attempts`.
- Publishing should require the current claim token unless the message is
  already published.
- Releasing should clear the claim fields and return the message to `pending`.
- Reminder dispatch should query due unsent jobs by tenant and mark them sent
  only after the notification adapter returns an accepted idempotent result.
- Worker queries must always filter by trusted `Principal.tenant_id`.

Use `SELECT ... FOR UPDATE SKIP LOCKED` or an equivalent atomic claim pattern
for Postgres workers so multiple workers do not claim the same row.

## Verification

Memory backend gate:

```bash
export APP_ENV_FILE=disabled
export PERSISTENCE_BACKEND=memory
PYTHONPATH=src python3 -B -m pytest -q
PYTHONPATH=src python3 -B -m compileall -q src tests
python3 -m json.tool eval/cases.json >/dev/null
```

Postgres backend gate:

```bash
export APP_ENV_FILE=.env
export PERSISTENCE_BACKEND=postgres
export DATABASE_URL="postgresql://personal_assistant:personal_assistant@127.0.0.1:5432/personal_assistant"
.venv/bin/python -m pytest tests/test_persistence_config.py tests/test_postgres_persistence.py -q
```

Manual replay checks:

- Create a reminder that requires approval.
- Restart the API.
- Approve the pending action.
- Replay the same Telegram update or runtime request.
- Confirm exactly one workflow state, calendar event, scheduled reminder,
  event, outbox message, and notification delivery exist for the tenant/key.

## Limitations

- There is no committed migration history in this branch.
- Notification delivery records are still adapter-local; they are not persisted
  in Postgres yet.
- There is no data migration path from existing in-memory sessions to Postgres;
  memory sessions are disposable by design.
- External calendar sync is not implemented. `calendar_events` represents the
  local calendar adapter state only.
- Memory retrieval is simple tenant/user filtering, not semantic vector search.
- `.env`, `.env.*`, database URLs, bot tokens, and provider keys must stay out
  of committed files and trace output.
