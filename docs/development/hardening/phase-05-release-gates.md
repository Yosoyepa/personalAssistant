# Phase 05 - release gates and operational readiness

## Identity

| Field | Value |
|---|---|
| Status | `MERGED` |
| Maintainer | `Yosoyepa <jandradeu@unal.edu.co>` |
| Phase branch | `codex/phase-5-release-gates` |
| Remote base commit | `23fc2b4` |
| Local starting commit | `58a5d22` |
| Accepted implementation head | `bb503c9` |
| Local acceptance records | `40f82c0`, `c1c00ec` |
| Pull request | `#12` merged |
| Merge commit | `ac1278dcc91ab721a0aeb00d9494ca0ac3bb2db2` (2026-07-27) |
| Release | `v0.2.0-alpha.1`, tagged at `ac1278d` |
| Date | `2026-07-27` |

## Objective and current decision

Turn static eval fixtures into executable release gates, add real PostgreSQL
atomicity and delivery corpora, expose truthful liveness/readiness and
metadata-only delivery metrics, and prepare `v0.2.0-alpha.1` without claiming
general availability.

All deterministic, local infrastructure, and controlled Telegram delivery
gates have passed. The phase was accepted locally and proceeded to its
protected pull request. Hosted CI, the merge commit `ac1278d`, and the
annotated `v0.2.0-alpha.1` tag (pointing at `ac1278d`) are now on record. The
GitHub prerelease was not verified from this checkout and is not claimed here.

The launch classification remains **hardened alpha, not GA**. The accompanying
12-point production-readiness audit records unresolved context, compaction,
sandboxing, killed-process, guardrail, per-failure-mode depth, judge
calibration, and production-trace gaps instead of treating missing evidence as
a pass.

## Agent ledger

| Role | Goal | Commit(s) | Decision |
|---|---|---|---|
| P5-A1 | Execute versioned eval cases with human and JSON output, strict selection, and non-zero failures | `5f7fe93`, `2ef8452` | accepted after exact manifest accounting, safe path confinement, sanitized failures, and extensible legacy inventory |
| P5-A2 | Add at least 50 executable idempotency cases | `bd31f05` | accepted with 57 production-backed replay, isolation, fingerprint, HTTP, and Telegram cases |
| P5-A3 | Add at least 50 executable security/privacy cases | `deef8ba`, `a6858cd` | accepted with 50 closed-output auth, webhook, allowlist, authority, and redaction cases after a numeric-sentinel flake was removed |
| P5-A4 | Add at least 50 atomicity and 50 delivery/concurrency cases against real PostgreSQL | `adfabf2` | accepted after 100/100 real-PostgreSQL cases, repeated contention stress, fail-closed missing-DSN behavior, and zero residual schemas |
| P5-A5 | Add liveness, readiness, heartbeat, PII-free metrics, CI eval gates, and release preparation | `a7eead8`, `cb6c026` | accepted after PostgreSQL migration/readiness tests, timing validation, adversarial metrics tests, version consistency, and explicit alpha risk audit |
| P5-R1 | Normalize release fixtures rejected by the complete diff gate | `e0d15af` | accepted after both JSON fixtures retained valid content and exactly one final newline |
| P5-R2 | Prevent runtime settings from exposing credentials or external identities through `repr` | `2878f4c` | accepted with field-level redaction and sentinel regressions |
| P5-R3 | Pseudonymize external trace identifiers while retaining correlation | `e873bcf` | accepted after adversarial in-memory/PostgreSQL/admin tests and unchanged functional idempotency contracts |
| P5-R4 | Prevent `/admin/errors` from reflecting a raw Telegram run identifier | `99eee3d` | accepted with exact raw/digest lookup and HTTP response privacy regression |
| P5-R5 | Preserve lookup compatibility for legacy PostgreSQL trace rows without returning raw identifiers | `b86084c` | accepted with dual lookup limited to validated Telegram run IDs and sanitized reconstruction |
| P5-R6 | Provide an explicit operator-controlled sanitizer for historical trace rows at rest | `37e62e3` | accepted with dry-run default, literal confirmation, transactional rewrite, zero deletes, and real PostgreSQL verification |
| P5-R7 | Make the legacy eval inventory hash portable across Git line endings without weakening content integrity | `bb503c9` | accepted after LF/CRLF/CR equivalence, non-line-ending mutation rejection, 293/293 evals, PostgreSQL corpus, and complete coverage gates |

Every implementation diff was reviewed before explicit-path staging. Accepted
branches were merged with merge commits. No implementation branch was rebased,
squashed, force-pushed, or pushed independently.

## Eval runner and corpus decisions

- `uv run python -m personal_assistant.evals --suite eval/cases` is the
  authoritative deterministic release command.
- The runner validates a strict versioned manifest and strict case schemas,
  confines case and executor paths to their approved roots, rejects an empty
  selection, sanitizes executor failures, and exits non-zero on load or case
  failure.
- Human output names each case. JSON output is stable and machine-readable.
  Category, permission tier, and failure-mode filters are supported.
- Legacy Python tests are an exact allowlisted inventory with source hash and
  node accounting. New product failure cases use dedicated executors rather
  than expanding the legacy adapter.
- The final suite contains 293 cases: 26 legacy/golden cases, 60 temporal
  cases, 57 idempotency cases, 50 security/privacy cases, 50
  atomicity/recovery cases, and 50 delivery/concurrency cases.
- Telegram, LLM, transcription, and TTS providers are disabled or replaced by
  deterministic finite doubles for automated gates.
- Missing `TEST_POSTGRES_DSN` is a sanitized blocking failure for the
  reliability corpus, never a skip or an empty pass.

## PostgreSQL reliability evidence

- Every reliability case creates a cryptographically unique
  `eval_reliability_*` schema, applies the repository migrations, uses
  production PostgreSQL repositories/UoWs, and removes only its validated
  schema.
- Atomicity cases inject faults before and after each reminder write, during
  approval resume, and after an externally committed but locally unknown
  `COMMIT` outcome. They also exercise same-identity and distinct-identity
  contention, cross-tenant identity, and sequential replay.
- Delivery cases exercise `SKIP LOCKED` claim partitioning, lease boundaries,
  stale-token fencing, explicit state transitions, expired `sending` sweeps,
  provider outcome classification, process crashes, P5 manual resolution,
  event filtering, and tenant fencing.
- Fault cases accept only the exact injected PostgreSQL exception. Unexpected
  exceptions fail the case instead of being relabeled as expected faults.
- Same-identity contention requires every contender to reach a successful or
  reused terminal result while all returned effect identities remain equal.
- Five repeated stress cycles covered six same-identity contenders, four
  distinct identities, and five workers claiming three messages each. All 15
  stress scenarios passed.
- Independent root execution passed all 293 cases and left zero
  `eval_reliability_*` schemas.

## Operational readiness decisions

- `GET /livez` proves only that the process can serve a request and has no
  dependency checks.
- `GET /healthz` is a one-version deprecated alias carrying a successor link
  to `/livez`.
- `GET /readyz` checks PostgreSQL access and migration status. When the worker
  is enabled it additionally requires a fresh cross-process heartbeat.
- The heartbeat is recorded only after a successful worker tick. Failed ticks
  and trace-store failures never refresh it.
- Migration `0005_worker_heartbeat.sql` stores one fixed component name and a
  timestamp. It stores no host, PID, tenant, principal, recipient, body,
  message ID, or provider metadata.
- Worker interval and heartbeat timeout reject `NaN`, positive/negative
  infinity, zero, and negative values. An enabled worker also requires timeout
  to exceed its interval.
- Authenticated loopback-only `GET /admin/metrics` returns only the six closed
  delivery-state counts and closed health states. Store errors return zero
  counts plus `metrics=error`; exception text and private data are not emitted.
- The package, build metadata, and FastAPI runtime share version
  `0.2.0-alpha.1` (normalized to `0.2.0a1` in Python distribution metadata).

## Migration evidence

PostgreSQL checks used `postgres:16-alpine` reporting server version `16.14`.
A fresh validated `release_gate_*` schema passed:

1. status with migrations 0001-0005 pending;
2. apply of all five checksummed migrations;
3. repeated apply with `applied_now=[]`;
4. final ready status with no pending migration;
5. verification of five history rows and the two heartbeat columns;
6. safe removal of the validated schema.

The final database audit found zero `release_gate_*` and zero
`eval_reliability_*` schemas. Runtime startup continues to perform no DDL.
Migration `0005` is additive and may remain during binary rollback.

## Verification evidence

| Gate | Result |
|---|---|
| `uv lock --check` | pass; 76 packages resolved |
| `uv sync --frozen --all-extras --group dev` | pass; local package installed as `0.2.0a1` |
| `uv run ruff check .` | pass |
| `uv run mypy src` | pass; 113 source files and zero diagnostics |
| eval runner | 293/293 passed, 0 failed |
| `uv run pytest -q` with PostgreSQL 16 | 648 passed, 3 allowlisted skips, 36 subtests passed |
| coverage execution | 648 passed, 3 allowlisted skips |
| coverage | 91% total line coverage, threshold 85% |
| diff-cover against `origin/main` | 92% over 1,658 changed lines, threshold 90% |
| `uv run python -m compileall -q src` | pass |
| `uv build` | sdist and wheel built for `0.2.0a1`; wheel contains migration 0005, operational and trace-sanitizer modules, and matching metadata |
| `uv run pip-audit` | no known dependency vulnerabilities; unpublished local package not present on PyPI |
| `uv run pre-commit run --all-files` | all hooks passed |
| Gitleaks `v8.24.2` read-only container scan | no leaks in 110 commits / approximately 2.78 MB |
| `git diff --check origin/main...HEAD` | pass after release fixture and cross-platform eval-hash remediation |
| fresh migration smoke | `status -> apply 0001..0005 -> apply no-op -> status ready`; schema removed |
| historical trace sanitizer smoke | dry-run left the legacy row unchanged; confirmed apply rewrote one row; raw/digest lookup and idempotent rerun passed; schema removed |

The three skips are the previously allowlisted compatibility probes: the
PostgreSQL adapter intentionally does not expose module-level SQL constants or
record-serializer helpers, and the import-order isolation probe is inapplicable
after psycopg has already been loaded by the real PostgreSQL corpus. No critical
security, atomicity, delivery, or eval case is skipped or expected to fail.

## GitHub governance evidence

- GitHub CLI is authenticated as `Yosoyepa`; Git operations use HTTPS.
- `main` requires a pull request and resolved conversations.
- Required checks are `quality`, `tests (3.11)`, `tests (3.12)`, `security`,
  and `postgres-integration`.
- Administrator enforcement is enabled. Force-push and branch deletion are
  disabled.
- Merge commits are enabled; squash and rebase merges are disabled.
- The full 293-case eval command is part of the already-required
  `postgres-integration` job, so a regression blocks the phase without adding
  an unprotected check name.
- Both Python matrix jobs and the integration job use PostgreSQL 16. External
  provider variables are disabled in CI.

The first hosted CI run on pull request `#12` passed `quality` and `security`
but exposed one cross-platform defect: the immutable legacy inventory hash was
calculated from CRLF bytes on Windows while Git's Linux checkout contained the
same JSON with LF bytes. Commit `bb503c9` now hashes repository-canonical LF
line endings, retains rejection of every tested non-line-ending mutation, and
passes the complete local gate. Pull request `#12` subsequently merged at
`ac1278d`; because `main` requires those five checks with administrator
enforcement enabled (see the governance evidence above), the merge itself is
the record that they reran and passed.

## Data and privacy review

- No production token, webhook secret, database password, recipient, message
  body, transcript, or external provider diagnostic was added to Git.
- The local test-role password was synchronized with the restarted disposable
  PostgreSQL container without printing it. Test DSNs existed only in
  process-local environment variables and were removed after each gate.
- Trace, webhook, auth, readiness, migration, metrics, and executor failure
  outputs were reviewed for closed metadata and sanitized errors.
- Runtime settings hide credentials, provider URLs, tenant/principal identity,
  and Telegram allowlist data from `repr`.
- External trace identifiers are pseudonymized before persistence and admin
  presentation. Legacy PostgreSQL rows remain queryable without returning raw
  identifiers, and an explicit dry-run/confirmed transactional sanitizer can
  rewrite historical rows without deleting them.
- `.env.release-smoke` remained ignored and untracked. Its inherited ACL was
  replaced with rules limited to the current owner, local administrators, and
  `SYSTEM` before provider access.
- Gitleaks scanned complete local history, and the protected GitHub security
  job will repeat repository secret scanning on the pull request.

## Controlled bot gate

The controlled smoke passed on `2026-07-27` with 257 assertions. It used a
cryptographically unique `smoke_release_*` PostgreSQL schema, migrations
0001-0005, an exact replay of one synthetic Telegram update posted directly to
the loopback webhook, and real outbound delivery through the dedicated
Telegram test bot.

Verified invariants:

1. five migrations applied and readiness was current;
2. exact replay reused one logical workflow and one pending approval;
3. approval created exactly one calendar event, scheduler row, domain event,
   and outbox message;
4. the first runtime stopped before the reminder was due;
5. the restarted runtime produced a strictly fresh worker heartbeat and
   `/readyz` returned HTTP 200;
6. Telegram confirmed one reminder delivery in one provider attempt;
7. outbox and scheduler ended `published`, with zero `pending`, `claimed`,
   `sending`, `failed`, or `uncertain` rows;
8. authenticated `/admin/metrics` exposed only closed counts and health states
   and contained none of the private markers used by the test;
9. the configured webhook URL/certificate state was unchanged; neither
   `setWebhook` nor `deleteWebhook` was called;
10. the validated temporary schema was removed and its absence verified.

This smoke intentionally did not traverse the public reverse proxy or Telegram
webhook ingress. Those boundaries remain covered by deterministic HTTP,
authentication, secret-header, allowlist, and remote-peer tests. The outbound
Telegram provider call and PostgreSQL restart/delivery path were real.

## Residual alpha risks and rollback

- Telegram cannot share a transaction with PostgreSQL. A provider result that
  becomes uncertain still requires manual reconciliation and must never be
  retried blindly.
- The production-readiness audit intentionally records gaps in context
  telemetry, long-session compaction, tool/network sandboxing, killed-process
  end-to-end exercises, broad content/citation guardrails, per-failure-mode
  statistical depth, calibrated LLM judges, and production trace retention.
- PostgreSQL tenant filters and adversarial tests exist, but database RLS is not
  implemented. This release remains single-operator and is not a SaaS
  multi-tenant claim.
- Starlette emits one upstream `TestClient` deprecation warning that does not
  affect runtime behavior.

Rollback requires stopping ingress and the worker, reconciling all `sending`
and `uncertain` deliveries, and deploying the previously verified binary with
delivery disabled until the queue is reviewed. Additive migrations 0001-0005
remain in place; no down migration or data deletion is authorized.
