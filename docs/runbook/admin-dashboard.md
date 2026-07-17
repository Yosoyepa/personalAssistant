# Admin Dashboard Runbook

The admin dashboard is a local, read-only view over the composed assistant
runtime. It is intended for local debugging of tenant-scoped state, not for
production operations.

## Start Locally

Install the optional API dependencies:

```bash
python -m pip install -e '.[api,test]'
```

Start FastAPI on loopback. `ADMIN_TOKEN` is required: every admin request must
include it only as `Authorization: Bearer <ADMIN_TOKEN>`. Keep it in an ignored
local `.env` file or a secret manager; `X-Admin-Token` is not accepted.

```bash
PYTHONPATH=src python3 -m uvicorn personal_assistant.infrastructure.http:app \
  --host 127.0.0.1 \
  --port 8000
```

The active boundary is loopback-only client validation, required bearer-token
validation, and server-fixed tenant-scoped reads. Do not expose the admin app
through a tunnel, reverse proxy, or public interface.

Open the HTML dashboard:

```text
http://127.0.0.1:8000/admin
```

The optional `limit` parameter is clamped to `1..200` for list endpoints and
dashboard tables. Legacy `tenant_id` and `principal_id` query parameters are
ignored: they cannot filter, impersonate, or otherwise change authority.

## Available Endpoints

All routes are `GET`, local-only, and read-only:

| Route | Purpose |
|---|---|
| `/admin` | HTML dashboard with health, agenda, reminders, errors, approvals, traces, outbox, scheduler, events, states, and memory sections. |
| `/admin/snapshot` | Full JSON snapshot containing every dashboard section. |
| `/admin/health` | Health summary and attention counters. |
| `/admin/agenda` | Local calendar events ordered by upcoming/past status. |
| `/admin/reminders` | Scheduled reminder jobs enriched with calendar event context. |
| `/admin/errors` | Normalized error rows from trace, workflow, and outbox sources with filters for category, run, event type, and source. |
| `/admin/approvals` | Workflow states waiting for approval plus `approval.requested` trace events. |
| `/admin/traces` | Recent trace events, counts by event type, and run count. |
| `/admin/outbox` | Tenant outbox messages, dispatch status, attempts, claims, and event payloads. |
| `/admin/scheduler` | Scheduled reminder jobs, due/sent/pending counts, recipients, and body previews. |
| `/admin/events` | Tenant event-store rows with type, source, subject, correlation, and data. |
| `/admin/states` | Durable-lite workflow states and status counts. |
| `/admin/memory` | Tenant memory records with kind, confirmation status, source, and text preview. |

Example JSON check:

```powershell
# Read from ignored .env into this PowerShell process; do not print the value.
$adminLines = @(Get-Content .env | Where-Object {
  $_ -match '^ADMIN_TOKEN="[^"]+"$'
})
if ($adminLines.Count -ne 1) { throw 'Expected exactly one non-empty ADMIN_TOKEN.' }
$adminToken = ([regex]::Match(
  $adminLines[0], '^ADMIN_TOKEN="(?<value>[^"]+)"$')).Groups['value'].Value
$headers = @{ Authorization = "Bearer $adminToken" }
Invoke-RestMethod -Method Get `
  -Uri 'http://127.0.0.1:8000/admin/snapshot?limit=25' -Headers $headers |
  ConvertTo-Json -Depth 10
```

After the local check, run `Remove-Variable adminToken, headers` in that
PowerShell session.

## Signals Shown

- Agenda: `/admin/agenda` and the HTML dashboard show local calendar events,
  upcoming/past status, today count, and the next event.
- Reminders: `/admin/reminders` shows due, sent, pending, notification time,
  channel, recipient, body preview, idempotency key, and linked calendar title
  when available.
- Approvals: pending approval pressure appears as `health.attention.pending_approvals`;
  approval details come from waiting workflow states and `approval.requested`
  traces.
- Outbox: pending, claimed, published, and failed dispatch status is counted and
  each message shows attempts, claim owner, next attempt, idempotency key, and
  event data.
- Errors: `/admin/errors` normalizes trace errors, failed workflow states, and
  failed outbox records. Trace errors are categorized as `audio`, `llm`, `tool`,
  `workflow`, or `unknown`.
- Traces: trace counts include `agent.started`, `context.selected`,
  `llm.called`, `tool.called`, `guardrail.checked`, `approval.requested`,
  `agent.completed`, and `agent.failed`.

## Security Limits

- The admin app accepts only numeric loopback peers: `127.0.0.0/8` and `::1`.
  `localhost` may appear in a human-facing URL, but it is not a separately
  accepted peer identity.
- Requests without the matching `Authorization: Bearer <ADMIN_TOKEN>` header
  are denied, even from loopback. `X-Admin-Token` is not an alternative.
- Server authority is fixed by `ASSISTANT_TENANT_ID`,
  `LOCAL_AUTH_PRINCIPAL_ID`, and `LOCAL_AUTH_PERMISSION_TIER`. Identity headers
  and `tenant_id`/`principal_id` query parameters cannot impersonate it.
- The dashboard is read-only. Approve or reject runtime approvals through the
  runtime API (`/v1/runtime/approvals/...`) or the Telegram command flow, not
  through admin endpoints.
- Treat trace input/output summaries, memory previews, outbox payloads, and
  event data as potentially sensitive. Do not paste screenshots or JSON dumps
  into public issues.
- `ADMIN_TOKEN`, bot tokens, webhook secrets, provider API keys, database URLs,
  and OAuth credentials must stay out of git and out of traces.
- This is not a production trace dashboard. Never expose it outside loopback;
  the public HTTPS edge must allow only `POST /webhooks/telegram`. See
  `docs/runbook/hardened-local-deployment.md` for the exact proxy allowlist and
  secret-rotation procedure.
