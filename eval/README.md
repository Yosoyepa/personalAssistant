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
uv run python -m personal_assistant.evals --suite eval/cases --category temporal --tier failure-mode --failure-mode temporal-misinterpretation --failure-mode-detail dst-gap --json
```

## Top failure modes

Every case in the five largest failure families carries a canonical top-mode
slug in `failureMode`. The previous fine-grained slug is preserved in the
optional `failureModeDetail` field, recorded immediately after `failureMode`;
cases outside these families keep their fine-grained `failureMode` and omit
the detail. `--failure-mode` filters on the canonical slug and
`--failure-mode-detail` filters on the fine-grained one.

| Top failure mode | Definition | Contract refs | Executor | Case file | Cases |
|---|---|---|---|---:|---:|
| `reminder-atomicity-violation` | Multi-write reminder commits and crash recovery leave partial state | FM-12, FM-17 | `reminder.atomicity.postgres.v1` | `atomicity-recovery-postgres.v1.json` | 50 |
| `delivery-concurrency-violation` | Concurrent workers or stale leases double-deliver or lose outbox rows | FM-12 | `outbox.delivery.postgres.v1` | `delivery-concurrency-postgres.v1.json` | 50 |
| `idempotency-replay-duplicate` | Replayed events reuse identity yet must never duplicate or collide effects | FM-03, FM-16, FM-17 | `reminder.idempotency.v2` | `idempotency.v2.json` | 57 |
| `temporal-misinterpretation` | Reminder time expressions parse to the wrong instant or timezone | FM-02 | `reminder.extract.v1` | `temporal.v1.json` | 60 |
| `security-boundary-breach` | Untrusted input crosses an authority, allowlist, or redaction boundary | FM-01, FM-07, FM-18 | `security.boundary.v1` | `security-privacy.v1.json` | 50 |

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

That prohibition is about **this suite**, and it is unchanged. A separate
Level-2 behavioral tier exists for the two surfaces code cannot verify; see
"Behavioral tier (Level 2)" below.

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

## Behavioral tier (Level 2)

The 299 cases above are deterministic and **none of them exercises an LLM**. The
runtime has two real LLM call sites, and the Level-2 tier is where they get
measured:

| Surface | Call site |
|---|---|
| `intent-classification` | `_infer_intent` (`use_cases/commands.py`) |
| `reminder-extraction` | `_extract_with_llm` (`use_cases/reminders.py`) |

It is a separate package (`src/personal_assistant/evals/behavioral/`) and a
separate corpus (`eval/behavioral/`) on purpose. L1 decides by exact equality,
which is right for a release gate and wrong for graded human judgement. Rather
than loosen L1, the tiers stay apart — see
`docs/adr/ADR-006-behavioral-eval-tier-and-judge.md`.

```bash
# Replay committed cassettes. No network, no provider, deterministic.
uv run python -m personal_assistant.evals.behavioral \
  --corpus eval/behavioral --mode replay

# Filters
--surface intent-classification   --split holdout   --tag relative-time   --json
```

### What blocks and what does not

Exit 1 means a **harness** failure: a cassette that cannot answer a request, a
payload the runtime's own parser rejects, a judge that broke. A missing cassette
entry in replay mode is an explicit sanitized failure, never a skip and never an
empty pass — the same policy this file applies to a missing `TEST_POSTGRES_DSN`.

Model disagreement with a human label exits **0**. Disagreement is the
measurement this tier publishes; gating on it would make CI depend on a provider
matching one person's judgement.

CI runs `--mode replay` only.

### Provenance

`Cassette.provenance` is required and has no default. `recorded` means a real
provider answered; `synthetic` means the payloads came from a fixture. Only
`recorded` runs count as calibration evidence — `--mode replay` prints a warning
above the rates otherwise, and `judge_authority()` refuses to promote the judge.

**The cassettes committed today are `synthetic`.** No live pass has run, so the
rates they produce measure the harness rather than any model. See
`docs/development/judge-calibration-v1.md`.

### Changing a prompt invalidates the cassettes

The cassette key is a sha256 of the rendered prompt, so editing anything under
`prompts/` (or `CORPUS_NOW`) makes every entry miss and replay fail. That alarm
is intended: re-record with `--mode record` against a configured provider.
