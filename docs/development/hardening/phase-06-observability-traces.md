# Phase 06 - context observability, trace completeness and retention

## Identity

| Field | Value |
|---|---|
| Status | `MERGED` |
| Maintainer | `Yosoyepa <jandradeu@unal.edu.co>` |
| Phase branch | `codex/phase-6-observability-traces` (merged and deleted, local and remote) |
| Base commit | `ac1278d` |
| Pull request | `#13` (https://github.com/Yosoyepa/personalAssistant/pull/13) |
| Merge commit | `6e95c1f` |
| Release | `PENDING` |
| Date | `2026-07-28` |

## Objective and current decision

Close the days 1-7 P0 items of the `v0.2.0-alpha.1` production-readiness
audit roadmap: GAP #1 (context-utilization telemetry, reframed per call as
described below), GAP #12 (trace completeness and retention), and GAP #6
(tool/network sandbox, design only).

**Explicit reframing of GAP #1.** The audit's DoD ("typical context
utilization below 40%") and GAP #3 ("compaction tested on long sessions")
presuppose conversational multi-turn context. This runtime's LLM calls are
single-shot: each call builds one prompt from the current message plus a fixed
set of context references, and no multi-turn conversation history is
assembled. The phase therefore defines utilization **per LLM call** as
`input_tokens / configured context window`, recorded on every `llm.called`
trace event and surfaced to the operator as p50/p95 on the admin dashboard.
The audit addendum records this reframing. GAP #3 (compaction) is untouched
and remains a GAP pending a conversation-history design decision.

All phase changes are additive or fail-closed tightenings on top of the
phase-05 contracts. Deterministic, PostgreSQL-backed, and hosted-CI
verification all pass: ruff, mypy, targeted pytest, the full suite with
PostgreSQL 16, the complete eval gate, coverage, diff-cover, the static
gates, and the required PR checks (quality, tests 3.11/3.12,
postgres-integration, security). The phase merged as `6e95c1f` via PR #13.
The launch
classification remains **hardened alpha, not GA**; the audit scorecard is
unchanged by this phase.

## Waves 3 + 2 plan and execution record

| Wave | Slot | Goal | Mode | Status |
|---|---|---|---|---|
| 1 | A1 | Per-call context-utilization telemetry: `LLM_CONTEXT_WINDOW_TOKENS` setting, plumbing, `llm.called`/`context.selected` payload metrics, privacy whitelist | IMPLEMENTATION | DELIVERED, verified |
| 1 | A2 | Trace completeness: `REQUIRED_TRACE_FIELDS` contract, write-time validation in both recorders, `trace.completeness.v1` eval executor and cases | IMPLEMENTATION | DELIVERED, verified |
| 1 | A3 | ADR-004 tool-execution sandbox design for GAP #6 | REVIEW_ONLY (design document, no runtime mutation) | DELIVERED, accepted as Proposed |
| 2 | A4 | Trace retention: `TRACE_RETENTION_DAYS` setting, operator-invoked pruning CLI, runbook policy | IMPLEMENTATION | DELIVERED, verified |
| 2 | A5 | Dashboard context visibility: p50/p95 component, `high_context_utilization` attention signal | IMPLEMENTATION | DELIVERED, verified |

Executed as two waves: wave 1 delivered A1, A2, and ADR-004 in parallel;
wave 2 delivered A4 and A5 (retention and dashboard) on top of wave 1. The
orchestrator ran integrity verification (ruff, mypy, targeted pytest, and the
non-PostgreSQL eval subset) after each delivery. Work proceeded without
per-slot worktrees: all slots landed on a single uncommitted tree on top of
`ac1278d`, then explicit-path staging integrated them into the phase branch
as four Conventional Commits (`21b6506`, `3d40f13`, `a18b853`, `669fddb`).

## Agent ledger

| Role | Goal | Commit(s) | Decision |
|---|---|---|---|
| P6-A1 | Record `input_tokens`/`output_tokens`/`context_utilization` on both `llm.called` call sites and `text_length`/`estimated_tokens` on `context.selected`; new `llm_context_window_tokens` setting (env `LLM_CONTEXT_WINDOW_TOKENS`, default 200000, positive-int validated) plumbed env -> `AppSettings` -> `build_container()` -> use cases -> `http.py`/`worker.py`, following the `reminder_minutes_before` pattern | `21b6506` | ACCEPTED |
| P6-A2 | Module-level `REQUIRED_TRACE_FIELDS` contract and shared `require_trace_completeness()` validator raising `IncompleteTraceEventError`, enforced at write time by both the local and PostgreSQL trace recorders before `for_persistence()` redaction; new `trace.completeness.v1` eval executor with 2 cases (evals 293 -> 295; legacy corpus and sha256 pin untouched); emitter-side fixture fixes in `security_boundary_v1.py` and `test_trace_privacy.py` | `3d40f13` | ACCEPTED |
| P6-A3 | ADR-004 two-layer GAP #6 design (adapter-level deny-by-default egress allowlist plus single locked-down container), Status: Proposed | `669fddb` | ACCEPTED as design only; implementation deferred to roadmap days 8-14 |
| P6-A4 | `trace_retention_days` setting (env `TRACE_RETENTION_DAYS`, default 30, positive-int validated); operator-invoked CLI `infrastructure/trace_retention.py` mirroring `trace_sanitizer.py` (dry-run default, `--apply --confirm PRUNE_TRACES`, `--days` > env > default, range 1..3650, single transaction, cutoff computed once in-transaction, JSON output with totals only); runbook "Trace retention" section documenting the 30-90 day policy | `a18b853` | ACCEPTED |
| P6-A5 | `context` component in `AdminDashboard.snapshot()` with `samples`, `p50`, `p95` (nearest-rank on observed samples, stdlib only) and `calls_by_model`; `high_context_utilization` health signal when p95 > 0.40 (audit DoD #1 threshold); fail-closed empty state on adapter failure; HTML section; closed `/admin/metrics` contract untouched | `21b6506` | ACCEPTED |

Every diff was produced against `ac1278d` and reviewed as a working tree
before staging. The phase branch was created from `ac1278d`; nothing was
rebased, squashed, force-pushed, or pushed.

## Delivered decisions

- `llm_usage_metrics()` in `application/use_cases/reminders.py` merges
  `input_tokens`, `output_tokens`, and `context_utilization` (rounded to 4
  decimals) into the `llm.called` `output_summary` at both call sites
  (`reminders.py` and `commands.py`), fixing the inconsistency where the
  command path recorded no token data. Keys are omitted — never emitted as
  zeros — when the provider reports no usage.
- `context.selected` gained `text_length` and `estimated_tokens` in its
  `input_summary`.
- `privacy.py` `_METRIC_KEYS` whitelists `contextutilization` so the value
  survives trace redaction as a metric, not a payload.
- Required trace fields per event type: `agent.started` -> `input_summary`,
  `context.selected` -> `context_refs`, `llm.called` -> `model`,
  `tool.called` -> `tool_call`, `guardrail.checked` -> `validation`,
  `approval.requested` -> `tool_call`, `agent.completed` -> `output_summary`,
  `agent.failed` -> `error`. Validation runs as emitted, before redaction may
  legitimately strip non-allowlisted keys; the error message names only the
  event type and field names, never payloads.
- The retention CLI is operator-invoked only: runtime startup, workers, and
  migrations never delete trace rows. Its output contains the schema, cutoff
  timestamp, and row counts — never tenant, trace, or user identifiers.
- The dashboard context component reads `llm.called` events through the same
  public trace port as the other components; a failing or empty adapter
  degrades to the empty component instead of raising out of `snapshot()`.
  Only numeric aggregates and model names leave the method. The p95 attention
  threshold is the audit DoD #1 value (0.40).

## Diff and staging review

- [x] `git status --porcelain` reviewed: 18 modified paths, 8 new paths, all
      inside the authorized scope.
- [x] `git diff --stat HEAD` reviewed: 2683 insertions, 9 deletions across
      the 26 files (final integrated totals, including the documentation
      updates recorded after the initial review).
- [x] `git diff --check HEAD` passed.
- [x] Explicit-path staging executed per commit group (prohibited forms
      `git add .`, `git add -A`, `git commit -am` were not used).
- [x] `git diff --cached --stat` reviewed at each of the four stagings
      (742 + 556 + 834 + 551 insertions across the four commits; totals
      match the reviewed working-tree diff plus the documentation updates).
      `git diff --check HEAD` passed before staging.

**Changed paths (integrated on the phase branch over `ac1278d`):**

```text
.env.example
docs/adr/ADR-004-tool-execution-sandbox.md
docs/runbook/persistence.md
eval/cases/suite.json
eval/cases/trace-completeness.v1.json
src/personal_assistant/adapters/observability/local.py
src/personal_assistant/adapters/persistence/postgres.py
src/personal_assistant/application/dto/tracing.py
src/personal_assistant/application/use_cases/commands.py
src/personal_assistant/application/use_cases/reminders.py
src/personal_assistant/domain/common/privacy.py
src/personal_assistant/evals/executors/security_boundary_v1.py
src/personal_assistant/evals/executors/trace_completeness_v1.py
src/personal_assistant/infrastructure/admin.py
src/personal_assistant/infrastructure/bootstrap.py
src/personal_assistant/infrastructure/config.py
src/personal_assistant/infrastructure/http.py
src/personal_assistant/infrastructure/trace_retention.py
src/personal_assistant/infrastructure/worker.py
tests/test_admin_dashboard.py
tests/test_llm_context_utilization.py
tests/test_trace_completeness.py
tests/test_trace_privacy.py
tests/test_trace_retention.py
```

## Gate evidence

Canonical commands from `maintainer-workflow.md` section 8, run with
`APP_ENV_FILE=disabled` on the uncommitted tree at base `ac1278d`,
`2026-07-28`. An initial deterministic run passed without PostgreSQL; the
PostgreSQL-dependent gates were then executed against PostgreSQL 16 in the
container `personal-assistant-pg16-phase3` (`postgres:16-alpine`, DSN
provided via `TEST_POSTGRES_DSN` on `localhost:55432`). Both runs are
recorded below.

| Gate | Exact command | Result | Note |
|---|---|---|---|
| Lock | `uv lock --check` | `PASS` | |
| Sync | `uv sync --frozen --all-extras --group dev` | `PASS` | satisfied by every `uv run` gate below, which synchronizes the frozen environment before execution |
| Ruff | `uv run ruff check .` | `PASS` | all checks passed |
| Mypy | `uv run mypy src` | `PASS` | 115 source files, zero diagnostics |
| Pytest (no DSN) | `uv run pytest -q` | `648 passed, 70 skipped, 1 failed (environmental), 36 subtests passed` | initial deterministic run; the single failure was the reliability corpus gate failing closed with `MissingTestPostgresDsnError`, superseded by the PostgreSQL run below |
| Pytest (PostgreSQL 16) | `uv run pytest -q` with `TEST_POSTGRES_DSN` | `PASS — 726 passed, 3 skipped, 36 subtests passed, 0 failures` | final consolidated run, including `test_complete_postgres_reliability_corpus_passes`, the 3 retention integration tests, and the 7 retention error-path tests added after the initial 716-test run (26 tests total in `tests/test_trace_retention.py`); the 3 skips are the allowlisted phase-05 compatibility probes |
| Coverage | `uv run coverage run --source=src/personal_assistant -m pytest` | `PASS` | with PostgreSQL 16 |
| Coverage XML | `uv run coverage xml` | `PASS` | |
| Coverage total | `uv run coverage report --fail-under=85` | `PASS — 91% total line coverage` | threshold 85% |
| Diff coverage | `uv run diff-cover coverage.xml --compare-branch origin/main --fail-under=90` | `PASS — 95% on tracked modified files` | `trace_retention.py` (new file) at 99% line coverage; the single miss is the `if __name__ == "__main__"` guard, the same accepted idiom as `trace_sanitizer.py`; estimated committed-diff coverage ≈97%, threshold 90% |
| Compilation | `uv run python -m compileall -q src` | `PASS` | |
| Build | `uv build` | `PASS` | |
| Dependencies | `uv run pip-audit` | `PASS` | no known dependency vulnerabilities; only notes the unpublished local package is not on PyPI |
| Pre-commit config | `uv run pre-commit validate-config` | `PASS` | implied by the hooks run loading the versioned configuration |
| Pre-commit | `uv run pre-commit run --all-files` | `PASS` | all hooks passed |
| Whitespace | `git diff --check HEAD` | `PASS` | |
| PostgreSQL reliability corpus | `tests/test_postgres_reliability_eval_corpus.py::test_complete_postgres_reliability_corpus_passes` | `PASS` | green within the 716-test PostgreSQL run |
| Full eval gate | `uv run python -m personal_assistant.evals --suite eval/cases` | `PASS — 295/295 passed, 0 failed` | with PostgreSQL 16; includes the 2 new `trace.completeness.v1` cases; legacy corpus and sha256 pin unchanged |
| Non-PostgreSQL evals | `uv run python -m personal_assistant.evals --suite eval/cases --json` | `195/195 PASS` | deterministic-only run from before the DSN was available; subsumed by the 295/295 full gate above |
| Retention PostgreSQL integration tests | `uv run pytest -q tests/test_trace_retention.py` with `TEST_POSTGRES_DSN` | `PASS` | the 3 integration tests verified: dry-run deletes nothing; apply deletes only old rows with other tables untouched and is idempotent on re-apply; CLI dry-run reports counts only; 7 further error-path tests were added afterwards (26 total, all passing with DSN) |
| Focused tests, A1 | `uv run pytest -q tests/test_llm_context_utilization.py` | `21 passed` | |
| Focused tests, A2 | `uv run pytest -q tests/test_trace_completeness.py` | `20 passed` | |
| Focused tests, A5 | `uv run pytest -q tests/test_admin_dashboard.py` | `19 passed (8 pre-existing + 11 new)` | exact-shape `/admin/metrics` contract tests unmodified and green |
| Rollback rehearsal | `git restore --worktree -- tests/test_trace_privacy.py` after a filesystem backup, then re-apply | `PASS` | restore produced a zero-line diff for the path; re-apply restored the exact 1-insertion diff; `uv run pytest tests/test_trace_privacy.py -q` passed 6/6 afterwards |

The 3 skips in the PostgreSQL run are the previously allowlisted phase-05
compatibility probes: the PostgreSQL adapter intentionally does not expose
module-level SQL constants or record-serializer helpers, and the import-order
isolation probe is inapplicable after psycopg has been loaded by the real
PostgreSQL corpus. No critical security, atomicity, delivery, trace,
retention, or eval case is skipped or expected to fail. The 7 retention
error-path tests were added after the initial 716-test run; the final
consolidated full-suite re-run recorded 726 passed, 3 skipped, 36 subtests
passed, and zero failures.

## Secret and privacy review

- [x] No `.env` real, credential, token, key, password, or authenticated URL
      added. `.env.example` gained only documented defaults
      (`TRACE_RETENTION_DAYS="30"`, `LLM_CONTEXT_WINDOW_TOKENS="200000"`).
- [x] Trace metric keys are whitelisted, not payload keys: `privacy.py`
      `_METRIC_KEYS` admits `contextutilization` so only the numeric ratio
      survives redaction.
- [x] The completeness validator's error message names only static
      identifiers (event type and field names), never event payloads.
- [x] The retention CLI prints totals only — schema, cutoff, counts — never
      tenant, trace, or user identifiers, and computes the cutoff inside the
      transaction so the dry-run count and the delete always agree.
- [x] The dashboard context component emits only numeric aggregates
      (samples, p50, p95) and model names; the closed `/admin/metrics`
      contract is untouched and its exact-shape tests are unmodified and
      green.
- [x] New tests sweep PII: `test_trace_privacy.py` fixture now satisfies the
      completeness contract while retaining its redaction assertions;
      `security_boundary_v1.py` probes keep their payload-stripping intent
      with an explicit safe marker.
- [x] ADR-004 non-goals recorded: no multi-tenant isolation claims, no
      sandbox for untrusted/model-generated code, no GA authorization.
- [x] Versioned pre-commit hooks (including private-key and AWS credential
      detection) passed over all files via `pre-commit run --all-files`.
- [x] Staged-name secret guard: the four explicit-path stagings contained no
      `.env`, credential, key, or token path (staged path lists reviewed at
      each `git add`; full-file content detection covered by the versioned
      pre-commit hooks above).

**Evidence and findings (redacted):** none. Full-history scanning remains
gated by the versioned CI security job on the pull request; no improvised
local scan is claimed as evidence.

## Risks and decisions

| ID | Risk or decision | Probability | Impact | Mitigation | Status |
|---|---|---|---|---|---|
| R-01 | Write-time completeness validation is the only behavior-tightening change: an emitter that omits a required field now raises instead of persisting a partial row | L | M | Both recorders share one validator; both eval cases and 20 unit tests pin the contract; emitter fixtures were corrected where they violated it; historical rows remain readable | ACCEPTED |
| R-02 | `context_utilization` depends on provider-reported `input_tokens`; providers that omit usage produce no metric | M | L | Keys are omitted rather than zeroed; the dashboard component degrades to an empty state; the attention signal only fires on observed samples above threshold | ACCEPTED |
| R-03 | Retention pruning is a deletion and cannot reconstruct removed rows | L | M | Dry-run default, literal `--confirm PRUNE_TRACES`, single transaction, runbook instructs backup first, operator-invoked only | ACCEPTED |
| R-04 | ADR-004 is design only; GAP #6 remains open until the days 8-14 implementation phase | H | M | ADR records acceptance criteria with deterministic probes; audit addendum states the scorecard is unchanged | OPEN |

## Rollback plan

| Element | Definition |
|---|---|
| Trigger | Any regression in trace writes, admin snapshot, startup validation, or delivery after integration |
| Rollback point | Phase merge commit `6e95c1f` (before merge: the uncommitted tree on `ac1278d`) |
| Planned command | Before staging: `git restore --worktree -- <path>` per reviewed path. After merge: `git revert -m 1 <merge-commit>` from a `codex/rollback-phase-6-observability-traces` branch per workflow section 6 |
| Data impact | None. No migrations, no schema changes, no deletions at runtime. Write-time completeness validation rejects incomplete new events but never rewrites or deletes historical rows; previously persisted rows remain readable |
| Configuration or flags | All changes are additive with safe defaults: `LLM_CONTEXT_WINDOW_TOKENS=200000` and `TRACE_RETENTION_DAYS=30` apply only when unset; the retention CLI is not wired into any runtime path; the dashboard component degrades fail-closed. No feature flag is required to revert |
| Post gate | Ruff, mypy, focused pytest, and the non-PostgreSQL eval subset from the gate table |
| Owner | `Yosoyepa` |

**Safe rehearsal result:** `PASS` — rehearsed on the uncommitted tree before
staging (see the gate table row above): backup, `git restore --worktree` on
`tests/test_trace_privacy.py`, verified zero diff, re-applied, and its 6
tests passed. The post-commit rollback path is `git revert -m 1
<merge-commit>` from a `codex/rollback-phase-6-observability-traces` branch
per workflow section 6, rehearsed conceptually only; the merge commit does
not exist yet.

## Definition of Done

### Tasks

- [x] Objectives and acceptance criteria met without scope expansion.
- [x] Invariants preserved: tenant scoping, permission tiers, idempotency,
      closed `/admin/metrics` contract, legacy eval corpus and sha256 pin,
      trace redaction rules.
- [x] Working-tree diff reviewed (`git status`, `git diff --stat`,
      `git diff --check`, full diff).
- [x] Explicit-path staging and staged-diff review.
- [x] Focused tests pass with evidence in this log.
- [x] No secrets, real data, or temporary artifacts in the tree.
- [x] Conventional Commits created on the phase branch: `21b6506`
      `feat(observability)`, `3d40f13` `feat(tracing)`, `a18b853`
      `feat(infrastructure)`, `669fddb` `docs`.
- [x] Residual risks recorded.

### Phase

- [x] All five reserved slots delivered implementation or review evidence;
      no empty commits created.
- [x] Wave order respected: wave 2 built on wave 1 deliveries.
- [x] Accepted work integrated into the phase branch
      `codex/phase-6-observability-traces` (four commits; working tree clean
      after integration).
- [x] Complete section-8 gates pass: deterministic and PostgreSQL 16
      evidence green (final consolidated run: 726 passed / 3 allowlisted
      skips / 36 subtests / 0 failures, 295/295 evals, 91% coverage, 95%
      diff-cover, static gates pass).
- [x] Secret review passed, including the versioned pre-commit hooks over all
      files and the staged-path review at staging.
- [x] No open blockages; no repeated failure cycles.
- [x] Rollback rehearsed (pre-staging restore/re-apply rehearsal passed; see
      the rollback plan).
- [x] Single phase PR opened with green CI (#13; quality, tests 3.11/3.12,
      postgres-integration, and security all pass).
- [x] Merge commit integration (`6e95c1f`); phase branch deleted local and
      remote; no worktrees remained.

## Approvals

| Decision | Owner | Date | Evidence / comment |
|---|---|---|---|
| Authorize staging | `Yosoyepa` | 2026-07-28 | Approved in session ("dale continua") after full gate evidence was presented |
| Authorize commit | `Yosoyepa` | 2026-07-28 | Same approval; four Conventional Commits `21b6506`, `3d40f13`, `a18b853`, `669fddb` |
| Authorize PR | `Yosoyepa` | 2026-07-28 | Approved in session after CI+Security green on #13 |
| Authorize merge commit | `Yosoyepa` | 2026-07-28 | Merge commit `6e95c1f` |
| Close objective | `PENDING` | | Requires a full audit re-run; this phase alone does not close any scorecard row |
