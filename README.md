# Personal Assistant

Local-first scaffold for a production-grade personal assistant. The MVP is a
deterministic L2 workflow with bounded LLM use, tenant-scoped memory, durable-lite
state, CloudEvents-style events, outbox/inbox idempotency, and code-enforced
permission gates for external side effects.

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

## Local Verification

This scaffold avoids network-required dependencies. Pydantic v2 is used for
schemas; tests run with the Python standard library:

```bash
PYTHONPATH=src python3 -B -m unittest discover -s tests
PYTHONPATH=src python3 -B -m compileall src tests
```

If `pytest` is installed, the same test files are compatible with it.
The unittest suite includes architecture-boundary checks that enforce the
hexagonal import direction from `domain` inward to `application` and outward to
`adapters`.

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
