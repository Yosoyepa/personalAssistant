-- Initial durable schema for the alpha persistence adapters.
--
-- Every statement is additive and idempotent so databases created by the
-- former ensure_schema() path can be adopted without rewriting or deleting
-- rows. Transaction ownership belongs to the migration runner.

CREATE TABLE IF NOT EXISTS assistant_events (
    tenant_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    source TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    fingerprint TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, event_id)
);

CREATE INDEX IF NOT EXISTS assistant_events_tenant_time_idx
ON assistant_events (tenant_id, occurred_at);

CREATE TABLE IF NOT EXISTS assistant_outbox (
    tenant_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    message_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    dispatch_status TEXT NOT NULL,
    claim_token TEXT,
    claim_owner TEXT,
    claimed_until TIMESTAMPTZ,
    next_attempt_at TIMESTAMPTZ,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL,
    published_at TIMESTAMPTZ,
    event_payload JSONB NOT NULL,
    fingerprint TEXT NOT NULL,
    payload JSONB NOT NULL,
    PRIMARY KEY (tenant_id, idempotency_key),
    UNIQUE (tenant_id, message_id)
);

CREATE INDEX IF NOT EXISTS assistant_outbox_claim_idx
ON assistant_outbox (
    tenant_id,
    dispatch_status,
    claimed_until,
    next_attempt_at,
    created_at
);

CREATE TABLE IF NOT EXISTS assistant_workflow_states (
    tenant_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    workflow_id TEXT NOT NULL,
    workflow_type TEXT NOT NULL,
    status TEXT NOT NULL,
    step TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    payload_fingerprint TEXT,
    fingerprint TEXT NOT NULL,
    payload JSONB NOT NULL,
    PRIMARY KEY (tenant_id, idempotency_key),
    UNIQUE (tenant_id, workflow_id)
);

-- The earliest alpha schema did not contain this nullable replay field.
ALTER TABLE assistant_workflow_states
ADD COLUMN IF NOT EXISTS payload_fingerprint TEXT;

CREATE INDEX IF NOT EXISTS assistant_workflow_states_status_idx
ON assistant_workflow_states (tenant_id, status, updated_at);

CREATE TABLE IF NOT EXISTS assistant_approvals (
    tenant_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    approval_id TEXT NOT NULL,
    action TEXT NOT NULL,
    resource TEXT NOT NULL,
    tier TEXT NOT NULL,
    workflow_kind TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ,
    fingerprint TEXT NOT NULL,
    payload JSONB NOT NULL,
    PRIMARY KEY (tenant_id, approval_id),
    UNIQUE (tenant_id, principal_id, workflow_kind, idempotency_key)
);

CREATE INDEX IF NOT EXISTS assistant_approvals_pending_idx
ON assistant_approvals (tenant_id, principal_id, status, created_at);

CREATE TABLE IF NOT EXISTS assistant_calendar_events (
    tenant_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    event_id TEXT NOT NULL,
    title TEXT NOT NULL,
    starts_at TIMESTAMPTZ NOT NULL,
    request_fingerprint TEXT NOT NULL,
    request_payload JSONB NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, idempotency_key),
    UNIQUE (tenant_id, event_id)
);

CREATE INDEX IF NOT EXISTS assistant_calendar_events_starts_idx
ON assistant_calendar_events (tenant_id, starts_at);

CREATE TABLE IF NOT EXISTS assistant_scheduled_reminders (
    tenant_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    reminder_id TEXT NOT NULL,
    calendar_event_id TEXT NOT NULL,
    notify_at TIMESTAMPTZ NOT NULL,
    channel TEXT NOT NULL,
    recipient TEXT NOT NULL,
    sent BOOLEAN NOT NULL DEFAULT false,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, idempotency_key),
    UNIQUE (tenant_id, reminder_id)
);

CREATE INDEX IF NOT EXISTS assistant_scheduled_reminders_due_idx
ON assistant_scheduled_reminders (tenant_id, sent, notify_at, reminder_id);

CREATE TABLE IF NOT EXISTS assistant_memory_records (
    tenant_id TEXT NOT NULL,
    user_id TEXT,
    memory_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    source TEXT NOT NULL,
    confirmed BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL,
    fingerprint TEXT NOT NULL,
    payload JSONB NOT NULL,
    PRIMARY KEY (tenant_id, memory_id)
);

CREATE INDEX IF NOT EXISTS assistant_memory_records_lookup_idx
ON assistant_memory_records (
    tenant_id,
    user_id,
    kind,
    confirmed,
    created_at
);

CREATE TABLE IF NOT EXISTS assistant_trace_events (
    tenant_id TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    parent_event_id TEXT,
    fingerprint TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, trace_id)
);

CREATE INDEX IF NOT EXISTS assistant_trace_events_run_idx
ON assistant_trace_events (tenant_id, run_id, timestamp);

CREATE INDEX IF NOT EXISTS assistant_trace_events_tenant_idx
ON assistant_trace_events (tenant_id, timestamp);
