# Personal Assistant

Local-first personal assistant runtime built as a deterministic L2 workflow:
Python code owns routing, state, permissions, idempotency, and side effects;
LLM calls are bounded provider activities used only when deterministic logic
needs help.

The project is intentionally not a generic autonomous agent loop. It is a
single assistant harness for personal productivity workflows with tenant-scoped
memory, explicit approvals, local-first observability, Telegram integration,
optional audio, and optional durable Postgres storage.

## Current Status

The repository currently includes:

- A Python 3.11 package with domain, application, adapter, contract, and
  infrastructure layers.
- FastAPI runtime endpoints for health, readiness, reminders, approvals,
  workflow state, traces, Telegram webhooks, and local admin views.
- Telegram webhook normalization, command routing, text replies, reminder
  approvals, due-reminder dispatch, and outbound Telegram notification
  adapters.
- Optional Telegram voice/audio transcription through an OpenAI-compatible
  speech-to-text adapter.
- Optional MiniMax text-to-speech for Telegram audio replies through
  `sendAudio`.
- Deterministic reminder and calendar workflows with P3/P5 approval gates,
  idempotency keys, event-store writes, outbox records, workflow states, and
  trace events.
- In-memory stores by default, plus optional Postgres persistence for
  approvals, events, outbox, workflow state, memory, local calendar, scheduler,
  and traces.
- A local-only read-only admin dashboard for tenant-scoped inspection.
- MiniMax and generic Anthropic-compatible LLM adapters behind `LLMProvider`.
- Deterministic tests for tenant isolation, idempotency, permissions,
  prompt-injection handling, HTTP boundaries, admin visibility, Telegram
  delivery, audio adapters, Postgres wiring, and architecture boundaries.

Current limits:

- No committed migration history yet; Postgres schema creation is code-owned.
- Notification delivery records are still adapter-local rather than persisted in
  Postgres.
- No production deployment hardening, external calendar sync, OAuth token
  storage, semantic vector memory, active MCP runtime path, or active A2A runtime
  path.

## Architecture

```text
Telegram webhook / local runtime request
        -> Channel normalization
        -> Trusted principal + tenant resolution
        -> Conversation command service
        -> Deterministic reminder/calendar workflow
        -> Ports for LLM, transcription, TTS, calendar, scheduler, events,
           outbox, approvals, memory, notifications, traces
        -> In-memory or Postgres adapters
        -> Optional workers for due reminders and notification dispatch
```

MVP autonomy is L2. Deterministic code owns the path and uses bounded LLM calls
only for classification, extraction, or drafting where the route allows it.
WhatsApp, A2A, MCP, and external integration contracts may exist, but they are
not the active internal runtime for the MVP.

The executable contract is in `agents/personal_assistant/contract.md`. The core
security invariant is that `tenant_id` comes from the authenticated `Principal`,
never from Telegram text, tool arguments, LLM output, request JSON bodies, or
retrieved documents.

## Local Setup

Python 3.11 or newer is required.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
```

Install optional API and Postgres dependencies when needed:

```bash
python -m pip install -e '.[api,test]'
python -m pip install -e '.[api,test,postgres]'
```

Configuration is loaded from the process environment and, by default, an
optional local `.env` file. Use `.env.example` as the template. Set
`APP_ENV_FILE=disabled` for hermetic tests. Keep Telegram tokens, webhook
secrets, MiniMax keys, speech provider keys, `ADMIN_TOKEN`, `DATABASE_URL`, OAuth
tokens, and every other credential out of git.

## Verify Locally

The main local gate does not require Telegram, MiniMax, Groq, Postgres, or other
network services:

```bash
APP_ENV_FILE=disabled PYTHONPATH=src python3 -B -m pytest -q
PYTHONPATH=src python3 -B -m compileall -q src tests
python3 -m json.tool eval/cases.json >/dev/null
```

For the focused API/persistence/admin gate used during this MVP:

```bash
APP_ENV_FILE=disabled PYTHONPATH=src python3 -B -m pytest -q \
  tests/test_llm_adapters.py tests/test_telegram_notifications.py tests/test_http_runtime.py \
  tests/test_postgres_persistence.py tests/test_persistence_config.py \
  tests/test_admin_dashboard.py tests/test_prompt_and_reply_catalogs.py
```

Expected properties:

- All tests pass and source/test files compile.
- `eval/cases.json` parses as valid JSON.
- User-supplied `tenant_id=...` remains inert text.
- P3/P5 side effects require approval and are idempotent.
- Duplicate Telegram webhook delivery does not duplicate calendar, reminder,
  event-store, workflow-state, scheduler, or outbox records.
- Cross-tenant canary data is not returned through memory or calendar paths.

## Run The API

Start the local runtime on loopback:

```bash
export APP_ENV_FILE=.env
export PERSISTENCE_BACKEND=memory
PYTHONPATH=src python3 -m uvicorn personal_assistant.infrastructure.http:app \
  --host 127.0.0.1 \
  --port 8000
```

Check the process:

```bash
curl -sS http://127.0.0.1:8000/healthz | python3 -m json.tool
curl -sS http://127.0.0.1:8000/readyz | python3 -m json.tool
```

Create a reminder through the trusted runtime API:

```bash
curl -sS -X POST http://127.0.0.1:8000/v1/runtime/reminders \
  -H "Content-Type: application/json" \
  -H "X-Principal-Id: user-local" \
  -H "X-Tenant-Id: tenant-local" \
  -H "X-Permission-Tier: P5" \
  -d '{
    "message_id": "telegram-message-1",
    "conversation_id": "telegram-chat-1",
    "text": "recuerdame clase el martes a las 5",
    "channel": "telegram",
    "recipient": "telegram-chat-1",
    "now": "2026-06-20T12:00:00+00:00",
    "timezone": "America/Bogota"
  }' \
  | python3 -m json.tool
```

The expected first response is an approval escalation. Approve it with:

```bash
APPROVAL_ID="<approval id>"

curl -sS -X POST "http://127.0.0.1:8000/v1/runtime/approvals/${APPROVAL_ID}/approve" \
  -H "Content-Type: application/json" \
  -H "X-Principal-Id: user-local" \
  -H "X-Tenant-Id: tenant-local" \
  -H "X-Permission-Tier: P5" \
  -d '{}' \
  | python3 -m json.tool
```

## Telegram

The Telegram bridge is documented in `docs/runbook/telegram.md`. The active
webhook path is:

```text
POST /webhooks/telegram/{TELEGRAM_WEBHOOK_SECRET}
```

Minimum local environment:

```bash
export TELEGRAM_BOT_TOKEN="<bot token>"
export TELEGRAM_WEBHOOK_SECRET="<random webhook secret>"
export TELEGRAM_ALLOWED_USER_IDS="<comma-separated Telegram user ids>"
export ASSISTANT_TENANT_ID="personal"
export ASSISTANT_TIMEZONE="America/Bogota"
```

Run FastAPI on loopback and expose it through ngrok or another HTTPS tunnel.
Then register the webhook:

```bash
export PUBLIC_BASE_URL="https://your-public-https-url.example"

curl -sS -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
  -d "url=${PUBLIC_BASE_URL}/webhooks/telegram/${TELEGRAM_WEBHOOK_SECRET}" \
  -d "secret_token=${TELEGRAM_WEBHOOK_SECRET}" \
  | python3 -m json.tool
```

The webhook resolves tenant authority from runtime configuration, rejects wrong
path/header secrets, filters allowed Telegram user ids when configured, and
does not let the assistant call Telegram directly. Telegram sends are owned by
the notification adapter and remain P5/idempotent side effects.

## Audio And TTS

Incoming Telegram voice/audio messages can be transcribed before command
routing. Configure an OpenAI-compatible transcription provider, for example
Groq:

```bash
export TRANSCRIPTION_PROVIDER="openai_compatible"
export GROQ_API_KEY="<speech provider key>"
export TRANSCRIPTION_BASE_URL="https://api.groq.com/openai"
export TRANSCRIPTION_MODEL="whisper-large-v3-turbo"
```

Optional Telegram audio replies use MiniMax TTS. Workflows still produce text;
infrastructure may synthesize a short audio copy after the text reply is
accepted:

```bash
export TTS_PROVIDER="minimax"
export MINIMAX_API_KEY="<minimax token plan key>"
export TTS_BASE_URL="https://api.minimax.io"
export TTS_MODEL="speech-2.8-turbo"
export TTS_VOICE_ID="male-qn-qingse"
export TTS_AUDIO_FORMAT="mp3"
export TTS_LANGUAGE_BOOST="Spanish"
export TTS_MAX_REPLY_CHARACTERS="280"
export TELEGRAM_AUDIO_REPLY_MODE="voice_only"
```

`TELEGRAM_AUDIO_REPLY_MODE=voice_only` sends audio only when the incoming
message was voice/audio. Use `always` for every Telegram reply or `disabled` for
text-only behavior. See `docs/runbook/minimax.md` for provider notes.

## Admin Dashboard

The admin dashboard is a local read-only inspection surface, not a production
ops console. It is loopback-only and tenant-scoped; when `ADMIN_TOKEN` is set,
requests must include `Authorization: Bearer <token>` or `X-Admin-Token`.

```bash
export ADMIN_TOKEN="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
)"
```

Open:

```text
http://127.0.0.1:8000/admin?tenant_id=tenant-local&principal_id=user-local
```

Useful JSON endpoints include `/admin/snapshot`, `/admin/health`,
`/admin/approvals`, `/admin/traces`, `/admin/outbox`, `/admin/scheduler`,
`/admin/agenda`, `/admin/reminders`, `/admin/errors`, `/admin/events`,
`/admin/states`, and `/admin/memory`. The full guide is in
`docs/runbook/admin-dashboard.md`.

## Postgres

Memory mode is the default and is disposable. Use Postgres when webhook retries,
approval resumes, worker restarts, or admin inspection must survive process
restarts.

Install the optional extra and configure the backend:

```bash
python -m pip install -e '.[api,test,postgres]'

export PERSISTENCE_BACKEND=postgres
export DATABASE_URL="postgresql://personal_assistant:personal_assistant@127.0.0.1:5432/personal_assistant"
PYTHONPATH=src python3 -m uvicorn personal_assistant.infrastructure.http:app \
  --host 127.0.0.1 \
  --port 8000
```

`PERSISTENCE_BACKEND=postgres` fails startup if `DATABASE_URL`, `psycopg`, or
the database connection is unavailable. The current adapter initializes
`assistant_*` tables for approvals, events, outbox, workflow states, memory,
local calendar, scheduled reminders, and traces. See
`docs/runbook/persistence.md` for schema notes, idempotency rules, worker lease
expectations, and limitations.

## Implementation Boundary

Present:

- `normalize_telegram_webhook(payload, tenant_id=...)`
- `build_container()` and `personal_assistant.infrastructure.http:app`
- `ConversationCommandService`, `ReminderWorkflow`, and `ReminderWorker`
- Local-only admin dashboard and JSON admin endpoints
- Telegram webhook bridge and outbound Telegram notification adapter
- MiniMax LLM/TTS adapters and generic Anthropic/OpenAI-compatible provider
  adapters where applicable
- In-memory persistence and optional Postgres persistence for the main runtime
  state stores

Not present yet:

- Versioned migrations
- Persisted notification delivery ledger
- Production auth/deploy hardening
- OAuth credential storage
- External calendar sync
- Active MCP or A2A execution path

Forbidden by contract:

- Tenant authority from untrusted text, request bodies, LLM output, or retrieved
  documents
- Direct `telegram.send` from the agent workflow
- MVP `mcp.*` or `a2a.*` tool calls
- Third-party messaging, financial actions, destructive bulk deletion, and
  secret reads

## Project Docs

- `docs/runbook/telegram.md` - BotFather, ngrok, webhook, Telegram command,
  audio, and local runtime notes.
- `docs/runbook/admin-dashboard.md` - local admin dashboard and JSON endpoint
  guide.
- `docs/runbook/persistence.md` - memory/Postgres persistence guide.
- `docs/runbook/minimax.md` - MiniMax LLM and TTS provider notes.
- `docs/adr/` - accepted architecture decisions.
- `docs/architecture/` - architecture reviews and short design notes.
- `docs/architecture/build-vs-frameworks.md` - why the MVP uses a small local
  harness instead of OpenClaw, HermeAgent/Hermes Agent, or OpenHands as the core
  runtime.
- `docs/development/maintainer-workflow.md` - executable single-maintainer
  workflow for `codex/` branches, worktrees, review, commits, phase PRs,
  rollback, gates, and Definition of Done;
  `docs/development/hardening-log.md` is its evidence template.
- `docs/public/` - public written artifacts.

## Test Map

- `tests/test_contracts.py` - A2A serialization, tool policy surface, and
  inactive `mcp.*`/`a2a.*` fail-closed behavior.
- `tests/test_command_router.py` - Telegram command routing, pending approvals,
  approval commands, agenda, and status.
- `tests/test_documents_and_channels.py` - Telegram/WhatsApp normalization and
  document prompt-injection warning behavior.
- `tests/test_reminder_workflow.py` - reminder extraction, approval gate,
  idempotency, workflow state reuse, outbox/event/trace writes.
- `tests/test_permissions_and_tenant.py` - tenant identity, P3/P5 approval,
  idempotent side effects, and cross-tenant memory/calendar isolation.
- `tests/test_durable_state.py` - terminal workflow state immutability.
- `tests/test_events_outbox.py` - event and outbox tenant/idempotency behavior.
- `tests/test_http_runtime.py` - runtime API health, readiness, tenant
  authority, approval resume, structured errors, and tenant-scoped queries.
- `tests/test_admin_dashboard.py` - local admin dashboard snapshots, local-only
  guard, tenant-scoped visibility, and error categorization.
- `tests/test_scheduler_worker.py` - reminder notification worker dispatch, P5
  approval policy, bounded loop, and tenant scope.
- `tests/test_telegram_notifications.py` - Telegram dispatcher P5 approval,
  replay idempotency, audio sends, and conflict detection.
- `tests/test_llm_adapters.py` - MiniMax LLM/TTS and OpenAI-compatible
  transcription adapters.
- `tests/test_persistence_config.py` and `tests/test_postgres_persistence.py` -
  Postgres backend selection, optional dependency behavior, schema, and DTO
  serialization.
- `tests/test_architecture_boundaries.py` - hexagonal import boundaries.
- `eval/cases.json` - curated golden, failure-mode, and regression fixtures
  mapped to contract `AC-*` and `FM-*` references.

## Layout

- `agents/personal_assistant/contract.md` - single-agent contract.
- `src/personal_assistant/domain/` - business models, policies, permissions,
  pure domain services, and exceptions.
- `src/personal_assistant/application/` - DTOs, use cases, service ports, and
  bounded runtime orchestration.
- `src/personal_assistant/adapters/` - inbound channel/API adapters, outbound
  provider adapters, persistence adapters, and local observability.
- `src/personal_assistant/contracts/` - A2A and future interoperability
  contracts that are not the internal runtime.
- `src/personal_assistant/infrastructure/` - configuration, composition root,
  FastAPI app, prompts, replies, admin, and worker wiring.
- `docs/`, `eval/`, and `tests/` - design records, public artifacts, runbooks,
  golden cases, and regression checks.
