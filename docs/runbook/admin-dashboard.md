# Admin Dashboard Runbook

The admin dashboard is a local, read-only view over the composed assistant
runtime. It is intended for local debugging of tenant-scoped state, not for
production operations.

## Start Locally

Install the optional API dependencies:

```bash
python -m pip install -e '.[api,test]'
```

Start FastAPI on loopback. `ADMIN_TOKEN` is optional; when set, every admin
request must include it as `Authorization: Bearer <token>` or `X-Admin-Token:
<token>`. Keep it in the local process environment or an ignored `.env` file:

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

The active boundary is loopback-only client validation, optional admin token
validation, and tenant-scoped reads. Do not expose the admin app through ngrok,
a reverse proxy, or a public interface.

Open the HTML dashboard:

```text
http://127.0.0.1:8000/admin?tenant_id=tenant-local&principal_id=user-local&limit=50
```

Query parameters:

| Parameter | Default | Notes |
|---|---|---|
| `tenant_id` | `ASSISTANT_TENANT_ID` / `personal` | Selects the tenant to inspect. |
| `principal_id` | `local-admin` | Display principal for the local admin boundary. |
| `limit` | `50` | Clamped to `1..200` for list endpoints and dashboard tables. |

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

```bash
curl -sS \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  'http://127.0.0.1:8000/admin/snapshot?tenant_id=tenant-local&principal_id=user-local&limit=25' \
  | python3 -m json.tool
```

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

- The admin app accepts only loopback clients: `127.0.0.0/8`, `::1`, and
  `localhost`.
- If `ADMIN_TOKEN` is configured, requests without the matching Bearer token or
  `X-Admin-Token` header are denied.
- Admin reads are tenant-scoped by the selected `tenant_id`; cross-tenant data
  must not appear in a snapshot.
- The dashboard is read-only. Approve or reject runtime approvals through the
  runtime API (`/v1/runtime/approvals/...`) or the Telegram command flow, not
  through admin endpoints.
- Treat trace input/output summaries, memory previews, outbox payloads, and
  event data as potentially sensitive. Do not paste screenshots or JSON dumps
  into public issues.
- `ADMIN_TOKEN`, bot tokens, webhook secrets, provider API keys, database URLs,
  and OAuth credentials must stay out of git and out of traces.
- This is not a production trace dashboard. Add real authentication,
  authorization, audit logging, redaction, transport security, and deployment
  hardening before exposing it outside a local developer machine.
