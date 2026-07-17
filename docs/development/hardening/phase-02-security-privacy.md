# Phase 02 — trusted local boundaries and trace privacy

## Identity

| Field | Value |
|---|---|
| Status | `MERGED` |
| Maintainer | `Yosoyepa <jandradeu@unal.edu.co>` |
| Phase branch | `codex/phase-2-security-privacy` |
| Base commit | `dbc3168` |
| Accepted implementation head | `dfd90c8` |
| Local acceptance commit | `fb7fe56` |
| Pull request | [#9](https://github.com/Yosoyepa/personalAssistant/pull/9) |
| Merge commit | `0925425` |
| Date | `2026-07-17` |

## Objective and acceptance

Close the local runtime trust boundary, authenticate Telegram webhook traffic,
make the Telegram allowlist default-deny, and prevent sensitive content from
crossing trace, error, administration, and persistence boundaries.

The phase was accepted locally only after forged identity headers could no
longer change tenant, principal, permission tier, or scopes; non-loopback and
unauthenticated callers could not reach runtime or administration surfaces;
invalid Telegram traffic produced no effects; and centralized redaction was
verified at DTO, storage, PostgreSQL, HTTP, and administration boundaries.

## Agent ledger

| Role | Goal | Commit | Decision |
|---|---|---|---|
| P2-A1 | Enforce a trusted loopback and bearer-token local principal | `b6d41db` | accepted after `ADMIN_TOKEN` unification and authentication grammar review |
| P2-A2 | Require a constant-time Telegram secret check and default-deny actor allowlist | `130e4c3` | accepted |
| P2-A3 | Redact sensitive trace and error content at every serialization and storage boundary | `8da2b11` | accepted after the administration error boundary was closed |
| P2-A4 | Integrate HTTP trust policies and adversarial end-to-end tests | `6da5414` | accepted |
| P2-A5 | Document hardened local exposure, rotation, verification, and rollback | `0bc643b` | accepted after PowerShell and proxy hardening rework |
| P2-A5 rework | Replace documentation examples rejected by the public-artifact secret scanner | `e25846a` | accepted |

The orchestrator integrated accepted branches with merge commits. Commit
`77081fa` aligns two public HTTP assertions with the deliberately generic
privacy-preserving error contract; it does not weaken the redaction policy.

## Functional and security decisions

- `LocalPrincipalProvider` accepts only a numeric loopback peer and a strict
  `Authorization: Bearer <ADMIN_TOKEN>` value. The token digest is compared
  with `secrets.compare_digest` and authority comes only from server settings.
- `/v1/runtime/*`, `/admin`, and `/admin/*` use that provider. Client-supplied
  principal, tenant, permission tier, scopes, and impersonation parameters do
  not define authority.
- A missing `ADMIN_TOKEN` leaves local runtime and administration surfaces
  inaccessible without preventing health, readiness, or webhook startup.
- Telegram exposes only `POST /webhooks/telegram`. It requires
  `X-Telegram-Bot-Api-Secret-Token`, validates it in constant time, denies all
  actors when the allowlist is empty, and never substitutes a chat identifier
  for a missing user actor.
- Rejected Telegram updates do not create workflow state, calendar entries,
  scheduler rows, events, outbox messages, or replies.
- Trace sanitization is centralized and fail-closed. It retains approved
  identifiers, hashes, sizes, and categories while redacting messages,
  transcripts, prompts, credentials, sensitive URLs, binary content, and
  unclassified fields.
- Sanitization is enforced at trace DTO serialization, in-memory and
  PostgreSQL storage, reads, workflow errors, outbox errors, events, and
  administration responses.
- The deployment contract exposes only the Telegram webhook through an HTTPS
  reverse proxy. Runtime and administration interfaces remain bound to
  loopback.

## Verification evidence

Executed from the integrated phase branch with external providers disabled:

| Gate | Result |
|---|---|
| `uv lock --check` | pass |
| `uv sync --frozen --all-extras --group dev` | pass |
| `uv run ruff check .` | pass |
| `uv run mypy src` | pass, 87 source files and zero diagnostics |
| `uv run pytest -q` | 398 passed, 2 skipped, 3 strict xfailed, 2 subtests passed |
| coverage | 89% total line coverage, threshold 85% |
| diff-cover | 92% over 502 changed lines, threshold 90% |
| `uv run python -m compileall -q src` | pass |
| `uv build --quiet` | sdist and wheel built |
| `uv run pip-audit` | no known dependency vulnerabilities; the unpublished local package is not auditable through PyPI |
| `uv run pre-commit run --all-files` | all hooks passed |
| public-artifact secret scanner | pass |
| `git diff --check` | pass |

The three remaining strict expected failures are owned by later phases:

1. atomic recovery after a crash between effects and terminal workflow state
   (phase 3);
2. concurrent worker double delivery caused by the missing atomic claim
   (phase 4);
3. worker restart after provider acceptance but before marking the reminder
   sent (phase 4).

The forged-header characterization is now a normal passing test.

## Remote evidence

GitHub accepted head `fb7fe56` after every protected check completed:

- `quality`: pass;
- `tests (3.11)`: pass;
- `tests (3.12)`: pass;
- `security`: pass;
- `postgres-integration`: pass.

The pull request was mergeable and clean under branch protection. GitHub
created merge commit `0925425` with parents `dbc3168` and `fb7fe56`. Required
PR checks and resolved conversations remain enforced; force-push and deletion
remain prohibited for `main`. Merge commits remain enabled while squash and
rebase remain disabled.

## Data and migration review

- No schema migration, destructive data operation, production credential,
  provider secret, message body, transcript, or real personal data was added.
- Existing PostgreSQL trace JSONB rows may still contain historical raw values
  at rest. All current reads are sanitized, and the runbook requires an
  explicit purge or rewrite before treating historical storage as clean.
- New writes are sanitized before persistence. External Telegram, LLM,
  transcription, and TTS providers remain fake or disabled in tests.

## Risks and rollback

- Stable SHA-256 digests of low-entropy values can permit correlation or
  offline enumeration. They are metadata, not an anonymization guarantee.
- The fail-closed trace allowlist discards future metadata until each new field
  is deliberately classified.
- Historical trace JSONB can remain sensitive at rest until the documented
  purge or rewrite is performed.
- The public-edge proxy policy is operational configuration and must be
  verified on every deployment; application tests cannot prove the deployed
  route table.
- Starlette emits one upstream `TestClient` deprecation warning that does not
  affect behavior.
- Reminder creation and notification delivery remain non-atomic/non-durable
  until phases 3 and 4.

Rollback is a single revert of the eventual phase merge commit. This phase has
no database downgrade and requires no data deletion.
