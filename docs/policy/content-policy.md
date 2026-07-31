# Content Policy

Status: ratified
Scope: enabled workflows — Telegram reminders, document summarization, local runtime
Enforcement: deterministic, local regex scanning in
`src/personal_assistant/domain/common/guardrails.py`. No external moderation
APIs are used; every rule below is a local, auditable pattern with a stable ID.

## Severity model

| Severity | Meaning | Effect on a scan result |
| --- | --- | --- |
| MEDIUM (`flag`) | The finding is recorded in `GuardrailResult.findings` for logging, redaction, or review. | Does **not** block. `result.blocked` stays `False`. |
| HIGH (`block`) | The finding marks the text unsafe. | Blocks. `result.blocked` is `True`; `assert_prompt_safe` / `assert_output_safe` raise `AssistantError` (`GuardrailViolation`, code `guardrail_blocked` for output). |

Default convention: every new rule is born as **flag (MEDIUM)**. A rule is
only promoted to **block (HIGH)** when the harm it prevents is explicit,
high-risk, and effectively irreversible (credential leakage, data
exfiltration, hidden-instruction disclosure, destructive commands), and the
pattern is narrow enough that false positives are negligible. The per-rule
rationale below documents each promotion.

Scope legend: **input** = text received from the user (scanned by
`scan_prompt`); **output** = text produced by the assistant (scanned by
`scan_output`).

## Input rules

These rules cover abuse patterns not already covered by the prompt-injection
and PII pattern tables.

### CP-IN-001 — Violent threat (input, flag / MEDIUM)

- **Label:** `cp_in_001_violent_threat`
- **Description:** flags reminder or task text that pairs a violent verb
  (`kill`, `murder`, `assassinate`, `bomb`, `hurt`) with a person target
  (`you`, `him`, `her`, `them`, `someone`, `my boss/partner/neighbor`).
- **Rationale:** a personal assistant must not become a planning tool for
  violence. The signal is recorded for review, but the rule stays a flag:
  benign idioms and fictional contexts ("kill time", movie plots) make
  blocking too risky, and there is no explicit irreversible harm caused by
  merely storing the text.

### CP-IN-002 — Secret sharing (input, flag / MEDIUM)

- **Label:** `cp_in_002_secret_sharing`
- **Description:** flags user input that pastes credential-shaped assignments
  into the assistant, e.g. `password is hunter2`, `api_key=...`, `token: ...`.
- **Rationale:** secrets stored in transcripts and reminder payloads expand
  the blast radius of any future data leak, so they are flagged for redaction
  pipelines and review. Blocking is rejected: users legitimately reference
  credentials in reminders ("renew the API token"), and the PII category
  already covers personal data separately.

## Output rules

Assistant replies and notification bodies are scanned before they are
returned or delivered. All four rules are explicit high-risk rules and are
therefore born as **block (HIGH)**; the rationale for each promotion is
documented per rule.

### CP-OUT-001 — Credential material (output, block / HIGH)

- **Label:** `cp_out_001_credential_material`
- **Description:** blocks replies containing credential/token-shaped strings:
  OpenAI-style `sk-...` keys, AWS `AKIA...` access key IDs, GitHub `ghp_...`
  tokens, Slack `xox[baprs]-...` tokens, Telegram bot tokens
  (`<digits>:<35 chars>`), `Bearer ...` authorization values, and PEM private
  key headers (`-----BEGIN ... PRIVATE KEY-----`).
- **Rationale:** leaking a live credential in a chat reply or notification is
  irreversible — once delivered, the secret must be rotated. Detection is
  shape-based (long, provider-specific token formats), so false positives on
  natural-language replies are negligible. This justifies block over flag.

### CP-OUT-002 — Exfiltration instruction (output, block / HIGH)

- **Label:** `cp_out_002_exfiltration_instruction`
- **Description:** blocks replies that instruct sending, posting, uploading,
  emailing, or forwarding content to an external `http(s)://` endpoint, or
  that use explicit exfiltration language (`exfiltrate ...`).
- **Rationale:** an assistant reply that talks the user (or a downstream
  automation) into moving data to an external endpoint enables irreversible
  data loss, typically as the payload of a prompt-injection attack. Legitimate
  reminder copy in this product never contains URLs, so the false-positive
  cost of blocking is negligible.

### CP-OUT-003 — Hidden-instruction leak (output, block / HIGH)

- **Label:** `cp_out_003_hidden_instruction_leak`
- **Description:** blocks replies that disclose system-prompt, developer
  message, or hidden-instruction content, e.g. `system prompt: ...`,
  `my hidden instructions are ...`.
- **Rationale:** hidden instructions are part of the product's trust
  boundary; leaking them hands attackers a map for future prompt-injection
  attempts. Templated product copy never references these artifacts, so
  blocking carries no legitimate-use cost.

### CP-OUT-004 — Destructive action (output, block / HIGH)

- **Label:** `cp_out_004_destructive_action`
- **Description:** blocks replies that instruct destructive shell or database
  actions: `rm -rf ...`, `del /f|/s|/q ...`, `format <drive>:`, `DROP TABLE`,
  `delete all files|data|records`, `wipe the disk|drive|database`.
- **Rationale:** a reply that instructs an irreversible destructive command
  can cause unrecoverable damage if the user or an automation follows it.
  The patterns are deliberately command-shaped (not the words "delete" or
  "remove" alone) so ordinary Spanish product copy ("puedes borrar el
  recordatorio") is never affected; with false positives structurally
  excluded, the rule is born as block rather than flag.

## Error-payload hygiene

When `assert_output_safe` blocks, the raised `GuardrailViolation` carries an
error context with **categories, labels, and severities only**. Raw matched
text and excerpts are never included in the error payload, so blocked-output
diagnostics cannot themselves leak credentials or user content. (Findings
returned inside `GuardrailResult` follow the existing excerpt convention:
short, bounded excerpts for local debugging, never propagated into structured
error responses.)

## Maintenance

- New rules are added as data rows in `CONTENT_POLICY_INPUT_PATTERNS` or
  `CONTENT_POLICY_OUTPUT_PATTERNS`, born as flag (MEDIUM) unless they meet
  the block criteria above; the promotion rationale must be documented here.
- Every rule keeps a stable ID (`CP-IN-###` / `CP-OUT-###`), a matching
  snake_case label, and one positive plus one negative test case in
  `tests/test_content_policy_guardrails.py`.
