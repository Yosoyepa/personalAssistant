# Phase 03 — atomic and recoverable reminder persistence

## Identity

| Field | Value |
|---|---|
| Status | `MERGED` |
| Maintainer | `Yosoyepa <jandradeu@unal.edu.co>` |
| Phase branch | `codex/phase-3-atomic-persistence` |
| Base commit | `0925425` |
| Accepted implementation head | `fe2c27e` |
| Local acceptance commit | `905d04e` |
| Pull request | [#10](https://github.com/Yosoyepa/personalAssistant/pull/10) |
| Merge commit | `a0117ec` |
| Date | `2026-07-17` |

## Objective and acceptance

Make reminder creation one atomic operation across workflow state, calendar,
scheduler, event store, and outbox; replace implicit PostgreSQL DDL with
versioned migrations; and make a committed result safely recoverable when the
client loses the outcome of `COMMIT`.

The phase was accepted locally only after failures injected after every write
left the exact prior database snapshot intact, a crash after a durable commit
replayed without duplicate effects, and 24 competing processes produced one
logical reminder. The same invariants are implemented by the in-memory
development adapter and by PostgreSQL 16.

## Agent ledger

| Role | Goal | Commit(s) | Decision |
|---|---|---|---|
| P3-A1 | Add a reminder creation unit of work and atomic in-memory implementation | `5ed85f6` | accepted after rollback covered approval resume and all deterministic-ID indexes |
| P3-A2 | Add explicit, checksummed, locked PostgreSQL migrations | `85c092e` | accepted after real `status/apply/no-op/status` validation |
| P3-A3 | Derive stable effect IDs and enforce tenant-scoped uniqueness | `b81c122` | accepted after PostgreSQL preflight and uniqueness tests |
| P3-A4 | Implement one-connection PostgreSQL UoW, conflict classification, and unknown-commit recovery | `92d242d`, `7ccce45` | accepted after both transaction exceptions became process-safe |
| P3-A5 | Prove rollback, replay, and multiprocess uniqueness with fault injection | `771ea05` | accepted |
| P3-A5 gate rework | Restore differential coverage without production exclusions | `5b89e3a` | accepted after diff coverage rose from 83% to 91% |
| P3-A5 CI rework | Run the coverage matrix against the same PostgreSQL corpus used by local acceptance | `7225e04` | accepted without moving or lowering the gate |

Every implementation was reviewed as an unstaged diff before explicit-path
staging. Accepted branches were integrated with merge commits; no branch was
rebased, squashed, force-pushed, or pushed independently.

## Persistence decisions

- `ReminderUnitOfWork` owns the workflow-state, calendar, scheduler, event
  store, and outbox ports used by one reminder transaction.
- The in-memory adapter acquires a stable lock order and restores deep
  snapshots, including secondary indexes, on any exception.
- The PostgreSQL adapter uses one connection and one `SERIALIZABLE`
  transaction. Its repositories share that connection and cannot commit
  independently.
- The atomic transaction creates or reuses the calendar event, creates the
  scheduler row, appends `reminder.created`, appends the delayed
  `notification.requested` outbox message, and marks the workflow completed.
- Terminal `tool_called` and `agent_completed` traces are emitted only after
  the database commit is visible.
- Known PostgreSQL serialization, deadlock, and unique violations are exposed
  as sanitized typed conflicts. Loss of the connection while committing is a
  distinct `ReminderCommitOutcomeUnknown` and is never retried blindly.
- Both transaction exceptions have stable pickle reconstruction, so process
  pools preserve their type and conflict kind instead of failing as a broken
  worker pool.
- Calendar, reminder, domain-event, notification-event, and outbox-message IDs
  are full SHA-256, domain-separated derivations of idempotency v2. Existing v1
  identifiers remain readable; new writes use v2 identities.
- Memory remains a development/test backend. PostgreSQL is required for the
  durable runtime path.

## Migration decisions

- `0001_initial.sql` defines the persisted alpha schema and can adopt the
  compatible pre-migration alpha layout.
- `0002_reminder_identity_constraints.sql` adds tenant-scoped unique indexes
  after failing explicitly if duplicate legacy identities already exist.
- `assistant_schema_migrations` records version, name, checksum, and applied
  time. A changed applied migration, unknown version, renamed migration, or
  history gap is rejected.
- Each migration runs in its own explicit transaction under a schema-scoped
  PostgreSQL advisory lock. A second migrator waits rather than racing.
- Runtime startup performs no DDL. Operators use:

  ```powershell
  uv run python -m personal_assistant.infrastructure.migrations status
  uv run python -m personal_assistant.infrastructure.migrations apply
  ```

- `/readyz` reports not ready when migrations are pending, corrupt,
  misconfigured, or unreachable, while returning only sanitized details.
- Reapplying a current schema is a no-op. Migrations are additive; there is no
  destructive down migration in this release.

## Verification evidence

All external Telegram, LLM, transcription, and TTS calls remained disabled or
fake. PostgreSQL checks used an ephemeral PostgreSQL `16.14` container.

| Gate | Result |
|---|---|
| `uv lock --check` | pass |
| `uv sync --frozen --all-extras --group dev` | pass |
| Ruff format over changed Python files | pass, 31 files already formatted |
| `uv run ruff check .` | pass |
| `uv run mypy src` | pass, 94 source files and zero diagnostics |
| `uv run pytest -q` | 480 passed, 2 allowlisted skips, 2 strict xfailed, 2 subtests passed |
| PostgreSQL fault-injection corpus | 15 passed; every test used a fresh schema and cleanup left zero residual schemas |
| repeated multiprocess contention | 5 independent repetitions passed after the accepted corpus run |
| coverage | 90% total line coverage, threshold 85% |
| diff-cover | 91% over 1,128 changed lines with 94 missing, threshold 90% |
| `uv run python -m compileall -q src` | pass |
| `uv build --quiet` | sdist and wheel built |
| `uv run pip-audit` | no known dependency vulnerabilities; the unpublished local package is not auditable through PyPI |
| `uv run pre-commit run --all-files` | all hooks passed |
| Gitleaks `v8.28.0` read-only container scan | no leaks in 80 commits / approximately 1.96 MB |
| `git diff --check` | pass |
| migration smoke | `status -> apply 0001+0002 -> apply no-op -> status ready`; acceptance schema removed |

The first PR run exposed a CI-environment mismatch: four required checks,
including `postgres-integration`, passed, but `tests (3.12)` omitted every real
PostgreSQL test because its matrix job had no database service. That reduced
remote differential coverage to 77% even though the same accepted head reached
91% locally with `TEST_POSTGRES_DSN`. Commit `7225e04` adds PostgreSQL 16 and
only that test DSN to the Python test matrix. It does not move, exclude, or
lower the changed-line gate, and the dedicated integration job remains intact.

The two allowlisted skips are compatibility probes for optional module-level
SQL constants and record-serializer helpers that this adapter intentionally
does not expose. All behavior-specific PostgreSQL tests execute. The two
remaining strict expected failures are Phase 4 ownership:

1. two workers can still deliver one due notification concurrently;
2. restarting after provider acceptance but before `mark_sent` can redeliver.

The Phase 3 atomic-recovery characterization is now a normal passing test.

## Remote evidence

GitHub accepted head `905d04e` after every protected check completed:

- `quality`: pass;
- `tests (3.11)`: pass with PostgreSQL 16;
- `tests (3.12)`: pass with PostgreSQL 16 and the 90% changed-line gate;
- `security`: pass;
- `postgres-integration`: pass.

The final PR was mergeable and clean under branch protection. GitHub created
merge commit `a0117ec` with parents `0925425` and `905d04e`, then deleted the
remote phase branch. Required checks and resolved conversations remain
enforced; force-push and deletion remain prohibited for `main`. The repository
continues to allow merge commits while squash and rebase are disabled.

## Recovery evidence

- `AFTER` triggers fail registration, approval resume, calendar, scheduler,
  event-store, outbox, and terminal-state writes. Each failure restores the
  byte-equivalent prior business snapshot and permits a clean replay.
- A trace sink crash after commit proves terminal traces cannot precede the
  durable state and that replay reuses the one committed reminder.
- A connection wrapper that commits and then raises an operational disconnect
  proves the unknown-outcome contract. Explicit replay resolves to the existing
  deterministic result without creating duplicates.
- Process-pool tests round-trip typed transaction errors and run 24 contenders
  through four workers. Typed conflicts may reach callers, but the database
  contains exactly one state, calendar item, scheduled reminder, event, and
  outbox message for the source identity.
- Payload changes under the same source identity remain conflicts, while
  distinct tenant and source identities remain isolated.

## Data and migration review

- No production credential, provider secret, real message, transcript, user
  identifier, or external recipient was added. Test database credentials were
  ephemeral and are absent from the repository.
- The phase adds two checksummed migration files and a migration-history table.
  The changes are additive and preserve existing v1 records.
- Applying `0002` is intentionally blocked when legacy duplicate identities
  exist. Operators must investigate and resolve those records; the migrator
  does not delete or merge user data.
- New outbox rows are durable but are not yet claimed or dispatched by the
  worker. Phase 4 owns delivery-state evolution and leases.

## Risks and rollback

- PostgreSQL transaction conflicts are typed but not automatically retried by
  the reminder workflow. A caller may replay the same idempotency identity;
  deterministic IDs make that replay safe.
- Unknown non-PostgreSQL exceptions without a recognized SQLSTATE preserve
  their original exception type. Public boundaries must continue applying the
  centralized sanitized error contract.
- A caller-owned persistent PostgreSQL connection can remain aborted after a
  transaction failure. The runtime uses managed DSN connections; custom
  integrations must discard or roll back their connection.
- The unique outbox event constraint currently assumes one message for each
  `notification.requested` event. Phase 4 must preserve or explicitly migrate
  that invariant when adding delivery attempts.
- A database commit followed by a lost connection cannot prove whether an
  external caller observed success. The supported response is explicit replay,
  not automatic retry.
- Starlette emits one upstream `TestClient` deprecation warning that does not
  affect runtime behavior.
- Notification delivery is still non-durable until Phase 4 closes the two
  remaining strict expected failures.

Rollback is a revert of the eventual Phase 3 merge commit. The binary may be
rolled back while the additive tables, migration history, columns, and indexes
remain in place; no data deletion or down migration is required. If `0002`
has not yet been applied, operators may stop after `status` and roll back the
binary. If it has been applied, the previous binary must tolerate the added
indexes and migration metadata before rollback is approved.
