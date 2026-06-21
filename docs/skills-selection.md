# Personal Assistant MVP - Capability Selection

## Purpose

This document records the capability set for the first MVP of the personal assistant. The product is a single personal-assistant workflow, not a multi-agent runtime. The design keeps the first release at autonomy level L2: deterministic workflow with bounded LLM calls.

## Selected Runtime Capabilities

| Capability | MVP decision | Primary owner | Notes |
|---|---|---|---|
| Telegram channel | Selected as primary ingress and egress | Channel adapter | Webhook input is normalized into an authenticated principal before the agent runs. The agent returns reply drafts and outbox intents; the channel adapter owns delivery. |
| Principal and tenancy | Required | Identity module | `tenant_id` is derived from the authenticated principal. The model, prompt, request body, and tool arguments are never trusted for tenant selection. |
| LLM provider | MiniMax through `LLMProvider` | Provider adapter | The agent depends on a provider interface only. MiniMax is the initial adapter, not a domain dependency. |
| Reminders | Selected | Reminder module | Create, list, update, complete, and cancel personal reminders. Writes are internal durable state with idempotency keys. |
| Calendar | Selected in local-first mode | Calendar module | Create, list, update, and cancel personal calendar items in the app store. External calendar sync is a future integration behind the same port and requires approval policy. |
| Small documents | Selected | Document module | Ingest and retrieve small user-provided documents only. Uploaded or retrieved content is untrusted context and cannot issue instructions. |
| Minimal memory | Selected | Memory module | Store explicit user preferences and stable facts only. No raw chat transcript dumping. Memory is tenant-scoped. |
| Events and outbox | Required | Event/outbox module | All side-effect intents are written transactionally. Dispatchers execute only after policy checks and idempotency verification. |
| A2A | Prepared only | Integration ports | Message envelope shape can be added later. No A2A runtime dependency in the MVP request path. |
| MCP | Prepared only | Tool registry ports | Tool contracts can be represented later as MCP tools. No MCP server is active in the MVP request path. |

## Explicitly Deferred

| Deferred item | Reason | Re-entry condition |
|---|---|---|
| Autonomous L3/L4 agent loop | The MVP intents have enumerable paths. | L2 eval pass rate is at least 90 percent and a real workflow cannot be modeled deterministically. |
| Multi-agent orchestration | The MVP is depth-first and stateful. | A future use case is breadth-first, independently parallelizable, and worth the cost and coordination overhead. |
| External email or third-party messaging | Higher blast radius than Telegram direct replies. | Tool contract, approval gate, egress checks, and regression evals exist. |
| External calendar mutation | Requires OAuth scopes, token isolation, and rollback semantics. | External calendar adapter has scoped tokens, idempotency, approval policy, and trace coverage. |
| Large document RAG | Not needed for the small-doc MVP. | Document size and retrieval requirements exceed simple tenant-scoped storage. |
| General web browsing | Introduces untrusted public content and egress risk. | A separate tool contract, injection guardrails, and allowlisted domains exist. |

## Capability Boundaries

The assistant may classify the user request, ask clarifying questions, draft a Telegram response, and request internal state changes through outbox events. It must not perform external mutations directly.

The Telegram adapter authenticates the incoming update and resolves the principal before invoking the assistant. If the principal cannot be resolved to exactly one `tenant_id`, the run fails closed.

The LLM is used for bounded tasks: intent classification, natural-language date extraction, summarization of small documents, and response drafting. Business decisions, permission checks, tenant scoping, idempotency, and dispatch are deterministic code.

## Minimal Context Pack

Each run should select only:

| Context source | Include when | Exclude when |
|---|---|---|
| Agent contract | Always | Never |
| Current Telegram message | Always | Never |
| Principal record | Always | No valid principal exists, because the run should stop |
| Relevant reminders/calendar items | Intent requires scheduling context | Intent does not mention time, tasks, or calendar |
| Relevant small-document snippets | Intent references uploaded documents or asks a document question | User request is unrelated to documents |
| Relevant memory | A stored preference can change handling | Preference is stale, unrelated, or conflicts with current user input |
| Tool schemas | Only active tools for the selected route | Tools outside the route, A2A, or MCP |

Context utilization target is below 40 percent of the selected model context window. Large artifacts are referenced by id and summarized, not embedded wholesale.

## Permission Summary

| Operation | Tier | Approval policy |
|---|---|---|
| Read principal, reminders, calendar, documents, memory | P0 | No approval, tenant scoped |
| Draft response or plan | P1 | No approval |
| Write internal reminder/document/memory state | P2 | No approval when actor is the owning principal and schema checks pass |
| Write calendar event through calendar tool | P3 | Requires trusted out-of-band approval even in local-first mode |
| Queue outbox event | P2 | No approval to queue; dispatcher policy decides execution |
| External Telegram delivery | P5 | Outside agent ownership; this scaffold requires trusted approval for all P5 sends |
| External calendar mutation | P3 | Deferred; requires explicit approval and idempotency |
| Targeted memory or document deletion | P6 | Requires explicit target confirmation and audit |
| Bulk destructive deletion | P6 | Not in MVP |

## A2A and MCP Preparation

The MVP should keep integration seams without making them runtime dependencies:

- Define tool capabilities as contracts that can later map to MCP.
- Keep tool inputs and outputs schema-validatable.
- Use a stable message envelope shape for future A2A handoffs.
- Keep `agent_id`, `run_id`, `tenant_id`, `principal_id`, and `trace_id` on every event.
- Fail any MVP run that attempts to invoke `mcp.*` or `a2a.*` capabilities.

## Acceptance Checks

| Check | Probe |
|---|---|
| The MVP declares Telegram as the primary channel. | `rg -n "Telegram channel \\| Selected as primary" docs/skills-selection.md` |
| MiniMax is behind `LLMProvider`, not a domain dependency. | `rg -n "MiniMax through .*LLMProvider" docs/skills-selection.md` |
| `tenant_id` is derived from the authenticated principal. | `rg -n "tenant_id.*authenticated principal" docs/skills-selection.md` |
| A2A is prepared but not active in the MVP path. | `rg -n "No A2A runtime dependency" docs/skills-selection.md` |
| MCP is prepared but not active in the MVP path. | `rg -n "No MCP server is active" docs/skills-selection.md` |
