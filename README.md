# Personal Assistant

**Local-first personal assistant runtime, built as a deterministic L2 workflow.** Python code owns routing, state, permissions, idempotency, and side effects; LLM calls are bounded provider activities used only when deterministic logic needs help.

[![CI](https://github.com/Yosoyepa/personalAssistant/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Yosoyepa/personalAssistant/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/Yosoyepa/personalAssistant?include_prereleases&sort=semver&label=release)](https://github.com/Yosoyepa/personalAssistant/releases)
[![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

This is intentionally **not** a generic autonomous agent loop. It is a single-assistant harness for personal productivity workflows — reminders, calendar, and notifications — with tenant-scoped memory, explicit approval gates, local-first observability, and optional durable Postgres storage.

> [!NOTE]
> Current release: [`v0.2.0-alpha.2`](https://github.com/Yosoyepa/personalAssistant/releases/tag/v0.2.0-alpha.2) — hardened alpha (prerelease). Suitable for controlled single-operator deployments; not approved for unattended multi-tenant service. See the [release notes](docs/releases/v0.2.0-alpha.2.md).

## Features

### Channels

- **WhatsApp** (inbound + outbound): webhook with Meta challenge validation and HMAC SHA-256 signature verification (`X-Hub-Signature-256`), immediate replies to allow-listed users via the Graph API, and proactive reminder delivery. Setup in `docs/runbook/whatsapp.md`.
- **Telegram**: webhook normalization with secret-token verification and user allowlist, command routing, text replies, optional voice transcription (OpenAI-compatible speech-to-text) and MiniMax TTS audio replies. Setup in `docs/runbook/telegram.md`.
- **Multi-channel routing**: `ChannelNotificationRouter` delivers scheduled reminders over Telegram or WhatsApp through one outbox pipeline.

### Operations

- **Admin panel** (`/admin`): loopback-only, Bearer-authenticated operations surface — snapshots, traces, outbox, scheduler, guardrail metrics — with explicit **P5 approve/deny actions** and **`uncertain` delivery reconciliation** (`delivered` / `retry`). Guide in `docs/runbook/admin-dashboard.md`.
- **Durable delivery worker**: Postgres-backed outbox with `SKIP LOCKED` coordination, worker heartbeats, and ambiguous-outcome semantics that bias against duplicate sends. Notes in `docs/runbook/persistence.md`.
- **Zero-downtime truth**: `/livez` and `/readyz` probes (`/healthz` remains as a deprecated alias emitting `Deprecation: true`).

### Security model

- The core invariant: `tenant_id` comes from the authenticated `Principal` — never from message text, tool arguments, LLM output, request bodies, or retrieved documents.
- Bidirectional guardrails at every entry point: prompt-injection, PII, and content-policy scans on inputs and outputs, with sanitized `guardrail.checked` trace events.
- Deny-by-default outbound egress allowlist (ADR-004 layer A) and a hardened container profile with read-only root filesystem and dropped capabilities (ADR-004 layer B).

### Quality & governance (WCT)

The repository is developed under the **Well Code Contract** harness (`tools/wct`), with 17 enforced gates covering architecture boundaries, strict typing, branch coverage, mutation budgets, dead code, DRY, SAST, secrets, and Gherkin acceptance — plus ratchets that can only tighten. `AGENTS.md` is generated from `governance/rules/*.yaml` by `wct rules build`; governance files are integrity-locked with an audited, human-approved bless procedure.

Current evidence (reproducible, commands in the release notes):

- **1015 tests passed**, 3 skipped, 396 subtests — full suite against PostgreSQL 16
- **299/299** deterministic behavioral eval cases
- **90% branch coverage** (10619 statements, 2492 branches)
- **17/17** WCT commit-tier gates green

## Architecture

```text
WhatsApp / Telegram webhook or local runtime request
        -> Channel normalization
        -> Trusted principal + tenant resolution
        -> Conversation command service
        -> Deterministic reminder/calendar workflow
        -> Ports for LLM, transcription, TTS, calendar, scheduler, events,
           outbox, approvals, memory, notifications, traces
        -> In-memory or Postgres adapters
        -> Workers for due reminders and notification dispatch
```

The codebase follows a strict dependency rule (`entrypoints -> adapters -> application -> domain`, enforced by import-linter): frameworks, ORMs, and SDKs live only in `adapters/` and `infrastructure/`; ports are defined where they are used. The executable agent contract is in `agents/personal_assistant/contract.md`, and accepted decisions are recorded in `docs/adr/`.

## Getting started

### Prerequisites

- Python 3.11+ and [uv](https://docs.astral.sh/uv/)
- Optional: PostgreSQL 16 for the durable backend (memory mode is the default and disposable)

### Install

```bash
uv sync --all-extras --group dev
```

### Configure

Configuration comes from the process environment and an optional local `.env` file — copy `.env.example` as the template.

| Variable | Purpose |
|---|---|
| `APP_ENV_FILE` | Env file path; set to `disabled` for hermetic tests |
| `PERSISTENCE_BACKEND` | `memory` (default) or `postgres` |
| `DATABASE_URL` | Postgres DSN, required when `PERSISTENCE_BACKEND=postgres` |
| `ADMIN_TOKEN` | Bearer token for the loopback admin surface |
| `ASSISTANT_TENANT_ID` / `ASSISTANT_TIMEZONE` | Default tenant and timezone |

Channel variables (`TELEGRAM_*`, `WHATSAPP_*`, `TRANSCRIPTION_*`, `TTS_*`) are documented in their runbooks.

> [!IMPORTANT]
> Keep every credential out of git: tokens, webhook secrets, API keys, and `DATABASE_URL` live only in the environment or the git-ignored `.env`. Secret handling and rotation procedures are in `docs/runbook/hardened-local-deployment.md`.

### Run

```bash
uv run uvicorn personal_assistant.infrastructure.http:app --host 127.0.0.1 --port 8000
```

```bash
curl -sS http://127.0.0.1:8000/livez | python3 -m json.tool
curl -sS http://127.0.0.1:8000/readyz | python3 -m json.tool
```

A hardened container profile is available via the multi-stage `Dockerfile` and `deploy/compose.yaml`.

### Verify

```bash
APP_ENV_FILE=disabled uv run pytest -q                 # full suite
uv run python -m personal_assistant.evals --suite eval/cases   # behavioral corpus
uv run python -m tools.wct gate --tier fast            # governance gates (needs PYTHONPATH=.)
```

## Documentation

| Document | Contents |
|---|---|
| `docs/runbook/telegram.md` | BotFather, webhook, audio/TTS, local runtime notes |
| `docs/runbook/whatsapp.md` | Meta app, webhook subscription, HMAC verification, smoke test, troubleshooting |
| `docs/runbook/admin-dashboard.md` | Admin panel, P5 actions, uncertain reconciliation, JSON endpoints |
| `docs/runbook/persistence.md` | Memory/Postgres backends, schema, idempotency, worker CLI |
| `docs/runbook/hardened-local-deployment.md` | HTTPS webhook-only boundary, secrets, rotation, rollback |
| `docs/runbook/minimax.md` | MiniMax LLM and TTS provider notes |
| `docs/runbook/v0.2.0-alpha.1.md` | Installation, migration, and smoke procedure for the alpha line |
| `docs/adr/` | Accepted architecture decisions (ADR-001 to ADR-006) |
| `docs/architecture/` | Architecture reviews and design notes |
| `docs/development/` | Maintainer workflow, hardening phase logs, governance setup |
| `docs/policy/` | Ratified content policy |
| `eval/README.md` | Behavioral eval harness and corpus format |
| `agents/personal_assistant/contract.md` | Executable single-agent contract |

## Project layout

- `src/personal_assistant/domain/` — models, policies, permissions, pure domain services
- `src/personal_assistant/application/` — DTOs, use cases, ports, bounded orchestration
- `src/personal_assistant/adapters/` — channel/API adapters, provider adapters, persistence
- `src/personal_assistant/infrastructure/` — configuration, composition root, FastAPI app, admin, workers
- `tests/`, `eval/`, `features/` — regression suite, golden corpus, Gherkin acceptance
- `governance/`, `tools/wct/` — WCT policy, rules, integrity lock, and the gate harness
- `docs/` — runbooks, ADRs, release notes, development logs

## Known limitations

- WhatsApp supports plain text, inbound voice/audio transcription, and proactive delivery — non-audio media (images, documents, video) returns an explicit unsupported reply.
- The admin surface is loopback-only (`127.0.0.1` / `::1`); there is no production auth beyond it.
- The durable delivery worker requires PostgreSQL.
- Ambiguous delivery outcomes stop at `uncertain` and require manual operator reconciliation by design.
- No external calendar sync, OAuth credential storage, semantic vector memory, or active MCP/A2A runtime path yet.
