# Telegram Local Runbook

This runbook explains how to verify the local MVP and connect the Telegram Bot
API bridge to BotFather and ngrok. It does not contain secrets.

## Implementation Boundary

The repository currently provides:

- `personal_assistant.adapters.inbound.api.normalize_telegram_webhook`
- `personal_assistant.infrastructure.bootstrap.build_container`
- `personal_assistant.infrastructure.http:app` with runtime, Telegram webhook,
  and local-only admin endpoints
- `ConversationCommandService` for `/start`, `/help`, `/recordar`, `/agenda`,
  `/pendientes`, `/aprobar`, `/cancelar`, `/status`, and structured LLM intent
  routing when deterministic command rules do not match
- `ReminderWorkflow` with local calendar, scheduler, event store, outbox,
  workflow state, and trace recorder adapters
- `ReminderWorker` for due reminder notification dispatch
- `TelegramNotificationTool` as a P5 dispatcher adapter; the agent runtime does
  not call it directly
- MiniMax LLM and TTS adapters behind application ports
- deterministic tests for tenant isolation, idempotency, permission gates,
  prompt-injection handling, HTTP runtime boundaries, local admin visibility,
  worker dispatch, command routing, and hexagonal boundaries

The repository does not yet provide:

- persistent storage
- production trace dashboard
- external calendar sync

Do not wire direct Telegram delivery into the agent runtime. Per
`agents/personal_assistant/contract.md`, Telegram send side effects belong to a
dispatcher outside the agent-owned run artifact.

## Required Local Gate

Run this before and after any Telegram wrapper change:

```bash
PYTHONPATH=src python3 -B -m pytest -q
PYTHONPATH=src python3 -B -m compileall -q src tests
python3 -m json.tool eval/cases.json >/dev/null
```

Pass criteria:

- All unit tests pass.
- Python compilation succeeds for `src` and `tests`.
- `eval/cases.json` is valid JSON.
- The Telegram normalizer requires `tenant_id` from authenticated channel config.
- User text such as `tenant_id=evil` stays inert and does not become authority.

## Current Local Smoke Test

This verifies the reminder workflow without Telegram, ngrok, or network calls:

```bash
PYTHONPATH=src python3 - <<'PY'
from datetime import UTC, datetime

from personal_assistant.application.dto.reminders import ReminderWorkflowInput
from personal_assistant.application.use_cases.reminders import reminder_idempotency_key
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import ApprovalGrant, PermissionTier
from personal_assistant.infrastructure.bootstrap import build_container

container = build_container()
principal = Principal.for_test(
    principal_id="user-local",
    tenant_id="tenant-local",
    permission_tier=PermissionTier.P5,
)
text = "recuerdame clase el martes a las 5"
key = reminder_idempotency_key(principal.tenant_id, "telegram-message-1", text)
approval = ApprovalGrant.issue(
    principal=principal,
    action="calendar.create_event",
    resource=f"{key}:calendar",
    tier=PermissionTier.P3,
)
result = container.reminder_workflow.run(
    principal,
    ReminderWorkflowInput(
        message_id="telegram-message-1",
        conversation_id="telegram-chat-1",
        recipient="telegram-chat-1",
        text=text,
        now=datetime(2026, 6, 20, 12, tzinfo=UTC),
        timezone="America/Bogota",
        idempotency_key=key,
        approval=approval,
    ),
)
trace_types = [
    event.event_type.value
    for event in container.traces.list_for_tenant(principal.tenant_id)
]
print(result.model_dump(mode="json"))
print(trace_types)
PY
```

Expected output criteria:

- `status` is `completed`.
- `calendar_event_id` and `reminder_id` are not null.
- Trace types include `agent.started`, `guardrail.checked`,
  `context.selected`, `tool.called`, and `agent.completed`.

## Current Runtime API Smoke Test

The runtime API is loopback-only and accepts runtime-shaped JSON; it does not
accept raw Telegram Update JSON. Every `/v1/runtime/*` request requires
`Authorization: Bearer <ADMIN_TOKEN>`. The server fixes tenant, principal, and
permission tier from `ASSISTANT_TENANT_ID`, `LOCAL_AUTH_PRINCIPAL_ID`, and
`LOCAL_AUTH_PERMISSION_TIER`; identity headers and query parameters do not
grant or alter authority.

Install API dependencies:

```bash
python -m pip install -e '.[api,test]'
```

Start the runtime API:

```bash
PYTHONPATH=src python3 -m uvicorn personal_assistant.infrastructure.http:app \
  --host 127.0.0.1 \
  --port 8000
```

In another shell:

```bash
curl -sS http://127.0.0.1:8000/healthz | python3 -m json.tool
curl -sS http://127.0.0.1:8000/readyz | python3 -m json.tool
```

Create a reminder request. This should return `202` with a pending approval and
no calendar side effect yet:

```powershell
# Read from ignored .env into this PowerShell process; do not print the value.
$adminLines = @(Get-Content .env | Where-Object {
  $_ -match '^ADMIN_TOKEN="[^"]+"$'
})
if ($adminLines.Count -ne 1) { throw 'Expected exactly one non-empty ADMIN_TOKEN.' }
$adminToken = ([regex]::Match(
  $adminLines[0], '^ADMIN_TOKEN="(?<value>[^"]+)"$')).Groups['value'].Value
$headers = @{ Authorization = "Bearer $adminToken" }
$body = @{
  message_id = 'telegram-message-1'
  conversation_id = 'telegram-chat-1'
  text = 'recuerdame clase el martes a las 5'
  channel = 'telegram'
  recipient = 'telegram-chat-1'
  now = '2026-06-20T12:00:00+00:00'
  timezone = 'America/Bogota'
} | ConvertTo-Json
Invoke-RestMethod -Method Post `
  -Uri 'http://127.0.0.1:8000/v1/runtime/reminders' `
  -Headers $headers -ContentType 'application/json' -Body $body |
  ConvertTo-Json -Depth 10
```

Pass criteria:

- `status` is `escalated`.
- `approval_required` is `true`.
- `approval.approval_id` starts with `apr_`.
- No tenant or principal is accepted from the JSON body.

Approve the pending action:

```powershell
$approvalId = '<approval id from previous response>'
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/v1/runtime/approvals/$approvalId/approve" `
  -Headers $headers -ContentType 'application/json' -Body '{}' |
  ConvertTo-Json -Depth 10
```

Pass criteria:

- Approval response `status` is `approved`.
- Nested result `status` is `completed`.
- `calendar_event_id` and `reminder_id` are present.
- Re-approving the same approval reuses completed state instead of duplicating
  calendar/event records.

Inspect runtime traces:

```powershell
Invoke-RestMethod -Method Get `
  -Uri 'http://127.0.0.1:8000/v1/runtime/traces' -Headers $headers |
  ConvertTo-Json -Depth 10
```

After the local smoke test, run `Remove-Variable adminToken, headers` in that
PowerShell session.

## Current Local Admin Dashboard

The dashboard/admin surface is documented in
`docs/runbook/admin-dashboard.md`. The admin app is local-only, rejects
non-loopback clients, and is read-only. Admin routes always require
`Authorization: Bearer <ADMIN_TOKEN>`; `X-Admin-Token` is not an alternative.
Do not expose these routes outside loopback.

```bash
PYTHONPATH=src python3 -m uvicorn personal_assistant.infrastructure.http:app \
  --host 127.0.0.1 \
  --port 8000
```

Open:

```text
http://127.0.0.1:8000/admin
```

Useful JSON endpoints:

```text
http://127.0.0.1:8000/admin/snapshot
http://127.0.0.1:8000/admin/health
http://127.0.0.1:8000/admin/approvals
http://127.0.0.1:8000/admin/traces
http://127.0.0.1:8000/admin/outbox
http://127.0.0.1:8000/admin/scheduler
http://127.0.0.1:8000/admin/agenda
http://127.0.0.1:8000/admin/reminders
http://127.0.0.1:8000/admin/errors
http://127.0.0.1:8000/admin/events
http://127.0.0.1:8000/admin/states
http://127.0.0.1:8000/admin/memory
```

Dashboard criteria:

- Pending approval runs show `health.status = needs_attention`.
- Completed runs surface traces, states, events, outbox, scheduler, agenda,
  reminders, and memory for the requested tenant only.
- Errors surface through `/admin/errors`, `agent.failed` traces, failed outbox
  counts, and failed workflow counts.
- Cross-tenant canary text is not visible in another tenant snapshot.

## Environment Variables

`AppSettings.from_env()` currently reads the `ASSISTANT_*`, Telegram, admin, and
worker variables below.

| Variable | Required When | Secret | Purpose |
|---|---|---:|---|
| `APP_ENV_FILE=.env` | Local runtime startup | No | Optional env file loaded by `AppSettings`; use empty value to disable file loading in tests. |
| `PYTHONPATH=src` | Local commands without editable install | No | Makes the package importable. |
| `ASSISTANT_TENANT_ID=personal` | Telegram bridge/runtime config | No | Trusted tenant default. Must not come from message text. |
| `ASSISTANT_TIMEZONE=America/Bogota` | Runtime request construction | No | Default timezone for local scheduling. |
| `ASSISTANT_REPLY_LOCALE=es` | Runtime replies | No | Locale catalog used for user-facing command and workflow copy. |
| `TELEGRAM_BOT_TOKEN` | Calling Telegram Bot API | Yes | Bot API token generated by BotFather. |
| `TELEGRAM_WEBHOOK_SECRET` | Setting and validating webhook | Yes | Expected `X-Telegram-Bot-Api-Secret-Token` value. |
| `PUBLIC_BASE_URL` | Setting webhook | No | Public HTTPS ngrok/domain base URL. |
| `TELEGRAM_WEBHOOK_URL` | Local shell convenience | No | Public HTTPS URL plus webhook path for `setWebhook`. |
| `TELEGRAM_ALLOWED_USER_IDS` | Accepting Telegram updates | No | Comma-separated Telegram user IDs allowed in local/dev. Empty means deny everyone. |
| `NGROK_AUTHTOKEN` | Configuring ngrok agent | Yes | Used by `ngrok config add-authtoken`; do not commit. |
| `ADMIN_TOKEN` | Local runtime/admin access | Yes | Required for `/v1/runtime/*`, `/admin`, and `/admin/*`; send only as `Authorization: Bearer <ADMIN_TOKEN>` over loopback. |
| `REMINDER_WORKER_ENABLED=true` | Reminder notification dispatch | No | Starts the FastAPI background worker that sends due reminder notifications. |
| `REMINDER_WORKER_INTERVAL_SECONDS=15` | Worker loop config | No | Minimum interval is clamped to 1 second. |
| `REMINDER_MINUTES_BEFORE=30` | Event reminder scheduling | No | Minutes before a calendar event when the bot should notify. Relative reminders like `recuérdame en 2 minutos...` notify at the requested time instead. |
| `LLM_PROVIDER` | Bounded LLM extraction | No | Use `minimax` for MiniMax Token Plan, `anthropic_compatible` for AeroLink/Claude-compatible gateways, or `disabled` for deterministic-only behavior. |
| `MINIMAX_API_KEY` | MiniMax Token Plan LLM extraction | Yes | MiniMax subscription key. This is distinct from MiniMax pay-as-you-go API keys. |
| `MINIMAX_BASE_URL` | MiniMax Token Plan LLM extraction | No | Defaults to `https://api.minimax.io/anthropic` for Anthropic-compatible Messages API. |
| `MINIMAX_MODEL` | MiniMax Token Plan LLM extraction | No | Defaults to `MiniMax-M3`. |
| `LLM_API_KEY` | Generic bounded LLM extraction | Yes | Provider key. `MINIMAX_API_KEY`, `AEROLINK_API_KEY`, `ANTHROPIC_API_KEY`, and `ANTHROPIC_AUTH_TOKEN` are also read as fallbacks. |
| `LLM_BASE_URL` | Generic bounded LLM extraction | No | Provider base URL. `MINIMAX_BASE_URL`, `AEROLINK_BASE_URL`, and `ANTHROPIC_BASE_URL` are also accepted. |
| `LLM_MODEL` | Generic bounded LLM extraction | No | Provider model id. `MINIMAX_MODEL`, `AEROLINK_MODEL`, and `ANTHROPIC_MODEL` are also accepted. |
| `LLM_AUTH_HEADER` | Bounded LLM extraction | No | `x-api-key` by default; use `authorization` for bearer-token compatible proxies. |
| `TRANSCRIPTION_PROVIDER` | Telegram voice messages | No | Use `openai_compatible` for Groq or another provider exposing `/v1/audio/transcriptions`; otherwise leave `disabled`. |
| `TRANSCRIPTION_API_KEY` | Telegram voice messages | Yes | Speech-to-text provider key. `GROQ_API_KEY` and `AEROLINK_API_KEY` are also read as fallbacks. |
| `TRANSCRIPTION_BASE_URL` | Telegram voice messages | No | For Groq use `https://api.groq.com/openai`. Defaults to `AEROLINK_BASE_URL` if set. |
| `TRANSCRIPTION_MODEL` | Telegram voice messages | No | Speech-to-text model id, required when transcription is enabled. |
| `TTS_PROVIDER` | Telegram audio replies | No | Use `minimax` to synthesize Telegram reply audio from MiniMax T2A; leave `disabled` for text-only replies. |
| `TTS_API_KEY` | Telegram audio replies | Yes | TTS provider key. `MINIMAX_API_KEY` is read as a fallback for MiniMax Token Plan. |
| `TTS_BASE_URL` | Telegram audio replies | No | Defaults to `https://api.minimax.io`. |
| `TTS_MODEL` | Telegram audio replies | No | Defaults to `speech-2.8-turbo`. |
| `TTS_VOICE_ID` | Telegram audio replies | No | Defaults to `male-qn-qingse`; choose another MiniMax system voice if preferred. |
| `TTS_AUDIO_FORMAT` | Telegram audio replies | No | `mp3`, `wav`, or `flac`. Telegram replies use `sendAudio`; default `mp3`. |
| `TTS_LANGUAGE_BOOST` | Telegram audio replies | No | Defaults to `Spanish` for Spanish assistant replies. |
| `TTS_MAX_REPLY_CHARACTERS` | Telegram audio replies | No | Maximum text length to synthesize. Defaults to `280` to control cost and latency. |
| `TELEGRAM_AUDIO_REPLY_MODE` | Telegram audio replies | No | `disabled`, `voice_only`, or `always`. Use `voice_only` for MVP voice UX. |

Local `.env` files are ignored by git. Do not add a committed example with real
tokens or tenant/customer data.

MiniMax Token Plan defaults above are based on the official international
MiniMax docs for Token Plan Quick Start, Token Plan Other Tools, Anthropic API,
OpenAI-compatible API, and T2A HTTP API:
<https://platform.minimax.io/docs/token-plan/quickstart>,
<https://platform.minimax.io/docs/token-plan/other-tools>,
<https://platform.minimax.io/docs/api-reference/text-chat-anthropic>,
<https://platform.minimax.io/docs/api-reference/text-openai-api>, and
<https://platform.minimax.io/docs/api-reference/speech-t2a-http>.

## BotFather Setup

Source: <https://core.telegram.org/bots/features#botfather>

1. Open `@BotFather` in Telegram.
2. Send `/newbot`.
3. Choose a display name.
4. Choose a username ending in `bot`.
5. Store the generated token outside git as `TELEGRAM_BOT_TOKEN`.
6. Use `/setdescription`, `/setabouttext`, and `/setcommands` for local UX.
7. For this MVP, prefer private-chat testing. If the bot should not be added to
   groups during local development, use BotFather settings to disable group
   joins.

Recommended command list:

```text
start - Start the assistant
help - Show supported local MVP actions
recordar - Create a reminder with approval
agenda - Show local calendar events
pendientes - Show pending approvals
aprobar - Approve a pending action
cancelar - Cancel a pending approval
status - Show local assistant status
```

Security criteria:

- Never paste the token into docs, issues, traces, screenshots, or shell history
  shared with others.
- If the token leaks, rotate it with BotFather before continuing.
- The bot token authorizes Bot API calls; possession is enough to control the
  bot.

## ngrok Setup

Sources:

- <https://ngrok.com/docs/getting-started>
- <https://ngrok.com/docs/agent/web-inspection-interface>

Install and authenticate the ngrok agent:

```bash
ngrok help
ngrok config add-authtoken "$NGROK_AUTHTOKEN"
```

Start the runtime with explicit `--host 127.0.0.1 --port 8000`, then configure ngrok (or another
HTTPS edge) to forward **only** `POST /webhooks/telegram`. Do not use an
unrestricted tunnel to port 8000: that would publish health, runtime, and admin
routes. The exact allowlist and verification checklist are in
`docs/runbook/hardened-local-deployment.md`.

```bash
<configure an HTTPS edge with only POST /webhooks/telegram allowed>
```

Copy the public HTTPS forwarding URL from the ngrok terminal output. Keep the
ngrok process running while testing. Open the local inspection UI at:

```text
http://localhost:4040
```

ngrok inspection criteria:

- A Telegram request produces a `POST /webhooks/telegram` request only.
- Request body is JSON.
- Upstream response is a `2xx`.
- Replaying the request against the local server does not duplicate state when a
  stable idempotency key is used.

Do not point BotFather at `/v1/runtime/reminders`: that endpoint expects trusted
loopback admin authentication and a runtime request body, not raw Telegram
Update JSON.

## Telegram Bridge Contract

The Bot API bridge route is:

```text
POST /webhooks/telegram
Header: X-Telegram-Bot-Api-Secret-Token: <TELEGRAM_WEBHOOK_SECRET>
Body: Telegram Update JSON
```

Wrapper responsibilities:

- Require the Telegram secret-token header and validate it with
  `secrets.compare_digest` before update normalization.
- Derive the actor only from Telegram user `from.id` data. Never use `chat.id`
  as an actor fallback.
- Reject updates without a verifiable actor and reject actors absent from the
  configured allowlist. An empty allowlist denies everyone.
- Perform every denial before transcription, command routing, approvals,
  workflow state, domain events, outbox writes, Telegram replies, or TTS.
- Map the verified Telegram user to a known `Principal` and tenant from trusted
  config.
- Call `normalize_telegram_webhook(payload, tenant_id=trusted_tenant_id)`.
- Route normalized commands through `container.commands.handle(...)` with a
  trusted clock and timezone.
- Preserve Telegram's message reference as `message_id`, map `update_id` (or the callback event id when an update id is unavailable) to explicit `source_event_id`, and derive the v2 idempotency key from trusted tenant/principal/channel/conversation plus that source event. Text belongs only in the independent payload fingerprint.
- Return a fast `2xx` after accepting the update.
- Send replies through the infrastructure `NotificationPort` with a runtime P5
  approval when `TELEGRAM_BOT_TOKEN` is configured; without a token, return the
  reply draft in JSON for local testing.

The wrapper must not:

- accept `tenant_id` from message text
- log bot tokens, webhook secrets, OAuth tokens, or raw long document bodies
- invoke `mcp.*` or `a2a.*` tools in the MVP path
- send email, SMS, third-party messages, or financial requests
- execute P3+ actions without an approval record

## Set Telegram Webhook

Source: <https://core.telegram.org/bots/api#setwebhook>

After the loopback runtime and the restricted HTTPS edge are running, save the
following helper as an ignored local file, for example
`telegram-webhook.local.ps1`. It reads `.env` into process memory and prints
only sanitized status/metadata. The Bot API requires the bot token in its
request URL, but the helper builds that URL only inside the process: it never
appears in argv, a process listing, terminal output, or the public webhook URL.
Do not add this local helper or `.env` to git.

```powershell
param(
  [Parameter(Mandatory)]
  [ValidateSet('set', 'info', 'delete')]
  [string]$Action,
  [string]$EnvFile = '.env'
)

$values = @{}
Get-Content -LiteralPath $EnvFile -ErrorAction Stop | ForEach-Object {
  if ($_ -match '^\s*([A-Z0-9_]+)="?([^"#]*)"?\s*$') {
    $values[$matches[1]] = $matches[2]
  }
}
foreach ($name in 'TELEGRAM_BOT_TOKEN') {
  if ([string]::IsNullOrWhiteSpace($values[$name])) {
    throw "Missing setting: $name"
  }
}

$endpoint = 'https://api.telegram.org/bot' + $values.TELEGRAM_BOT_TOKEN
if ($Action -eq 'set') {
  foreach ($name in 'TELEGRAM_WEBHOOK_SECRET', 'PUBLIC_BASE_URL') {
    if ([string]::IsNullOrWhiteSpace($values[$name])) {
      throw "Missing setting: $name"
    }
  }
  $publicBase = $null
  if (-not [Uri]::TryCreate(
      $values.PUBLIC_BASE_URL,
      [UriKind]::Absolute,
      [ref]$publicBase)) {
    throw 'PUBLIC_BASE_URL must be an absolute HTTPS origin.'
  }
  if ($publicBase.Scheme -ne [Uri]::UriSchemeHttps -or
      $publicBase.UserInfo -or
      $publicBase.Query -or
      $publicBase.Fragment -or
      ($publicBase.AbsolutePath -ne '/' -and $publicBase.AbsolutePath -ne '')) {
    $message = 'PUBLIC_BASE_URL must be an HTTPS origin without userinfo, '
    throw ($message + 'query, fragment, or path.')
  }
  $origin = $publicBase.GetLeftPart([UriPartial]::Authority)
  $webhookUrl = $origin + '/webhooks/telegram'
  $body = @{
    url = $webhookUrl
    secret_token = $values.TELEGRAM_WEBHOOK_SECRET
    allowed_updates = @('message', 'edited_message')
    drop_pending_updates = $true
  } | ConvertTo-Json -Compress
}
try {
  switch ($Action) {
    'set' {
      $response = Invoke-RestMethod -Method Post -Uri ($endpoint + '/setWebhook') `
        -ContentType 'application/json' -Body $body
    }
    'info' {
      $response = Invoke-RestMethod -Method Get `
        -Uri ($endpoint + '/getWebhookInfo')
    }
    'delete' {
      $body = @{ drop_pending_updates = $true } | ConvertTo-Json -Compress
      $response = Invoke-RestMethod -Method Post `
        -Uri ($endpoint + '/deleteWebhook') `
        -ContentType 'application/json' -Body $body
    }
  }
} catch {
  # Do not surface the original error: it can include Telegram's tokenized URL.
  $Error.Clear()
  throw "Telegram $Action request failed."
} finally {
  $endpoint = $null
  $body = $null
  $webhookUrl = $null
  $matches = $null
  if ($null -ne $values) { $values.Clear() }
}
if (-not $response.ok) {
  $Error.Clear()
  throw "Telegram $Action failed."
}

Write-Output "action=$Action ok=true"
if ($Action -eq 'info') {
  $uri = [Uri]$response.result.url
  $metadata = 'webhook_host={0} webhook_path={1} pending_updates={2} ' +
    'has_last_error={3}'
  Write-Output ($metadata -f $uri.Host, $uri.AbsolutePath,
    $response.result.pending_update_count,
    [bool]$response.result.last_error_message)
}
```

On failure, keep only the helper's generic error. Do not print `$Error`, the
original exception, request diagnostics, or a stack trace: any of them could
contain the tokenized Bot API URL.

Run the local helper without credentials in arguments:

```powershell
.\telegram-webhook.local.ps1 -Action set
.\telegram-webhook.local.ps1 -Action info
```

Pass criteria:

- `getWebhookInfo.ok` is `true`.
- The sanitized `webhook_host` and `webhook_path` match the intended public
  HTTPS webhook URL; no secret appears in the URL.
- `pending_update_count` returns to `0` after test messages are processed.
- `last_error_message` is absent or stale from before this setup.
- ngrok inspection shows Telegram `POST` requests reaching the local route.
- Local traces include the expected lifecycle events and no secrets.

Remove the webhook when the local server or restricted HTTPS edge stops:

```powershell
.\telegram-webhook.local.ps1 -Action delete
```

## Troubleshooting

- `getWebhookInfo.result.url` is empty: the webhook was not set or was
  deleted. Re-run the local helper with `-Action set` and inspect its sanitized
  response.
- `last_error_message` mentions connection refused: confirm that the server
  listens on `127.0.0.1:8000`, then restore the edge's exact
  `POST /webhooks/telegram` allowlist. Do not replace it with an unrestricted
  tunnel.
- Telegram retries the same update: the webhook route returned non-2xx or
  timed out. Check the restricted-edge status and latency.
- Duplicate reminders appear: verify that the idempotency key includes
  principal tenant, Telegram message ID, and text.
- Tenant leakage in a local test: ensure `tenant_id` comes only from trusted
  channel configuration.
- A secret appears in logs: redact `TELEGRAM_BOT_TOKEN`,
  `TELEGRAM_WEBHOOK_SECRET`, OAuth tokens, and API keys.

## Release Criteria For Telegram Local MVP

- The required local gate passes.
- A Telegram update normalizes into `NormalizedMessage` without tenant authority.
- Unknown Telegram users are rejected before workflow routing.
- Missing or invalid secret headers, empty allowlists, and updates without a
  Telegram `from.id` are rejected before any state or external work.
- A reminder message without approval escalates and creates no side effect.
- A reminder message with valid approval creates exactly one local calendar event,
  one scheduled reminder, one event-store event, and one outbox event.
- Replayed Telegram requests are idempotent.
- ngrok inspection shows 2xx responses for accepted updates.
- `getWebhookInfo` shows the expected URL and no current delivery error.
- Traces contain lifecycle events and no secrets or raw credential values.
