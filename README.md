# Personal Assistant

Local-first scaffold for a production-grade personal assistant. The MVP is a
deterministic L2 workflow with bounded LLM use, tenant-scoped memory, durable-lite
state, CloudEvents-style events, outbox/inbox idempotency, and code-enforced
permission gates for external side effects.

## Architecture

```text
Telegram / WhatsApp webhooks
        -> Channel Gateway
        -> Message Normalizer
        -> Conversation Workflow
        -> AgentRuntimePort
        -> Tool Ports / MCP adapters
        -> Event Store + Outbox + Memory + Audit
        -> Workers: reminders, documents, notifications
```

MVP autonomy is L2: deterministic code owns the path, and LLM calls are bounded
activities for classification/extraction/summarization. A2A and MCP contracts are
prepared for interoperability, but neither protocol is the internal runtime.

## Local Verification

This scaffold avoids network-required dependencies. Pydantic v2 is used for
schemas; tests run with the Python standard library:

```bash
PYTHONPATH=src python3 -B -m unittest discover -s tests
PYTHONPATH=src python3 -B -m compileall src tests
```

If `pytest` is installed, the same test files are compatible with it.

## Layout

- `agents/personal_assistant/contract.md` - single-agent contract.
- `docs/adr/` - architecture decision records.
- `src/personal_assistant/shared/` - schemas, permissions, events, tracing,
  durable-lite stores, guardrails.
- `src/personal_assistant/reminders/` - deterministic reminder workflow.
- `src/personal_assistant/tools/` - tool contracts and local adapters.
- `src/personal_assistant/memory/` - tenant-scoped episodic/semantic memory.
- `src/personal_assistant/documents/` - small document extraction/summarization.
- `src/personal_assistant/scheduler/` - local reminder scheduler.
- `eval/` and `tests/` - golden, failure-mode, and regression checks.
