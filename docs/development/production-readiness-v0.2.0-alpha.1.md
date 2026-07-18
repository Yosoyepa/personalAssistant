# Production-readiness audit — v0.2.0-alpha.1

Audit scope: the PostgreSQL-backed, single-operator alpha. Evidence is limited
to repository code, deterministic tests, and CI configuration. Absence of
production traces is treated as a gap, not as proof of safety.

Launch decision: **alpha endurecida, no GA**. Controlled alpha use is accepted
with the gaps below; general availability is explicitly rejected.

| # | Definition of Done | Result | Evidence / reason | Closure effort | Priority |
|---:|---|---|---|---:|---|
| 1 | Typical context utilization below 40% | **GAP** | No production context-utilization metric, median, or p95 exists. | 3–5 days | P0 |
| 2 | State externalized | **PASS** | PostgreSQL persists approvals, workflow state, events, outbox, scheduler, memory, and traces; restart/replay tests exercise recovery. This pass applies only to Postgres mode. | 1–2 days/quarter to maintain | P2 |
| 3 | Compaction tested on long sessions | **GAP** | There is no compaction pipeline, metric-driven trigger, or >50-turn loss test. | 5–8 days | P0 |
| 4 | Destructive actions require human approval | **N/A** | Destructive/bulk-delete tools are forbidden and absent. Existing P3/P5 sends and calendar effects use code-enforced approvals and auditable state. Reassess before adding any destructive tool. | 1 day when scope changes | P1 |
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
