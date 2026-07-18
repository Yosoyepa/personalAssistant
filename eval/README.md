# Executable Evaluation Gates

The release suite is a versioned Level 1 evaluation gate: every active case
executes deterministic application or domain behavior without an LLM or network
access. A mismatch, invalid schema, corrupt legacy inventory, unknown executor,
duplicate ID, or filter that selects zero cases exits nonzero.

Run the complete suite with human-readable output:

```bash
uv run python -m personal_assistant.evals --suite eval/cases
```

Use `--json` for machine-readable stdout. Filters may be repeated and are
combined across dimensions:

```bash
uv run python -m personal_assistant.evals --suite eval/cases --category temporal --tier failure-mode --failure-mode dst-gap --json
```

## Version 1 layout

`eval/cases/suite.json` is the only discovery manifest. Its ordered `caseFiles`
list is explicit; files are never imported by glob. Absolute paths, parent
traversal, resolved path escapes, and duplicates are rejected. Each case file
has this envelope:

```json
{"schemaVersion": 1, "cases": []}
```

Every case requires `id`, `category`, `tier`, `failureMode`, non-empty
`contractRefs`, `executor`, `input`, and static `expected`; `tags` is optional.
IDs are unique across the complete suite. Tiers are `golden`, `failure-mode`,
or `regression`.

Executor slug `example.probe.v1` resolves only to
`personal_assistant.evals.executors.example_probe_v1`. The module must export
strict Pydantic `InputModel` and `ExpectedModel` schemas plus callable
`execute(input_model)`. The runner validates both declared expected output and
actual output through `ExpectedModel` before making one binary pass/fail
decision. Error reports omit inputs and actual values.

## Legacy migration

The original 27 fixtures remain byte- and ID-verifiable through
`legacySource`. Twenty-six were migrated to executable deterministic probes in
`legacy-contracts.v1.json`. The empty production placeholder is explicitly
retired in manifest metadata and never counts as a passing case.

`legacy.pytest.v1` has an immutable exact allowlist and a scrubbed subprocess
environment. It exists only to bridge those named migrated cases; new evals
must use a dedicated executor and must never extend this adapter.

New production failures become permanent executable regression cases. Do not
add placeholders, skips, generated expected values, or LLM judges for behavior
that code can verify.

## PostgreSQL reliability corpus

The `atomicity-recovery` and `delivery-concurrency` categories are blocking
integration gates. They require PostgreSQL 16 plus `psycopg`, and read only
`TEST_POSTGRES_DSN`. The DSN may be passwordless while `PGPASSWORD` is supplied
to the process environment. A missing DSN is an explicit sanitized failure,
never a skip or an empty pass.

Every case creates a cryptographically unique `eval_reliability_*` schema,
runs the repository migrations against it, executes production UoW/outbox
contracts, and drops only that validated schema. These cases never contact an
LLM or Telegram; deterministic finite provider scripts stand in only for the
external I/O boundary, while all critical persistence is real PostgreSQL.
