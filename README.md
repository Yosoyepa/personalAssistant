# Personal Assistant

Local-first scaffold for a production-grade personal assistant. The MVP is a
deterministic L2 workflow with bounded LLM use, tenant-scoped memory, durable-lite
state, CloudEvents-style events, outbox/inbox idempotency, and code-enforced
permission gates for external side effects.

## Current Status

This repository is a local-first Python package with optional FastAPI surfaces.
The code supports in-memory composition, Telegram webhook normalization,
conversation command routing, reminder workflow execution, local
calendar/scheduler adapters, tenant-scoped memory, trace recording, a runtime
API, a local-only admin dashboard, a reminder notification worker,
Anthropic-compatible LLM fallback extraction, optional voice transcription, and
deterministic tests.

Runtime configuration is read from process environment by
`src/personal_assistant/infrastructure/config.py`; `.env` files are not loaded
automatically. Telegram tokens, ngrok authtokens, webhook secrets, MiniMax keys,
OAuth tokens, and any other credentials must stay outside git. `.gitignore`
excludes `.env` and `.env.*` by default.

## Architecture

```text
Telegram webhook
WhatsApp adapter prepared but inactive
        -> Channel Gateway
        -> Message Normalizer
        -> Conversation Workflow
        -> AgentRuntimePort
        -> Tool Ports / MCP adapters
        -> Event Store + Outbox + Memory + Audit
        -> Workers: reminders, documents, notifications
```

MVP autonomy is L2: deterministic code owns the path, and LLM calls are bounded
activities for classification/extraction/summarization. WhatsApp, A2A, and MCP
contracts/adapters are prepared for interoperability, but they are not the
internal runtime path for the MVP.

The executable contract is in `agents/personal_assistant/contract.md`. The most
important local invariant is that `tenant_id` comes from the authenticated
`Principal`, never from Telegram text, tool arguments, LLM output, or retrieved
documents.

## Local Setup

Python 3.11 or newer is required by `pyproject.toml`.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
```

If you want the optional runtime/admin API dependencies:

```bash
python -m pip install -e '.[api,test]'
```

The current `test` extra includes FastAPI and httpx for the optional HTTP tests.
If network dependency installation is not available, tests that do not require
FastAPI can still run with `PYTHONPATH=src` as long as Pydantic v2 is installed.

## Local Verification

The local gate does not require external network services. Pydantic v2 is used
for schemas; the suite runs under `unittest`, with optional FastAPI/httpx tests
skipped when those dependencies are unavailable:

```bash
PYTHONPATH=src python3 -B -m unittest discover -s tests
PYTHONPATH=src python3 -B -m compileall src tests
python3 -m json.tool eval/cases.json >/dev/null
```

If `pytest` is installed, the same test files are compatible with it.
The unittest suite includes architecture-boundary checks that enforce the
hexagonal import direction from `domain` inward to `application` and outward to
`adapters`.

Optional pytest command:

```bash
PYTHONPATH=src python3 -B -m pytest
```

Verification criteria:

- All unit tests pass.
- `compileall` succeeds for `src` and `tests`.
- `eval/cases.json` parses as valid JSON.
- Telegram normalizer keeps user-supplied `tenant_id=...` as inert text and does
  not add tenant authority to `NormalizedMessage`.
- Reminder creation with approval creates one local calendar event, one
  scheduled reminder, one event-store row, one outbox row, and trace events for
  `agent.started`, `context.selected`, `guardrail.checked`, `tool.called`, and
  `agent.completed`.
- Duplicate webhook delivery with the same idempotency key does not duplicate
  calendar, reminder, event-store, or outbox records.
- P3/P5 side effects require approval and remain idempotent.
- Cross-tenant memory and calendar canary data is not returned.

## Telegram Runbook

The local Telegram operational guide is in `docs/runbook/telegram.md`.

Current implementation boundary:

- Present: `normalize_telegram_webhook(payload, tenant_id=...)`, local
  `build_container()`, `ConversationCommandService`, `ReminderWorkflow`,
  `personal_assistant.infrastructure.http:app` for runtime endpoints, local-only
  admin dashboard, Telegram webhook bridge, reminder worker, Anthropic-compatible
  LLM adapter, OpenAI-compatible transcription adapter, in-memory traces and
  stores.
- Not present yet: durable database, production deployment hardening, OAuth
  storage, external calendar sync.
- Forbidden by contract: direct `telegram.send` from the agent, MVP `mcp.*` or
  `a2a.*` tool calls, third-party messaging, financial actions, destructive bulk
  deletion, secret reads, and tenant authority from untrusted text.

## Test Checklist

Run the local gate before changing runtime behavior or committing docs that
claim a behavior exists:

```bash
PYTHONPATH=src python3 -B -m unittest discover -s tests
PYTHONPATH=src python3 -B -m compileall src tests
python3 -m json.tool eval/cases.json >/dev/null
```

Coverage map:

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
- `tests/test_http_runtime.py` - runtime API health, readiness, tenant authority,
  approval resume, structured errors, and tenant-scoped queries.
- `tests/test_admin_dashboard.py` - local admin dashboard snapshots, local-only
  guard, and tenant-scoped visibility.
- `tests/test_scheduler_worker.py` - reminder notification worker dispatch,
  P5 approval policy, bounded loop, and tenant scope.
- `tests/test_telegram_notifications.py` - Telegram dispatcher P5 approval,
  replay idempotency, and conflict detection.
- `tests/test_architecture_boundaries.py` - hexagonal import boundaries.
- `eval/cases.json` - curated golden, failure-mode, and regression fixtures
  mapped to contract `AC-*` and `FM-*` references.

## Conventional Commit Plan

Recommended logical commits for this documentation/eval phase:

```text
docs(readme): document local setup and verification gates
docs(runbook): add Telegram BotFather and ngrok local runbook
test(eval): expand assistant contract eval cases
```

If these changes are kept as one commit, use:

```text
docs: add local runbook and eval verification plan
```

## Layout

- `agents/personal_assistant/contract.md` - single-agent contract.
- `docs/adr/` - architecture decision records.
- `src/personal_assistant/domain/` - business models, policies, events,
  permissions, durable state, exceptions, and pure domain services.
- `src/personal_assistant/application/` - use cases, DTOs, service ports, and
  bounded runtime orchestration:
  `dto/`, `ports/`, and `use_cases/`.
- `src/personal_assistant/adapters/` - inbound channel/API adapters, outbound
  local tools, scheduler implementations, and persistence adapters:
  `inbound/`, `outbound/`, `persistence/`, and `observability/`.
- `src/personal_assistant/contracts/` - A2A and future interoperability
  contracts that are not the internal runtime.
- `src/personal_assistant/infrastructure/` - composition root and local wiring.
- `docs/architecture/hexagonal-refactor-analysis.md` - latest architecture
  review, findings, and follow-up backlog.
- `eval/` and `tests/` - golden, failure-mode, and regression checks.
