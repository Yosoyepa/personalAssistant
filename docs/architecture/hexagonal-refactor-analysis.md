# Hexagonal Refactor Analysis

## Scope

This review was run after the initial hexagonal refactor to verify that source
files, business rules, DTOs, ports, adapters, tests, and Git history align with
the intended five-layer package boundary:

- `domain`
- `application`
- `adapters`
- `contracts`
- `infrastructure`

Five subagents reviewed package placement, domain/application design,
adapter/infrastructure coupling, tests/docs coverage, and Git author metadata.

## Findings And Disposition

| Area | Finding | Disposition |
|---|---|---|
| Package placement | Tracked source already lived under the five layer roots, except the package root `__init__.py`. Old top-level folders were generated `__pycache__` residue. | Cleaned generated residue locally and added a top-level package boundary test. |
| Domain purity | `TraceRecorder` was an in-memory adapter living in domain. | Moved recorder to `adapters/observability/local.py`; trace DTOs live in `application/dto/tracing.py`. |
| Domain/application direction | `domain/reminders/workflow_state.py` briefly imported application workflow DTOs. | Replaced with primitive mapping helpers so domain has no application imports. |
| Auth mapping | Domain identity parsed provider-specific claims and carried raw claim payloads. | Moved `AuthClaims` and `principal_from_auth_claims` to `adapters/inbound/auth.py`; domain keeps canonical `Principal`. |
| Channel DTO authority | `NormalizedMessage` carried `tenant_id` and raw webhook payloads. | Removed `tenant_id` and `raw` from the application DTO; tenant authority must come from `Principal`. |
| DTO/model separation | Application DTOs reused a model base from identity, and memory had duplicate record models. | Added `ApplicationDTO` and `DomainModel`; memory ports now use `domain.memory.models.MemoryRecord`. |
| Durable/event placement | CloudEvents, outbox messages, workflow state, and trace DTOs were in domain. | Moved execution/event envelopes to `application/dto`. Business domain remains adapter-free. |
| Scheduler coupling | Scheduler adapter sent notifications directly through another outbound adapter. | Moved due-reminder dispatch to `application/use_cases/reminder_notifications.py`; scheduler adapter now stores and marks jobs only. |
| P3/P5 replay bypass | Calendar and notification adapters returned idempotent results before approval/trusted-principal checks. | Approval/trust checks now run before replay; same key with different payload raises conflict. |
| Boundary tests | Import tests were string-based and narrow. | Replaced with AST import graph checks covering domain, application, contracts, adapters, top-level roots, and reminder use-case dependencies. |
| Git history | Five local commits used the wrong author/committer. | To be rewritten locally before push as `yosoyepa <jandradeu@unal.edu.co>`. |

## Remaining Non-Blocking Follow-Ups

- Convert the broad personal assistant contract acceptance criteria into a
  machine-checked mapping from `AC-*` to test/eval IDs.
- Add a broader cross-tenant canary test covering traces, scheduler jobs, and
  future retrieval/cache paths.
- Decide whether `contracts/tools.py` should be generated from executable tool
  definitions or remain an external protocol catalog.
- Tighten trace completeness assertions for declined/escalated/failed paths.

## Current Architecture Rule

`domain` must not import `application`, `adapters`, `contracts`, or
`infrastructure`. `application` must not import `adapters`, `contracts`, or
`infrastructure`. `contracts` must not import runtime adapters or infrastructure.
`adapters` must not import infrastructure. The composition root under
`infrastructure` is the only layer that wires concrete adapters together.
