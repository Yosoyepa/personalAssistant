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
PYTHONPATH=src python3 -B -m unittest discover -s tests
PYTHONPATH=src python3 -B -m compileall src tests
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

The runtime API accepts trusted local HTTP headers and runtime-shaped JSON. It
does not accept raw Telegram Update JSON.

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

```bash
curl -sS -X POST http://127.0.0.1:8000/v1/runtime/reminders \
  -H "Content-Type: application/json" \
  -H "X-Principal-Id: user-local" \
  -H "X-Tenant-Id: tenant-local" \
  -H "X-Permission-Tier: P5" \
  -d '{
    "message_id": "telegram-message-1",
    "conversation_id": "telegram-chat-1",
    "text": "recuerdame clase el martes a las 5",
    "channel": "telegram",
    "recipient": "telegram-chat-1",
    "now": "2026-06-20T12:00:00+00:00",
    "timezone": "America/Bogota"
  }' \
  | python3 -m json.tool
```

Pass criteria:

- `status` is `escalated`.
- `approval_required` is `true`.
- `approval.approval_id` starts with `apr_`.
- No tenant or principal is accepted from the JSON body.

Approve the pending action:

```bash
APPROVAL_ID="<approval id from previous response>"

curl -sS -X POST "http://127.0.0.1:8000/v1/runtime/approvals/${APPROVAL_ID}/approve" \
  -H "Content-Type: application/json" \
  -H "X-Principal-Id: user-local" \
  -H "X-Tenant-Id: tenant-local" \
  -H "X-Permission-Tier: P5" \
  -d '{}' \
  | python3 -m json.tool
```

Pass criteria:

- Approval response `status` is `approved`.
- Nested result `status` is `completed`.
- `calendar_event_id` and `reminder_id` are present.
- Re-approving the same approval reuses completed state instead of duplicating
  calendar/event records.

Inspect runtime traces:

```bash
curl -sS http://127.0.0.1:8000/v1/runtime/traces \
  -H "X-Principal-Id: user-local" \
  -H "X-Tenant-Id: tenant-local" \
  -H "X-Permission-Tier: P5" \
  | python3 -m json.tool
```

## Current Local Admin Dashboard

The dashboard/admin surface is documented in
`docs/runbook/admin-dashboard.md`. The admin app is local-only, rejects
non-loopback clients, and is read-only. When `ADMIN_TOKEN` is configured, admin
routes require a matching Bearer token or `X-Admin-Token` header; do not expose
these routes outside loopback.

```bash
export ADMIN_TOKEN="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
)"

PYTHONPATH=src python3 -m uvicorn personal_assistant.infrastructure.http:app \
  --host 127.0.0.1 \
  --port 8000
```

Open:

```text
http://127.0.0.1:8000/admin?tenant_id=tenant-local&principal_id=user-local
```

Useful JSON endpoints:

```text
http://127.0.0.1:8000/admin/snapshot?tenant_id=tenant-local&principal_id=user-local
http://127.0.0.1:8000/admin/health?tenant_id=tenant-local&principal_id=user-local
http://127.0.0.1:8000/admin/approvals?tenant_id=tenant-local&principal_id=user-local
http://127.0.0.1:8000/admin/traces?tenant_id=tenant-local&principal_id=user-local
http://127.0.0.1:8000/admin/outbox?tenant_id=tenant-local&principal_id=user-local
http://127.0.0.1:8000/admin/scheduler?tenant_id=tenant-local&principal_id=user-local
http://127.0.0.1:8000/admin/agenda?tenant_id=tenant-local&principal_id=user-local
http://127.0.0.1:8000/admin/reminders?tenant_id=tenant-local&principal_id=user-local
http://127.0.0.1:8000/admin/errors?tenant_id=tenant-local&principal_id=user-local
http://127.0.0.1:8000/admin/events?tenant_id=tenant-local&principal_id=user-local
http://127.0.0.1:8000/admin/states?tenant_id=tenant-local&principal_id=user-local
http://127.0.0.1:8000/admin/memory?tenant_id=tenant-local&principal_id=user-local
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
| `APP_HOST=127.0.0.1` | Uvicorn shell command | No | Local bind host; consumed by shell command, not `AppSettings`. |
| `APP_PORT=8000` | Uvicorn shell command | No | Local bind port for runtime API or ngrok upstream. |
| `ASSISTANT_TENANT_ID=personal` | Telegram bridge/runtime config | No | Trusted tenant default. Must not come from message text. |
| `ASSISTANT_TIMEZONE=America/Bogota` | Runtime request construction | No | Default timezone for local scheduling. |
| `TELEGRAM_BOT_TOKEN` | Calling Telegram Bot API | Yes | Bot API token generated by BotFather. |
| `TELEGRAM_WEBHOOK_SECRET` | Setting and validating webhook | Yes | Expected `X-Telegram-Bot-Api-Secret-Token` value. |
| `PUBLIC_BASE_URL` | Setting webhook | No | Public HTTPS ngrok/domain base URL. |
| `TELEGRAM_WEBHOOK_URL` | Local shell convenience | No | Public HTTPS URL plus webhook path for `setWebhook`. |
| `TELEGRAM_ALLOWED_USER_IDS` | Future Telegram auth mapping | No | Comma-separated Telegram user IDs allowed in local/dev. |
| `NGROK_AUTHTOKEN` | Configuring ngrok agent | Yes | Used by `ngrok config add-authtoken`; do not commit. |
| `ADMIN_TOKEN` | Local admin hardening | Yes | Optional. When configured, admin routes require a matching Bearer token or `X-Admin-Token` header in addition to loopback access. |
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

Start the current runtime API on `APP_PORT`, then expose it for manual HTTP
testing:

```bash
ngrok http 8000
```

Copy the public HTTPS forwarding URL from the ngrok terminal output. Keep the
ngrok process running while testing. Open the local inspection UI at:

```text
http://localhost:4040
```

ngrok inspection criteria:

- A manual request produces a `POST` request to the configured runtime path.
- Request body is JSON.
- Upstream response is a `2xx`.
- Replaying the request against the local server does not duplicate state when a
  stable idempotency key is used.

Do not point BotFather at `/v1/runtime/reminders`: that endpoint expects trusted
runtime headers and a runtime request body, not raw Telegram Update JSON.

## Telegram Bridge Contract

The Bot API bridge route is:

```text
POST /webhooks/telegram/{TELEGRAM_WEBHOOK_SECRET}
Header: X-Telegram-Bot-Api-Secret-Token: <TELEGRAM_WEBHOOK_SECRET>
Body: Telegram Update JSON
```

Wrapper responsibilities:

- Validate the Telegram secret-token header before parsing the request.
- Map Telegram user/chat to a known `Principal` and tenant from trusted config.
- Reject unknown Telegram users before intent routing.
- Call `normalize_telegram_webhook(payload, tenant_id=trusted_tenant_id)`.
- Route normalized commands through `container.commands.handle(...)` with a
  trusted clock and timezone.
- Derive or pass an idempotency key based on tenant, message ID, and text.
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

After the runtime is running and ngrok is forwarding to it:

```bash
export TELEGRAM_WEBHOOK_SECRET="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32).replace("-", "_")[:64])
PY
)"
export TELEGRAM_WEBHOOK_URL="https://your-ngrok-domain.example/webhooks/telegram/${TELEGRAM_WEBHOOK_SECRET}"

curl -sS -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
  -H "Content-Type: application/json" \
  -d @- <<JSON
{
  "url": "${TELEGRAM_WEBHOOK_URL}",
  "secret_token": "${TELEGRAM_WEBHOOK_SECRET}",
  "allowed_updates": ["message", "edited_message"],
  "drop_pending_updates": true
}
JSON
```

Verification:

```bash
curl -sS "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getMe" \
  | python3 -m json.tool

curl -sS "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getWebhookInfo" \
  | python3 -m json.tool
```

Pass criteria:

- `getMe.ok` is `true`.
- `getWebhookInfo.ok` is `true`.
- `getWebhookInfo.result.url` equals `TELEGRAM_WEBHOOK_URL`.
- `pending_update_count` returns to `0` after test messages are processed.
- `last_error_message` is absent or stale from before this setup.
- ngrok inspection shows Telegram `POST` requests reaching the local route.
- Local traces include the expected lifecycle events and no secrets.

Remove the webhook when the local server or ngrok tunnel stops:

```bash
curl -sS -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/deleteWebhook" \
  -H "Content-Type: application/json" \
  -d '{"drop_pending_updates": true}' \
  | python3 -m json.tool
```

## Troubleshooting

| Symptom | Likely Cause | Check |
|---|---|---|
| `getWebhookInfo.result.url` is empty | Webhook was not set or was deleted | Re-run `setWebhook` and inspect response. |
| `last_error_message` mentions connection refused | Local server stopped or ngrok points at wrong port | Confirm server listens on `APP_PORT`; restart `ngrok http APP_PORT`. |
| Telegram keeps retrying the same update | Webhook route returns non-2xx or times out | Check ngrok `http://localhost:4040` response status and latency. |
| Duplicate reminders appear | Idempotency key is not stable across retries | Verify key includes principal tenant, Telegram message ID, and text. |
| Tenant leakage in local test | Principal mapping uses untrusted data | Ensure `tenant_id` comes only from authenticated channel config. |
| Secret appears in logs | Wrapper logs raw headers or env | Redact `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`, OAuth tokens, and API keys. |

## Release Criteria For Telegram Local MVP

- The required local gate passes.
- A Telegram update normalizes into `NormalizedMessage` without tenant authority.
- Unknown Telegram users are rejected before workflow routing.
- A reminder message without approval escalates and creates no side effect.
- A reminder message with valid approval creates exactly one local calendar event,
  one scheduled reminder, one event-store event, and one outbox event.
- Replayed Telegram requests are idempotent.
- ngrok inspection shows 2xx responses for accepted updates.
- `getWebhookInfo` shows the expected URL and no current delivery error.
- Traces contain lifecycle events and no secrets or raw credential values.
