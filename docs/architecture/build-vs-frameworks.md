# Build From Scratch Vs Agent Frameworks

## Status

Accepted for the MVP.

## Decision

Build the personal assistant runtime from scratch as a small deterministic L2
harness instead of adopting OpenClaw, Hermes Agent, or OpenHands as the core
runtime.

This is not a rejection of those projects. They are useful references and may be
integrated or revisited later. For the current assistant, the dominant
constraint is product-specific control over identity, tenant scope, permission
tiers, approvals, idempotency, persistence, and local observability.

## Why Build The Harness

The MVP has a known path:

1. Receive a Telegram or trusted runtime request.
2. Resolve `Principal` and `tenant_id` from trusted channel/runtime state.
3. Route through deterministic commands first.
4. Use bounded LLM calls only for classification, extraction, or drafting.
5. Validate schema and permissions in code.
6. Persist workflow state, events, outbox, approvals, memory, scheduler jobs,
   calendar state, and traces through local ports.
7. Dispatch side effects only through approved adapters.

That shape maps cleanly to plain Python, FastAPI, Pydantic DTOs, explicit ports,
and in-memory/Postgres adapters. Adding a larger agent framework before this
contract stabilizes would add abstraction before the core trust boundary is
finished.

## Alternatives Considered

### OpenClaw

Official links:

- <https://github.com/openclaw/openclaw>
- <https://docs.openclaw.ai/gateway/sandboxing>

OpenClaw is relevant because it targets agent execution with a gateway and
sandboxing model. That is useful for safely running agent actions in isolated
environments.

It is not the right core runtime for this MVP because the assistant's immediate
risk is not arbitrary sandboxed execution. The immediate risk is tenant
authority, Telegram identity, P3/P5 approvals, replay-safe side effects, and
local persistence. Those constraints belong in the assistant contract and local
application services, not primarily in an execution sandbox.

### Hermes Agent

Official links:

- <https://hermes-agent.nousresearch.com/docs/user-guide/messaging/telegram>
- <https://hermes-agent.nousresearch.com/docs/user-guide/features/cron>
- <https://hermes-agent.nousresearch.com/docs/user-guide/security>

Hermes Agent is relevant because it already documents Telegram messaging, cron
features, and security concepts for an assistant-style product.

It is not the right core runtime for this MVP because the project needs its own
workflow contract around approvals, idempotency keys, tenant-scoped memory,
Postgres-backed runtime state, and local admin inspection. Telegram and cron are
important surfaces, but they are not the whole harness.

### OpenHands

Official links:

- <https://github.com/All-Hands-AI/OpenHands>
- <https://docs.openhands.dev/openhands/usage/use-cases/overview>

OpenHands is relevant as a mature coding-agent product with a strong autonomous
software-engineering focus. It is valuable as a reference for harness thinking,
workspace execution, and developer workflows.

It is not the right core runtime for this MVP because this assistant is not a
coding agent. Its primary workflows are personal reminders, Telegram
conversation, local calendar state, memory, approvals, audio, outbox dispatch,
and tenant-safe persistence. Reusing a coding-agent runtime would pull the
architecture toward a broader autonomous loop than the MVP needs.

## Accepted Trade-Offs

- We write more local harness code now.
- We keep framework lock-in low while the product contract changes quickly.
- We can test security properties with direct code assertions instead of
  framework-level behavior assumptions.
- We can later adopt a framework only where it earns its place, for example for
  durable long-running workflows, richer HITL, sandboxed tool execution, or
  developer-agent use cases.

## Revisit Triggers

Reconsider a framework when one of these becomes the dominant constraint:

- The workflow grows beyond a small deterministic L2 route catalog.
- The assistant needs sandboxed computer-use or code execution.
- Human-in-the-loop flows outgrow the current approval model.
- Durable multi-step workflows need stronger pause/resume/retry semantics than
  the current state store provides.
- Multi-agent or orchestrator-worker behavior becomes necessary and passes a
  curated eval gate.

Until then, the correct abstraction is the local contract: trusted principal in,
tenant-scoped state changes out, and every side effect guarded by code.
