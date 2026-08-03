# ADR-005: Single-Shot LLM Context — No Multi-Turn Conversation History

## Status

Accepted

Delivered in phase 09
(`docs/development/hardening/phase-09-single-shot-context.md`, 2026-08-03).

This ADR closes GAP #3 of the production-readiness audit
(`docs/development/production-readiness-v0.2.0-alpha.1.md`) by design
decision: there is no conversation transcript to compact, so no compaction
pipeline, metric-driven trigger, or long-session loss eval is required.
Any future move to multi-turn context must reopen this decision (see
Re-entry triggers).

## Date

2026-08-03

## Context

The audit's GAP #1 DoD ("typical context utilization below 40%") and GAP #3
("compaction tested on long sessions") presuppose conversational multi-turn
context. The phase-06 addendum established that this runtime's LLM calls are
single-shot and recorded that GAP #3 "requires a conversation-history design
decision before any compaction pipeline or loss eval can exist." This ADR is
that decision.

The single-shot property is structural, not accidental. There are exactly two
LLM call sites, both behind the `LLMProvider` port
(`application/ports/services.py`):

| Call site | File | Prompt variables | Budget |
|---|---|---|---|
| Reminder extraction fallback (only when deterministic parsing fails) | `application/use_cases/reminders.py` (`_extract_with_llm`) | `now`, `timezone`, `text` | `TokenBudget(1500)`, `max_tokens=384` |
| Conversation intent classification | `application/use_cases/commands.py` (`_infer_intent`) | `allowed_intents`, `now`, `timezone`, `text` | `TokenBudget(1000)`, `max_tokens=256` |

Supporting evidence:

- Prompts are flat, versioned `string.Template` templates loaded from the
  `prompts/` registry. No message-list or chat-shaped abstraction exists.
- The `context.selected` trace event carries a fixed reference set
  (`agent_contract`, `current_message`, `principal`).
- `conversation_id` is an identity/idempotency key (reminder idempotency,
  workflow state); it is never used to load prior turns.
- Memory (`MemoryRecord`, episodic/semantic/procedural) is explicit,
  tenant-scoped, admin-inspectable note-taking — not a transcript — and is
  not read into prompts today.
- Phase 06 records `context_utilization` (`input_tokens` over
  `LLM_CONTEXT_WINDOW_TOKENS`) on every `llm.called` trace event at both call
  sites via `llm_usage_metrics`, with admin-dashboard p50/p95.

## Decision

LLM calls are single-shot by design.

1. Each LLM call assembles one prompt from the current message plus a fixed
   set of context references. No multi-turn conversation history is stored
   for prompt purposes, and none is injected.
2. Cross-turn continuity flows only through explicit, tenant-scoped,
   inspectable artifacts — confirmed memory records and durable workflow
   state — never through an implicit rolling transcript.
3. Compaction is not applicable: no per-turn transcript accumulates, so there
   is nothing to compact and no long-session context-decay mode to test.
   GAP #3 is closed by this decision, not by implementing compaction.
4. Context-budget safety is enforced per call: `max_tokens` plus a
   `TokenBudget` at each call site, `context_utilization` telemetry on every
   `llm.called` event (phase 06), and the deterministic eval gate. The
   audit's "below 40% utilization" concern is monitored per call, which is
   the only granularity at which this runtime assembles context.

## Re-entry Triggers

Any of the following reopens GAP #3 and requires a new ADR superseding this
one, plus a compaction pipeline and long-session loss evals before the
triggering capability ships:

- A new intent route whose correct behavior depends on prior turns
  (conversational follow-ups, multi-turn clarification beyond what workflow
  state already carries).
- `context_refs` growing per-turn data beyond the fixed set above.
- Eval failures or production incidents attributable to missing
  conversational context; each becomes a permanent regression case per
  `eval/README.md`.
- A product decision to support free-form multi-turn dialogue.

## Consequences

Positive consequences:

- GAP #3 closes with zero new runtime surface: no transcript store (which
  would be the largest PII-bearing artifact in the system), no compaction
  pipeline, no new privacy exposure, no new eval infrastructure.
- Prompts stay small, bounded, and verifiable with deterministic code
  assertions, per `eval/README.md` ("no LLM judges for behavior that code
  can verify").
- The LLM-visible surface remains exactly: current message + fixed context
  references. Tenant isolation and privacy redaction scopes stay unchanged.

Negative consequences:

- Users must restate context across messages; continuity is limited to what
  explicit memory records and workflow state carry.
- If the product later moves to dialogue, the deferred work (history store,
  compaction, loss evals, privacy review) returns, and the per-call
  `context_utilization` telemetry will need re-aggregation across turns.

## Acceptance Criteria

| Criterion | Probe |
|---|---|
| Exactly two LLM call sites exist, both in the two named use-case modules | `rg -n "\.complete\(" src/personal_assistant/application/use_cases` → only `reminders.py` and `commands.py` |
| No conversation-history concept in code or prompts | `rg -ni "conversation_history\|message_history\|chat_history" src/ prompts/` → no matches |
| `conversation_id` is an identity/idempotency input, never a history loader | `rg -n "conversation_id" src/personal_assistant/domain/reminders/idempotency.py` |
| Per-call context telemetry covers both call sites | `rg -n "llm_usage_metrics" src/personal_assistant/application/use_cases` → both modules |
| Re-entry triggers are documented | this section exists and names at least the four triggers above |
