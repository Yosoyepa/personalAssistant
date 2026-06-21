# ADR-002: L2 Deterministic Workflow for the Personal Assistant MVP

## Status

Accepted

## Date

2026-06-20

## Context

The assistant must handle a small set of personal workflows: Telegram conversations, reminders, calendar events, small-document questions, minimal memory, and notifications through an outbox. These tasks have enumerable paths and well-known preconditions. Reliability, tenant isolation, and side-effect control matter more than open-ended autonomy.

## Decision

Use autonomy level L2 for the MVP: deterministic workflow with bounded LLM steps.

The assistant workflow is:

```text
1. Receive normalized Telegram command.
2. Resolve principal and tenant from authenticated channel identity.
3. Validate input schema and guardrails.
4. Select scoped context for the route.
5. Classify intent with deterministic rules first, LLM fallback only when needed.
6. Route to a deterministic handler.
7. Use MiniMax through LLMProvider only for bounded extraction, summarization, or drafting.
8. Validate structured output.
9. Write durable state changes and outbox events transactionally.
10. Emit trace events.
11. Return a response draft or escalation.
```

The workflow does not include an autonomous loop. The model cannot invent tools, select tenants, bypass permissions, or directly execute side effects.

## Intent Routes

| Intent | Handler | LLM use |
|---|---|---|
| `small_talk` | Draft direct reply | Optional response drafting |
| `reminder.create` | Extract date/time, validate, write reminder | Date/time extraction when deterministic parsing is uncertain |
| `reminder.list` | Query tenant-scoped reminders | None |
| `reminder.update` | Resolve target, validate change, write update | Clarifying question drafting |
| `reminder.cancel` | Resolve target, mark canceled | Clarifying question drafting |
| `calendar.create` | Extract event fields, validate, write local event | Field extraction when needed |
| `calendar.list` | Query tenant-scoped events | None |
| `document.ingest` | Store small document metadata and content | Optional summarization |
| `document.query` | Retrieve tenant-scoped snippets, answer with citations to snippets | Answer drafting over selected snippets |
| `memory.remember` | Store explicit preference or stable fact | Optional normalization |
| `memory.forget` | Delete selected memory item | None; destructive deletion requires explicit target confirmation |
| `unsupported` | Ask clarifying question or decline | Optional response drafting |

## State and Durability

Each run writes a durable state record with:

- `run_id`
- `agent_id`
- `principal_id`
- `tenant_id`
- `channel`
- `input_message_id`
- `intent`
- `status`
- `idempotency_key`
- selected context references
- output summary
- error code when failed

Side effects are represented as outbox rows with:

- `event_id`
- `tenant_id`
- `principal_id`
- `run_id`
- `event_type`
- `payload`
- `permission_tier`
- `approval_status`
- `idempotency_key`
- `dispatch_status`

Webhook retries must reuse the same idempotency key and must not create duplicate reminders, calendar items, memory entries, documents, or Telegram deliveries.

## Permission Model

| Action | Tier | MVP handling |
|---|---|---|
| Read scoped user data | P0 | Allowed after principal resolution |
| Draft response | P1 | Allowed |
| Internal write to reminders, calendar, documents, memory, or outbox | P2 | Allowed when schema, tenant, and route checks pass |
| External calendar write | P3 | Not active in MVP |
| External Telegram delivery | P5 | Handled by dispatcher, not by the agent |
| Destructive operations | P6 | Not active, except explicit memory/document deletion after target confirmation and audit |

## Escalation and Human-in-the-Loop

The workflow must ask or escalate when:

- no principal or tenant can be resolved;
- date, time, timezone, target reminder, target event, or target memory item is ambiguous;
- a write would affect a tenant or principal different from the authenticated principal;
- a requested operation is outside the active MVP routes;
- an external side effect other than approved direct Telegram reply is requested;
- a retrieved document contains instructions that conflict with the system contract;
- MiniMax returns malformed structured output after retry.

## Evals and Release Gate

The MVP stays at L2 until these checks pass:

- golden evals for each selected intent route;
- failure-mode evals for tenant leakage, duplicate webhook retry, ambiguous time, prompt injection in documents, malformed LLM output, and disabled MCP/A2A invocation;
- regression slot for every production incident;
- at least 90 percent pass rate on the curated route-level eval set before considering L3.

LLM-as-judge is not needed for permission, tenancy, idempotency, schema validity, or route selection when code assertions are sufficient.

## Consequences

Positive consequences:

- Predictable behavior for personal productivity workflows.
- Clear side-effect boundaries through events and outbox.
- Lower cost than autonomous loops.
- Easier to test tenant isolation and idempotency.
- A2A and MCP remain future-compatible without expanding MVP risk.

Negative consequences:

- Some open-ended assistant requests will be declined or converted into clarifying questions.
- The route catalog must be maintained as features grow.
- Complex planning tasks may require a later L3 design and new eval evidence.

## Acceptance Criteria

| Criterion | Probe |
|---|---|
| The ADR declares autonomy level L2. | `rg -n "autonomy level L2|L2 Deterministic" docs/adr/ADR-002-l2-mvp.md` |
| The workflow includes principal and tenant resolution before agent action. | `rg -n "Resolve principal and tenant" docs/adr/ADR-002-l2-mvp.md` |
| MiniMax access is bounded through `LLMProvider`. | `rg -n "MiniMax through LLMProvider" docs/adr/ADR-002-l2-mvp.md` |
| Side effects are represented as outbox rows. | `rg -n "outbox rows" docs/adr/ADR-002-l2-mvp.md` |
| The release gate requires 90 percent route-level eval pass rate before L3. | `rg -n "90 percent.*before considering L3" docs/adr/ADR-002-l2-mvp.md` |

