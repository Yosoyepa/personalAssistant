# ADR-003: Hexagonal Boundaries Inside the Modular Monolith

## Status

Accepted

## Date

2026-06-20

## Context

ADR-001 chooses a modular monolith for the MVP. The initial scaffold kept the
right production concerns, but several modules mixed domain rules, transport
schemas, local adapters, and persistence details in the same package. That shape
would make the assistant hard to evolve once Telegram, Calendar, memory,
documents, and future MCP/A2A integrations start changing independently.

The MVP still should not become a distributed system or an agent framework
runtime. It needs deterministic application workflows with LLM calls and tools
behind explicit contracts.

## Decision

Use hexagonal architecture inside the monolith:

| Layer | Responsibility |
|---|---|
| `domain` | Business language, domain models, policies, exceptions, events, durable state, permissions, and pure services. |
| `application` | Use cases, DTOs, ports, workflow orchestration, and runtime services. |
| `adapters` | Inbound channel/API adapters, outbound tools, local schedulers, and persistence implementations. |
| `contracts` | External interoperability contracts such as A2A and future protocol artifacts. |
| `infrastructure` | Composition root and local wiring for deployable/runtime concerns. |

Dependency direction is inward:

```text
adapters -> application -> domain
infrastructure -> adapters/application/domain
contracts -> domain/application DTOs only when needed
```

Domain code must not import adapters. Application use cases depend on ports and
DTOs, not concrete local implementations. Adapters translate transport payloads
or external tool calls into application DTOs and implement application ports.

## Implementation Rules

- Business rules live in `domain` or application use cases, never in inbound
  transport adapters.
- Pydantic DTOs for request/response boundaries live under `application/dto`.
- Domain models may use Pydantic when validation is part of the domain
  contract, but they must not import adapter modules.
- Application ports live under `application/ports` and describe behavior needed
  by use cases.
- Concrete in-memory stores, local Calendar, notifications, and schedulers live
  under `adapters`.
- `tenant_id` authority comes only from `Principal`.
- Write actions continue to require code-enforced approval grants and
  idempotency keys.
- Global structured exceptions live in `domain/common/exceptions.py`; inbound
  web frameworks may map them later, but they do not own the error vocabulary.
- A2A and MCP remain contracts/adapters, not the internal runtime.

## Consequences

Positive consequences:

- The reminder workflow can be tested against ports without knowing whether the
  calendar is local, Google Calendar, or MCP-backed.
- Transport schemas, DTOs, domain models, and persistence records have clearer
  ownership.
- Tenant isolation and permission checks stay below the LLM path and close to
  state-changing operations.
- New adapters can be added without changing domain rules.

Negative consequences:

- There are more packages than a feature-folder scaffold.
- Some early modules need mechanical moves and import churn before the shape is
  visible.
- Over-splitting tiny files is possible, so abstractions should stay aligned
  with actual use cases.

## Acceptance Criteria

| Criterion | Probe |
|---|---|
| Domain imports do not point to adapters. | `rg -n "personal_assistant\\.adapters" src/personal_assistant/domain` returns no matches. |
| Application workflow depends on ports, not local tools. | `rg -n "Local(Calendar|Notification)|InMemory|ReminderScheduler" src/personal_assistant/application` returns no matches. |
| Local tool implementations live in adapters. | `rg -n "class LocalCalendarTool|class LocalNotificationTool|class ReminderScheduler" src/personal_assistant/adapters` |
| Global errors are domain-owned. | `test -f src/personal_assistant/domain/common/exceptions.py` |
| A2A contracts are not runtime modules. | `test -f src/personal_assistant/contracts/a2a.py` |
| Physical package roots stay limited to the five layers plus root `__init__.py`. | `PYTHONPATH=src python3 -B -m unittest tests.test_architecture_boundaries` |
