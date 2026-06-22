# ADR-001: Modular Monolith for the Personal Assistant MVP

## Status

Accepted

Superseded for package layout by
[ADR-003: Hexagonal Boundaries Inside the Modular Monolith](ADR-003-hexagonal-boundaries.md).

## Date

2026-06-20

## Context

The MVP needs a personal assistant that can receive Telegram messages, use MiniMax through a provider abstraction, manage reminders and calendar data, work with small documents, keep minimal memory, and emit events through an outbox. The system also needs tenant isolation from the first version. Future A2A and MCP integrations should be possible, but they should not become the main runtime path for the MVP.

The repo is early-stage, so the architecture should optimize for clarity, reliable tests, and low operational overhead. The first release does not need independent scaling boundaries for reminders, memory, documents, or provider calls.

## Decision

Build the MVP as a modular monolith with explicit internal module boundaries and stable ports.
ADR-003 refines these boundaries into five source layers: `domain`,
`application`, `adapters`, `contracts`, and `infrastructure`.

The original logical module map is:

| Module | Responsibility |
|---|---|
| `channels.telegram` | Normalize Telegram webhooks, authenticate the Telegram account, and deliver approved outbox messages. |
| `identity` | Resolve `principal_id`, `tenant_id`, roles, and permission grants from authenticated channel identity. |
| `agents.personal_assistant` | Run the L2 assistant workflow and produce schema-valid results plus outbox intents. |
| `llm` | Expose `LLMProvider`; the first concrete adapter is MiniMax. |
| `reminders` | Own reminder commands, state, recurrence policy, and due-reminder queries. |
| `calendar` | Own local calendar events and availability queries. |
| `documents` | Own small document ingestion, metadata, tenant-scoped retrieval, and snippets. |
| `memory` | Own minimal explicit preferences and stable facts. |
| `events` | Own domain events, transactional outbox, idempotency keys, and dispatch status. |
| `observability` | Own traces, structured errors, and run metrics. |
| `integrations` | Hold future A2A and MCP port definitions without activating them in the MVP path. |

Module dependencies flow inward through interfaces:

```text
channels.telegram
  -> identity
  -> agents.personal_assistant
       -> llm.LLMProvider
       -> reminders port
       -> calendar port
       -> documents port
       -> memory port
       -> events outbox port
       -> observability port
```

Cross-module writes go through application services or ports. Direct table access across modules is not part of the contract. All reads and writes receive `tenant_id` from the principal context, not from model output.

Physical package placement now follows ADR-003:

| ADR-001 logical module | ADR-003 physical layer |
|---|---|
| `channels.telegram` | `adapters.inbound.channels.telegram` |
| `identity` | `domain.common.identity` plus `adapters.inbound.auth` for provider claims |
| `agents.personal_assistant` | `application.use_cases` plus `agents/personal_assistant/contract.md` |
| `llm` | `application.ports.services.LLMProvider` and future outbound adapter |
| `reminders` | `domain.reminders`, `application.dto.reminders`, `application.use_cases.reminders` |
| `calendar` | `application.ports.calendar`, `adapters.outbound.calendar` |
| `documents` | `application.dto.documents`, `application.use_cases.documents` |
| `memory` | `domain.memory`, `adapters.persistence.memory` |
| `events` | `application.dto.events`, `application.ports.events`, `adapters.persistence.in_memory` |
| `observability` | `application.dto.tracing`, `application.ports.observability`, `adapters.observability` |
| `integrations` | `contracts` and future adapters |

## Consequences

Positive consequences:

- One deployable unit keeps the MVP simple to run, test, trace, and debug.
- Transactional writes can atomically update state and append outbox events.
- Tenant isolation can be enforced consistently in repositories and services.
- MiniMax can be replaced by another provider without changing domain modules.
- A2A and MCP can be added later through prepared ports and contracts.

Negative consequences:

- Modules share one deployment cadence.
- Bad internal boundaries can degrade into an unstructured monolith if interfaces are bypassed.
- Long-running dispatchers must be carefully separated from synchronous request handling.

## Alternatives Considered

### Microservices

Rejected for the MVP. They add distributed transactions, service discovery, network retries, deployment overhead, and cross-service tracing before the product has proven module boundaries.

### Multi-agent runtime

Rejected for the MVP. The workflows are stateful and depth-first. A deterministic L2 workflow is simpler, cheaper, and easier to test.

### Provider-specific LLM coupling

Rejected. MiniMax is the first implementation, but domain code should target `LLMProvider` so provider behavior can be tested and swapped.

### Direct tool dispatch from the agent

Rejected. The agent should emit schema-valid intents and outbox events. Dispatchers own side effects, approval policy, and idempotency.

## Implementation Rules

- Every request path starts with principal resolution.
- No module accepts a model-supplied `tenant_id` as authority.
- Repositories enforce tenant scope below the LLM and below the agent workflow.
- Every side-effecting action has an idempotency key.
- Outbox events are appended in the same transaction as the state change that caused them.
- Telegram delivery reads from the outbox; the assistant does not call Telegram send APIs directly.
- A2A and MCP port definitions may exist, but runtime invocation is disabled for the MVP.
- Provider-specific MiniMax fields stay inside the MiniMax adapter.

## Acceptance Criteria

| Criterion | Probe |
|---|---|
| The architecture records modular monolith as the accepted decision. | `rg -n "^# ADR-001: Modular Monolith" docs/adr/ADR-001-modular-monolith.md` |
| The module map includes Telegram, identity, personal assistant, LLM, reminders, calendar, documents, memory, events, observability, and integrations. | `rg -n "channels.telegram|identity|agents.personal_assistant|llm|reminders|calendar|documents|memory|events|observability|integrations" docs/adr/ADR-001-modular-monolith.md` |
| MiniMax is isolated behind `LLMProvider`. | `rg -n "MiniMax.*LLMProvider|LLMProvider.*MiniMax" docs/adr/ADR-001-modular-monolith.md` |
| Tenant authority comes from principal context. | `rg -n "tenant_id.*principal" docs/adr/ADR-001-modular-monolith.md` |
| A2A and MCP are prepared but disabled in the MVP runtime path. | `rg -n "runtime invocation is disabled.*MVP" docs/adr/ADR-001-modular-monolith.md` |
