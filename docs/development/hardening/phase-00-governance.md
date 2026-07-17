# Phase 00 — reproducible baseline and repository governance

## Identity

| Field | Value |
|---|---|
| Status | `ACCEPTED_FOR_MERGE` |
| Maintainer | `Yosoyepa <jandradeu@unal.edu.co>` |
| Phase branch | `codex/phase-0-governance` |
| Base commit | `865dd63` |
| Local acceptance commit | `3af4425` |
| Pull request | [#1](https://github.com/Yosoyepa/personalAssistant/pull/1) |
| Date | `2026-07-17` |

## Objective and acceptance

Establish a locked development environment, a clean static-typing baseline,
executable characterization of the known alpha failure modes, stable CI checks,
and an auditable single-maintainer GitHub workflow.

The phase does not fix the characterized reminder, authentication, atomicity, or
delivery defects. Those five cases remain strict expected failures until the
owning phases remove their markers.

## Agent ledger

| Role | Goal | Commit | Decision |
|---|---|---|---|
| P0-A1 | Reproducible toolchain and secret hooks | `029fd54` | accepted |
| P0-A2 | Branch, review, commit and rollback governance | `794790b` | accepted after one rework |
| P0-A3 | Strict characterization of critical defects | `18afd2c` | accepted |
| P0-A4 | Remove 26 Mypy diagnostics without behavior change | `585d2be` | accepted |
| P0-A5 | CI, CODEOWNERS, Dependabot and branch protection plan | `6a38aeb`, `32f9242` | accepted after two reworks and remote verification |

All five goals were marked complete only after review of their unstaged diffs,
targeted validation, explicit staging, and Conventional Commit creation.

## Verification evidence

Executed from the integrated phase branch with `APP_ENV_FILE=disabled`:

| Gate | Result |
|---|---|
| `uv lock --check` | pass |
| `uv sync --frozen --all-extras --group dev` | pass |
| `uv run ruff check .` | pass |
| `uv run mypy src` | pass, 85 source files and zero diagnostics |
| `uv run pytest -q` | 137 passed, 2 skipped, 5 strict xfailed |
| coverage | 85% total line coverage |
| diff-cover | 91%, threshold 90% |
| `uv run python -m compileall -q src` | pass |
| `uv build` | sdist and wheel built |
| `uv run pip-audit` | no known dependency vulnerabilities |
| `uv run pre-commit run --all-files` | all hooks passed |
| `git diff --check origin/main...HEAD` | pass |
| governance PowerShell regression | pass without remote calls |
| governance `Verify` against GitHub | pass with exact desired state |

The PostgreSQL 16 service check is defined in CI. Docker Desktop was installed
but not running during local acceptance, so the remote `postgres-integration`
check remains mandatory before merge.

## Security review

- `graphify-out/`, local environments, coverage output and build output are not
  staged.
- No provider credentials are required by CI; all external providers are
  disabled.
- PostgreSQL CI credentials are disposable test-only values.
- Pre-commit private-key and credential checks passed.
- GitHub CLI 2.96.0 was installed from the official Winget package.
- GitHub authentication uses the browser/device flow; no token is placed in a
  command, file, log, or conversation.

## Required remote evidence

The phase must not be marked `MERGED` until all of the following are recorded:

- [x] phase branch pushed;
- [x] phase pull request created;
- [x] `quality`, `tests (3.11)`, `tests (3.12)`, `security`, and
      `postgres-integration` green on code-and-governance head `77418ba`;
- [x] desired GitHub governance applied and verified;
- [ ] merge commit recorded.

Branch protection must repeat the required checks for this history-only update
before the pull request can merge.

## Risks and rollback

- The five strict xfails intentionally keep critical defects visible. An XPASS
  blocks the suite until the owning phase converts the case into a normal test.
- Coverage uses the standard line-coverage display precision; changed-line
  coverage is independently gated at 90%.
- The first remote `Apply` exposed an empty-collection binding defect and an
  invalid `allow_fork_syncing=true` expectation for an unlocked branch. Both
  are covered by a network-free PowerShell regression in CI.
- GitHub branch protection may require repository-owner permissions. Failure to
  apply it blocks remote completion but does not invalidate the local commits.

Rollback is a single revert of the eventual phase merge commit. This phase has
no data migration and introduces no external side effects.
