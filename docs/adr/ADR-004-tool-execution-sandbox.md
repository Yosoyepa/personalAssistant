# ADR-004: Tool Execution Sandbox — Outbound Allowlist and Process Isolation

## Status

Proposed

This ADR records the design only. Implementation is scheduled for a later
phase of the v0.2.0-alpha.1 risk-adjusted roadmap (days 8–14). It does not
change any runtime behavior today.

## Date

2026-07-28

## Context

The production-readiness audit
(`docs/development/production-readiness-v0.2.0-alpha.1.md`) flags GAP #6: the
runtime has no container/VM execution sandbox and no network-domain allowlist.
Secrets are configured at adapter boundaries, but host/network isolation is
incomplete. The roadmap schedules this design in days 1–7 and implementation
("containerize tool/provider execution and inject secrets only at the adapter
boundary") in days 8–14.

The outbound network surface is small and enumerable:

| Component | Port | Adapter | Network target |
|---|---|---|---|
| LLM | `application.ports.services.LLMProvider` | `adapters.outbound.llm.minimax.MiniMaxLLMProvider` (Anthropic-compatible shape) | `AppSettings.llm_base_url` (default `https://api.minimax.io/anthropic`) |
| Telegram delivery | `application.ports.notifications` | `adapters.outbound.notifications.telegram.TelegramBotApiClient` | hardcoded `https://api.telegram.org` |
| Transcription | transcription port | `adapters.outbound.transcription.openai_compatible.OpenAICompatibleTranscriptionProvider` | `AppSettings.transcription_base_url` |
| TTS | TTS port | `adapters.outbound.tts.minimax.MiniMaxTTSProvider` | `AppSettings.tts_base_url` (default `https://api.minimax.io`) |
| Calendar / notifications tools | `application.ports.calendar`, `application.ports.notifications` | `LocalCalendarTool`, `LocalNotificationTool` | none — local, no egress |

Secrets enter only at the composition root today: `AppSettings.from_env()`
(`infrastructure/config.py`) reads `LLM_API_KEY`, `TELEGRAM_BOT_TOKEN`,
`TRANSCRIPTION_API_KEY`, and `TTS_API_KEY` into `repr=False` fields, and
`infrastructure/bootstrap.py`, `infrastructure/http.py`, and
`infrastructure/worker.py` pass them into adapter constructors. Domain and
application layers never see credentials.

Scoping follows the audit's supplemental tenant-isolation note: this is a
single-operator alpha with a loopback admin surface and exactly one public
route (`POST /webhooks/telegram`), not multi-tenant SaaS. The design therefore
optimizes for one host, one operator, and one provider set — not for running
untrusted third-party tool code. The immediate risks are secret exfiltration
to a non-provider host and unbounded egress from a compromised or misdirected
adapter, not arbitrary tenant code execution.

## Decision

Use a two-layer design: an application-enforced outbound allowlist plus a
single locked-down container as the isolation boundary.

### Layer A — Outbound network allowlist (adapter-level, in code)

Every adapter that opens a socket validates its target URL against a
deny-by-default, fail-closed allowlist before issuing a request. The allowlist
is a small module under `adapters/outbound` consulted by
`MiniMaxLLMProvider`, `TelegramBotApiClient`,
`OpenAICompatibleTranscriptionProvider`, and `MiniMaxTTSProvider`. Matching is
on the exact scheme + hostname (no wildcards, no subdomain globbing); a
non-allowlisted target raises a domain-owned error before any connection is
opened.

Options considered:

- **Reverse-proxy / host-firewall egress rules.** Rejected as the primary
  mechanism. Phase 02 already established the same lesson inbound: "the
  public-edge proxy policy is operational configuration and must be verified on
  every deployment; application tests cannot prove the deployed route table."
  An egress proxy would have the same property, add a runtime dependency the
  build-from-scratch posture (`docs/architecture/build-vs-frameworks.md`)
  avoids, and cannot be covered by the deterministic test suite. It remains
  acceptable as optional defense in depth at deployment time.
- **Adapter-level URL allowlist enforcement (chosen).** Plain Python, no new
  dependencies, unit-testable with code assertions, and enforced exactly where
  secrets already enter — the adapter boundary. Fits the repo's fail-closed
  convention (`TELEGRAM_ALLOWED_USER_IDS` empty denies every actor).
- **Per-request LLM-controlled URLs.** Not applicable and explicitly rejected:
  no component may take an egress target from model output; adapter base URLs
  come only from server settings.

### Layer B — Process isolation boundary (single locked-down container)

Run the runtime (HTTP app plus the reminder/delivery worker) as one container
image per the roadmap, hardened for a single operator: non-root user,
read-only root filesystem, dropped Linux capabilities, no new privileges, and
an attached egress policy that mirrors the Layer A allowlist. Secrets are
passed to the container environment only; they are never baked into the image.

Options considered:

- **Single locked-down container (chosen).** Aligns with the roadmap's days
  8–14 wording ("containerize tool/provider execution"), gives real
  host/network isolation for both the app and the worker, and stays one
  deployable unit — consistent with ADR-001's modular monolith.
- **Separate worker process with stdlib IPC.** Rejected as the boundary: a
  same-host subprocess with secrets in its environment provides no network or
  filesystem isolation and complicates the durable-delivery lease/heartbeat
  contract proven in phases 3–4. It may later complement the container if
  provider execution needs a smaller secret footprint.
- **Per-tool container or VM sandbox.** Rejected as disproportionate. The
  assistant executes no untrusted or model-generated code; the tool catalog is
  fixed and local except the four provider clients above. Per-tool isolation
  buys little against the actual threat and multiplies operational cost for a
  single-operator deployment.

## Secrets and Allowlist Configuration

Secrets continue to be injected only at the adapter boundary:

1. `AppSettings.from_env()` remains the single entry point for credentials.
2. The composition root (`bootstrap.py`, `http.py`, `worker.py`) constructs
   adapters with those values; domain, application, DTO, and trace layers
   never receive them.
3. `repr=False` on secret fields and the centralized trace sanitizer remain
   unchanged; the container image and its build context contain no secrets.
4. Inside the container, secrets arrive only through environment variables at
   start time.

The allowlist follows the `AppSettings` env-var convention:

- `EGRESS_ALLOWED_HOSTS`: comma-separated exact hostnames. Default is derived
  from the configured `llm_base_url`, `transcription_base_url`, and
  `tts_base_url` hosts plus `api.telegram.org` when a Telegram bot token is
  configured — an explicit override always wins.
- Fail closed: when any network-capable provider is enabled and the effective
  allowlist does not cover its configured host, `AppSettings.__post_init__`
  raises at startup, the same way invalid `REMINDER_WORKER_*` or timezone
  values do today.
- Local-only adapters (`LocalCalendarTool`, `LocalNotificationTool`) require
  no entries.
- Auditing: at startup the runtime logs the effective allowlist as hostnames
  only (never URLs carrying credentials, never secret values). A change to
  the allowlist is a config change reviewed like any other; the public-artifact
  secret scanner continues to gate releases.

## Consequences

What changes in the future implementation phase:

- New allowlist module under `adapters/outbound` and its wiring into the four
  network adapters.
- New `AppSettings` fields (`egress_allowed_hosts`) with fail-closed
  validation, plus `.env.example` documentation.
- A container image (Dockerfile and compose/runbook update) with the hardening
  listed above; the hardened-local-deployment runbook gains an egress
  verification section.
- Deterministic tests for allowlist enforcement and startup validation.

What stays:

- Hexagonal boundaries (ADR-003): no layer moves; enforcement lives in
  adapters and the composition root.
- One deployable unit (ADR-001): the container packages the monolith, not
  per-module services.
- Secret injection at adapter boundaries, trace sanitization, and the
  loopback/single-route inbound posture from phase 02.

Operational cost: one image build added to the release checklist; allowlist
review when a provider or base URL changes; container runtime required for the
hardened deployment profile (local in-process development remains supported
with the same code-level allowlist).

Explicit non-goals:

- No multi-tenant isolation claims; the alpha remains single-operator per the
  audit's supplemental note.
- No sandbox for untrusted or model-generated code execution.
- No general availability authorization; this design closes part of GAP #6
  but GA still requires the audit's full gate.

## Acceptance Criteria

The future implementation phase must satisfy, with deterministic probes:

| Criterion | Probe |
|---|---|
| Every network adapter fails closed on a non-allowlisted host. | Unit tests assert each of the four adapters raises before opening a connection when its target host is absent from the allowlist. |
| Startup is fail-closed. | A test constructs `AppSettings` with an enabled provider whose host is not covered and asserts a validation error. |
| Local tools need no egress. | `LocalCalendarTool` and `LocalNotificationTool` tests pass with an empty allowlist. |
| No HTTP client construction outside the adapter boundary. | `rg -n "urllib_request|httpx|requests\." src/personal_assistant/domain src/personal_assistant/application` returns no matches. |
| Allowlist audit output carries no secrets. | A test asserts the startup allowlist log record contains hostnames only and no configured credential substrings; the public-artifact secret scanner passes. |
| Container egress is restricted. | A deployment smoke: from inside the container, a request to a non-allowlisted host fails and a request to each enabled provider host is reachable; the container runs non-root with a read-only root filesystem. |
| Egress behavior is covered by the deterministic suite. | `uv run pytest -q` passes with external providers faked/disabled, no live network required. |
