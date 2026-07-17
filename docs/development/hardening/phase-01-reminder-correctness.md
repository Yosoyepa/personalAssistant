# Phase 01 — timezone-safe and collision-resistant reminders

## Identity

| Field | Value |
|---|---|
| Status | `LOCAL_ACCEPTED` |
| Maintainer | `Yosoyepa <jandradeu@unal.edu.co>` |
| Phase branch | `codex/phase-1-reminder-correctness` |
| Base commit | `ba82d29` |
| Local acceptance head | `45e5a5e` |
| Pull request | pending |
| Merge commit | pending |
| Date | `2026-07-17` |

## Objective and acceptance

Make reminder interpretation honor the user's IANA timezone, persist canonical
UTC instants, and scope idempotency to the trusted source identity. A repeated
source event with the same payload reuses its result; the same identity with a
different payload is a conflict. The local API reports that conflict as HTTP
409, while Telegram acknowledges it with HTTP 200 and performs no reply or
side effect so provider retries stop safely.

The phase was accepted only after time, identity, payload fingerprint, and
timezone propagated through commands, approvals, workflow state, calendar,
scheduler, events, outbox, PostgreSQL compatibility reads, and HTTP results.

## Agent ledger

| Role | Goal | Commit | Decision |
|---|---|---|---|
| P1-A1 | Parse local time with typed outcomes, IANA zones, UTC, DST and ambiguity handling | `80fff94` | accepted after adversarial parser review |
| P1-A2 | Scope v2 idempotency to tenant, channel, principal, conversation and source event | `e894153` | accepted |
| P1-A3 | Add versioned Spanish temporal clarification replies | `4a979e7` | accepted |
| P1-A4 | Propagate timezone, source identity and fingerprint through every reminder boundary | `60a5101` | accepted after formatter and invalid-timezone rework |
| P1-A5 | Cover timezone, DST, replay, collision and adapter boundaries | `c2326e2` | accepted |
| P1-A5 rework | Raise changed-line coverage with contract, HTTP and PostgreSQL regressions | `29b2ed9` | accepted after the first integrated gate reported 85% |

The orchestrator integrated accepted branches with merge commits. A mechanical
format correction is isolated in `bb5bc25`; it changes no behavior.

## Functional decisions

- `extract_reminder` returns `parsed`, `needs_clarification`, or `unsupported`.
- Hours from 1 through 12 without AM/PM are ambiguous; 24-hour values and
  explicit AM/PM values are accepted.
- Nonexistent and repeated DST wall times require clarification rather than a
  guessed instant.
- Relative expressions advance from the supplied UTC instant, including across
  DST transitions.
- Runtime configuration rejects an invalid IANA timezone at startup. Invalid
  request timezone input returns a versioned clarification without effects.
- `message_id` remains a provider message reference. `source_event_id` is the
  stable delivery/event identity; Telegram maps `update_id` to it.
- New keys use `reminder:v2:<full SHA-256>` over the normalized trusted identity.
  The payload fingerprint is stored and compared independently.
- Legacy PostgreSQL rows remain readable through deterministic read-time
  upgrades. New writes contain the strict timezone, source, and fingerprint
  fields.

## Verification evidence

Executed from the integrated phase branch with external providers disabled:

| Gate | Result |
|---|---|
| `uv lock --check` | pass |
| `uv sync --frozen --all-extras --group dev` | pass |
| Ruff format on the 41 changed Python files | pass |
| `uv run ruff check .` | pass |
| `uv run mypy src` | pass, 86 source files and zero diagnostics |
| `uv run pytest -q` | 304 passed, 2 skipped, 4 strict xfailed, 2 subtests passed |
| coverage | 89% total line coverage, threshold 85% |
| diff-cover | 93%, threshold 90% |
| `uv run python -m compileall -q src` | pass |
| `uv build` | sdist and wheel built |
| `uv run pip-audit` | no known dependency vulnerabilities |
| `uv run pre-commit run --all-files` | all hooks passed |
| `git diff --check origin/main...HEAD` | pass |

The four remaining strict expected failures are owned by later phases:

1. atomic recovery after a crash between effects and terminal workflow state
   (phase 3);
2. forged HTTP identity headers (phase 2);
3. concurrent worker double delivery (phase 4);
4. worker restart after provider acceptance (phase 4).

The former temporal characterization is now a normal passing test.

## Security, data and migration review

- No production secret, token, message body, or real personal data was added.
- Telegram conflict responses expose neither the idempotency key nor payload
  fingerprint and create no approval, calendar, scheduler, event, or outbox
  effect.
- External Telegram, LLM, transcription, and TTS providers remain fake or
  disabled in tests.
- This phase has no schema migration and introduces no destructive data change.
  Compatibility changes are read-time upgrades for existing JSONB payloads.

## Risks and rollback

- Pre-P1-A4 approvals use `message_id` as their deterministic legacy source
  identity. They remain readable, but old P3 resources may require recreation
  rather than unsafe impersonation of the new identity.
- Scheduled legacy rows did not preserve an IANA zone; their compatibility
  projection is explicitly UTC. All new rows preserve the zone.
- Header-based HTTP authority intentionally remains characterized as unsafe
  until phase 2 closes the trust boundary.
- Reminder creation and delivery are still non-atomic/non-durable until phases
  3 and 4.

Rollback is a single revert of the eventual phase merge commit. There is no
database downgrade or data deletion step for this phase.
