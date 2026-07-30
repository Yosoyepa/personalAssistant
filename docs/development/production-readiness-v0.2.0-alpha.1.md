# Production-readiness audit — v0.2.0-alpha.1

Audit scope: the PostgreSQL-backed, single-operator alpha. Evidence includes
repository code, deterministic tests, CI configuration, and the controlled
PostgreSQL/Telegram delivery smoke recorded in the Phase 5 acceptance ledger.
Absence of production traces is treated as a gap, not as proof of safety.

Launch decision: **alpha endurecida, no GA**. Controlled alpha use is accepted
with the gaps below; general availability is explicitly rejected.

Scorecard: **4 PASS / 8 GAP / 0 N/A**.

| # | Definition of Done | Result | Evidence / reason | Closure effort | Priority |
|---:|---|---|---|---:|---|
| 1 | Typical context utilization below 40% | **GAP** | No production context-utilization metric, median, or p95 exists. | 3–5 days | P0 |
| 2 | State externalized | **PASS** | PostgreSQL persists approvals, workflow state, events, outbox, scheduler, memory, and traces; deterministic recovery tests and the controlled reminder smoke proved replay plus restart before delivery. This pass applies only to Postgres mode. | 1–2 days/quarter to maintain | P2 |
| 3 | Compaction tested on long sessions | **GAP** | There is no compaction pipeline, metric-driven trigger, or >50-turn loss test. | 5–8 days | P0 |
| 4 | Destructive actions require human approval | **PASS** | The human's explicit reminder request authorizes the calendar write and its future notification; code binds calendar, send, and manual-reconciliation effects to resource-scoped P3/P5 grants and durable audit state. Delete/bulk-destructive tools remain forbidden and absent. Reassess before adding any new write, send, charge, or delete capability. | 1 day when scope changes | P1 |
| 5 | Permissions enforced by code | **PASS** | Trusted principals are server-derived; tenant authority cannot come from request/model text; P3/P5 grants and webhook allowlists fail closed in code. | 1–2 days/quarter to maintain | P2 |
| 6 | Tool execution sandboxed | **GAP** | The runtime has no container/VM execution sandbox or network-domain allowlist. Secrets are configured at adapter boundaries, but host/network isolation is incomplete. | 5–10 days | P0 |
| 7 | Durable pause/resume/retry survives a killed process | **GAP** | Durable delivery, leases, approval resume, idempotency, and fault injection exist, but no automated kill/restart exercise proves the whole process lifecycle or multi-day human waits. | 3–5 days | P0 |
| 8 | Structured outputs and critical assertions | **PASS** | Pydantic schemas forbid extras on critical DTO/eval paths; deterministic state-transition invariants and database constraints reject invalid delivery states. | 1–2 days/quarter to maintain | P2 |
| 9 | Input/output guardrails | **GAP** | PII and prompt-injection defenses plus output schemas exist; content-policy/citation guardrails and production hit-rate metrics do not. | 4–7 days | P1 |
| 10 | ≥50 evals per top failure mode | **GAP** | The deterministic suite is broad and release-gated, but five named product failure modes do not each have ≥50 representative cases. Counts aggregated by a broad family/category do not satisfy the literal ≥50 requirement for each distinct `failureMode`. | 8–12 days | P1 |
| 11 | LLM judges calibrated to human labels | **GAP** | This release uses deterministic assertions, not a calibrated behavioral judge; no ≥100-label calibration set or TPR/TNR report exists. | 5–8 days | P1 |
| 12 | CI blocks eval regressions and all production traces are retained | **GAP** | CI now blocks on the full eval command. However, 100% trace coverage, required-field completeness, and a 30–90 day production retention policy are not demonstrated. | 4–7 days | P0 |

Supplemental tenant-isolation assessment: code-derived tenant authority,
tenant-scoped repository queries, cross-tenant adversarial tests, and
tenant-scoped persistence are present. A production database RLS policy and a
documented trace/cache/sub-agent isolation proof are not present; the alpha
must remain single-operator and must not be marketed as fully isolated SaaS.

## Accepted alpha risks

- No measured context budget and no compaction safety net.
- No tool sandbox or outbound network allowlist.
- No end-to-end killed-process recovery exercise.
- Evals are deterministic and useful but not statistically deep per failure
  mode; no calibrated judge exists.
- CI enforcement is stronger than observability: trace completeness and
  retention remain unproven.

## Risk-adjusted 30-day roadmap

- Days 1–7 (P0): add context-size telemetry, required trace fields, trace
  completeness assertions, retention configuration, and an outbound network
  allowlist/sandbox design.
- Days 8–14 (P0): implement metric-triggered compaction and long-session loss
  evals; containerize tool/provider execution and inject secrets only at the
  adapter boundary.
- Days 15–21 (P0/P1): automate kill/restart plus long human-wait tests; add
  guardrail hit-rate telemetry and close content/citation policy gaps relevant
  to enabled workflows.
- Days 22–30 (P1): define five top failure modes, grow each to at least 50
  representative cases, label a calibration set, publish judge TPR/TNR if an
  LLM judge is introduced, and rerun this audit before any GA decision.

GA requires every numbered GAP to be closed or a new explicit risk acceptance
by the release owner; this document does not authorize GA.

## Phase 06 progress addendum (v0.2.0-alpha.1 + phase 06)

Status of this addendum: phase 06 is `MERGED` (hardening log:
`docs/development/hardening/phase-06-observability-traces.md`; PR #13, merge
commit `6e95c1f`, hosted CI and Security green on the PR and post-merge on
`main`). The PostgreSQL-dependent evidence that was
initially pending has since been recorded against PostgreSQL 16 and is
reflected below. The scorecard above is a published historical artifact of
the alpha.1 decision and is
**unchanged: 4 PASS / 8 GAP / 0 N/A** until a full audit re-run. GA remains
unauthorized.

Delivered for the days 1-7 P0 roadmap items:

- **GAP #1 (context telemetry) — reframed per call.** The original DoD
  ("typical context utilization below 40%") presupposes conversational
  multi-turn context. This runtime's LLM calls are single-shot: each call
  assembles one prompt from the current message plus fixed context
  references, and no conversation history is carried across calls. Phase 06
  therefore defines utilization per LLM call as `input_tokens` over the
  configured context window (`LLM_CONTEXT_WINDOW_TOKENS`, default 200000),
  recorded on every `llm.called` trace event at both call sites and omitted
  — never zeroed — when the provider reports no usage. The admin dashboard
  exposes operator-visible p50/p95 (nearest-rank on observed samples) plus
  per-model call counts, with an attention signal when p95 exceeds 0.40, the
  threshold from this row's DoD. Whether typical utilization is in fact below
  40% can now be measured; it is not yet asserted, and this addendum does not
  claim the GAP closed.
- **GAP #3 (compaction)** remains a GAP: there is still no conversation
  history to compact, and closing it requires a conversation-history design
  decision before any compaction pipeline or loss eval can exist.
- **GAP #12 (trace completeness and retention) — partially addressed.** A
  module-level required-fields contract per trace event type is now enforced
  fail-closed at write time by both trace recorders, before privacy
  redaction, and the deterministic eval gate gained a `trace.completeness.v1`
  executor with two cases (suite 293 -> 295 cases; legacy corpus and its
  sha256 pin untouched). Trace retention now has a configured window
  (`TRACE_RETENTION_DAYS`, default 30, inside the accepted 30-90 day policy),
  an operator-invoked pruning CLI (dry-run default, literal confirmation,
  single transaction, totals-only output), and a documented runbook policy.
  Pruning is never automatic at runtime. The CLI's PostgreSQL integration
  tests now pass against PostgreSQL 16 (`postgres:16-alpine`): the dry-run
  deletes nothing, apply deletes only old rows with other tables untouched
  and is idempotent on re-apply, and the CLI dry-run reports counts only;
  seven further error-path tests were added since (26 tests total in
  `tests/test_trace_retention.py`, all passing with DSN). A live production
  retention prune has still not been executed — only the deterministic
  integration tests — so retention behavior in real operation remains to be
  observed and this row stays a GAP.
- **GAP #6 (tool execution sandbox) — design only.** ADR-004
  (`docs/adr/ADR-004-tool-execution-sandbox.md`, Status: Proposed) records a
  two-layer design: an adapter-level, deny-by-default outbound allowlist
  (exact scheme + hostname, fail-closed, `EGRESS_ALLOWED_HOSTS`) plus a
  single locked-down container, with secrets still injected only at adapter
  boundaries. No runtime behavior changed; implementation is scheduled for
  the days 8-14 roadmap window.

Verification at writing time: ruff pass; mypy pass (115 source files); the
full pytest suite against PostgreSQL 16 (`personal-assistant-pg16-phase3`,
`postgres:16-alpine`, DSN via `TEST_POSTGRES_DSN`) passed 716 tests with 3
allowlisted compatibility-probe skips, 36 subtests passed, and zero failures —
including the reliability corpus gate and the retention CLI integration
tests. Seven additional retention error-path tests were added after that
initial run (26 total in `tests/test_trace_retention.py`); the final
consolidated full-suite re-run passed 726 tests with the same 3 allowlisted
skips, 36 subtests, and zero failures. The
complete eval gate passed 295/295 with zero failures. Coverage reached 91%
total line coverage (threshold 85%); diff-cover against `origin/main`
reached 95% on tracked modified files, with `trace_retention.py` at 99% (the
single miss is the `__main__` guard, the accepted `trace_sanitizer.py`
idiom) and estimated committed-diff coverage of ≈97% (threshold 90%). The
static gates — `uv lock --check`, compileall, `uv build`, `pip-audit` (no
vulnerabilities), `pre-commit run --all-files`, and `git diff --check` — all
pass. What remains pending is procedural, not evidential: the phase branch,
explicit staging, the single phase PR, and hosted CI.

## Phase 07 progress addendum (v0.2.0-alpha.1 + phase 07)

Status of this addendum: phase 07 implements the ADR-004 sandbox design and
the automated kill/restart exercise (hardening log:
`docs/development/hardening/phase-07-sandbox-recovery.md`). The scorecard
above remains a published historical artifact of the alpha.1 decision and is
**unchanged: 4 PASS / 8 GAP / 0 N/A** until a full audit re-run. GA remains
unauthorized.

Delivered for the days 8–14 and days 15–21 P0 roadmap items:

- **GAP #6 (tool execution sandbox) — implemented per ADR-004.** Layer A:
  every network adapter validates its target against a deny-by-default,
  exact `scheme + hostname` allowlist before opening a connection; the
  effective allowlist derives from the configured provider base URLs plus
  `api.telegram.org`, a non-empty `EGRESS_ALLOWED_HOSTS` is an explicit
  override, startup fails closed in `AppSettings` when an enabled provider's
  target is uncovered, and the startup audit record carries hostnames only.
  Layer B: a hardened single-unit container (multi-stage `Dockerfile`,
  non-root uid 10001) with a compose profile enforcing a read-only root
  filesystem, dropped capabilities, and `no-new-privileges`; the build and
  the hardened smoke passed locally (see the phase log). Whether this fully
  closes the GAP is for the audit re-run to decide.
- **GAP #7 (durable pause/resume/retry survives a killed process) —
  automated exercise added.** `tests/test_process_recovery_postgres.py`
  kills spawned worker processes at three instrumented points — before the
  claim commit, after the sending commit before provider I/O, and in the
  middle of provider I/O — against PostgreSQL 16, then asserts exactly-once
  delivery, sweep of expired `sending` leases to `uncertain` without
  automatic resend, and operator-approved reconciliation. Multi-day human
  waits remain unexercised; the audit re-run decides whether the row
  closes.
- **Accepted risk note.** A pre-existing test-isolation issue in
  `tests/test_public_artifacts.py` (passes in isolation, fails in full
  runs) was observed during phase 07 and is handled inside the phase as a
  hardening task; it is unrelated to the sandbox or recovery changes.
