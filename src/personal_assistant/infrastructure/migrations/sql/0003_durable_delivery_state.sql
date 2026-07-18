-- Add durable delivery state without rewriting or removing legacy payloads.
-- Older binaries continue to use assistant_scheduled_reminders.sent as their
-- only due guard. The trigger therefore keeps sent=false only for pending;
-- sent is a rollback safety guard, not delivery truth.

ALTER TABLE assistant_outbox
    ADD COLUMN IF NOT EXISTS sending_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_error_category TEXT,
    ADD COLUMN IF NOT EXISTS last_error_code TEXT,
    ADD COLUMN IF NOT EXISTS last_error_provider_code INTEGER,
    ADD COLUMN IF NOT EXISTS last_error_at TIMESTAMPTZ;

ALTER TABLE assistant_outbox
    ADD CONSTRAINT assistant_outbox_delivery_status_check
    CHECK (dispatch_status IN (
        'pending', 'claimed', 'sending', 'published', 'failed', 'uncertain'
    )) NOT VALID,
    ADD CONSTRAINT assistant_outbox_error_category_check
    CHECK (last_error_category IS NULL OR last_error_category IN (
        'network', 'rate_limited', 'rejected', 'configuration',
        'internal', 'unknown'
    )) NOT VALID,
    ADD CONSTRAINT assistant_outbox_error_code_check
    CHECK (last_error_code IS NULL OR last_error_code IN (
        'timeout', 'connection_failed', 'rate_limited',
        'provider_unavailable', 'authentication_failed', 'request_rejected',
        'invalid_configuration', 'internal_error', 'unknown'
    )) NOT VALID,
    ADD CONSTRAINT assistant_outbox_error_provider_code_check
    CHECK (
        last_error_provider_code IS NULL
        OR last_error_provider_code BETWEEN 0 AND 9999
    ) NOT VALID,
    ADD CONSTRAINT assistant_outbox_attempts_check
    CHECK (attempts >= 0) NOT VALID,
    ADD CONSTRAINT assistant_outbox_error_shape_check
    CHECK (
        (
            last_error_category IS NULL
            AND last_error_code IS NULL
            AND last_error_provider_code IS NULL
            AND last_error_at IS NULL
        ) OR (
            last_error_category IS NOT NULL
            AND last_error_code IS NOT NULL
            AND last_error_at IS NOT NULL
        )
    ) NOT VALID,
    ADD CONSTRAINT assistant_outbox_state_metadata_check
    CHECK (
        (
            dispatch_status = 'pending'
            AND claim_token IS NULL
            AND claim_owner IS NULL
            AND claimed_until IS NULL
            AND sending_at IS NULL
            AND published_at IS NULL
        ) OR (
            dispatch_status = 'claimed'
            AND claim_token IS NOT NULL
            AND claim_owner IS NOT NULL
            AND claimed_until IS NOT NULL
            AND sending_at IS NULL
            AND published_at IS NULL
        ) OR (
            dispatch_status = 'sending'
            AND claim_token IS NOT NULL
            AND claim_owner IS NOT NULL
            AND claimed_until IS NOT NULL
            AND sending_at IS NOT NULL
            AND attempts > 0
            AND published_at IS NULL
        ) OR (
            dispatch_status = 'published'
            AND claim_token IS NULL
            AND claim_owner IS NULL
            AND claimed_until IS NULL
            AND published_at IS NOT NULL
            AND last_error_category IS NULL
            AND last_error_code IS NULL
            AND last_error_provider_code IS NULL
            AND last_error_at IS NULL
        ) OR (
            dispatch_status IN ('failed', 'uncertain')
            AND claim_token IS NULL
            AND claim_owner IS NULL
            AND claimed_until IS NULL
            AND sending_at IS NOT NULL
            AND attempts > 0
            AND published_at IS NULL
            AND last_error_category IS NOT NULL
            AND last_error_code IS NOT NULL
            AND last_error_at IS NOT NULL
        )
    ) NOT VALID;

ALTER TABLE assistant_outbox
    VALIDATE CONSTRAINT assistant_outbox_delivery_status_check;
ALTER TABLE assistant_outbox
    VALIDATE CONSTRAINT assistant_outbox_error_category_check;
ALTER TABLE assistant_outbox
    VALIDATE CONSTRAINT assistant_outbox_error_code_check;
ALTER TABLE assistant_outbox
    VALIDATE CONSTRAINT assistant_outbox_error_provider_code_check;
ALTER TABLE assistant_outbox
    VALIDATE CONSTRAINT assistant_outbox_attempts_check;
ALTER TABLE assistant_outbox
    VALIDATE CONSTRAINT assistant_outbox_error_shape_check;
ALTER TABLE assistant_outbox
    VALIDATE CONSTRAINT assistant_outbox_state_metadata_check;

ALTER TABLE assistant_scheduled_reminders
    ADD COLUMN IF NOT EXISTS delivery_status TEXT,
    ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS sending_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS published_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_error_category TEXT,
    ADD COLUMN IF NOT EXISTS last_error_code TEXT,
    ADD COLUMN IF NOT EXISTS last_error_provider_code INTEGER,
    ADD COLUMN IF NOT EXISTS last_error_at TIMESTAMPTZ;

UPDATE assistant_scheduled_reminders
SET delivery_status = CASE WHEN sent THEN 'published' ELSE 'pending' END,
    published_at = CASE
        WHEN sent THEN COALESCE(published_at, created_at)
        ELSE published_at
    END
WHERE delivery_status IS NULL;

ALTER TABLE assistant_scheduled_reminders
    ALTER COLUMN delivery_status SET DEFAULT 'pending',
    ALTER COLUMN delivery_status SET NOT NULL,
    ADD CONSTRAINT assistant_scheduled_delivery_status_check
    CHECK (delivery_status IN (
        'pending', 'claimed', 'sending', 'published', 'failed', 'uncertain'
    )) NOT VALID,
    ADD CONSTRAINT assistant_scheduled_attempts_check
    CHECK (attempts >= 0) NOT VALID,
    ADD CONSTRAINT assistant_scheduled_error_category_check
    CHECK (last_error_category IS NULL OR last_error_category IN (
        'network', 'rate_limited', 'rejected', 'configuration',
        'internal', 'unknown'
    )) NOT VALID,
    ADD CONSTRAINT assistant_scheduled_error_code_check
    CHECK (last_error_code IS NULL OR last_error_code IN (
        'timeout', 'connection_failed', 'rate_limited',
        'provider_unavailable', 'authentication_failed', 'request_rejected',
        'invalid_configuration', 'internal_error', 'unknown'
    )) NOT VALID,
    ADD CONSTRAINT assistant_scheduled_error_provider_code_check
    CHECK (
        last_error_provider_code IS NULL
        OR last_error_provider_code BETWEEN 0 AND 9999
    ) NOT VALID,
    ADD CONSTRAINT assistant_scheduled_error_shape_check
    CHECK (
        (
            last_error_category IS NULL
            AND last_error_code IS NULL
            AND last_error_provider_code IS NULL
            AND last_error_at IS NULL
        ) OR (
            last_error_category IS NOT NULL
            AND last_error_code IS NOT NULL
            AND last_error_at IS NOT NULL
        )
    ) NOT VALID,
    ADD CONSTRAINT assistant_scheduled_legacy_sent_check
    CHECK (sent = (delivery_status <> 'pending')) NOT VALID,
    ADD CONSTRAINT assistant_scheduled_state_metadata_check
    CHECK (
        (
            delivery_status = 'pending'
            AND sending_at IS NULL
            AND published_at IS NULL
        ) OR (
            delivery_status = 'claimed'
            AND sending_at IS NULL
            AND published_at IS NULL
        ) OR (
            delivery_status = 'sending'
            AND attempts > 0
            AND sending_at IS NOT NULL
            AND published_at IS NULL
        ) OR (
            delivery_status = 'published'
            AND published_at IS NOT NULL
        ) OR (
            delivery_status IN ('failed', 'uncertain')
            AND attempts > 0
            AND sending_at IS NOT NULL
            AND published_at IS NULL
            AND last_error_category IS NOT NULL
            AND last_error_code IS NOT NULL
            AND last_error_at IS NOT NULL
        )
    ) NOT VALID;

ALTER TABLE assistant_scheduled_reminders
    VALIDATE CONSTRAINT assistant_scheduled_delivery_status_check;
ALTER TABLE assistant_scheduled_reminders
    VALIDATE CONSTRAINT assistant_scheduled_attempts_check;
ALTER TABLE assistant_scheduled_reminders
    VALIDATE CONSTRAINT assistant_scheduled_error_category_check;
ALTER TABLE assistant_scheduled_reminders
    VALIDATE CONSTRAINT assistant_scheduled_error_code_check;
ALTER TABLE assistant_scheduled_reminders
    VALIDATE CONSTRAINT assistant_scheduled_error_provider_code_check;
ALTER TABLE assistant_scheduled_reminders
    VALIDATE CONSTRAINT assistant_scheduled_error_shape_check;
ALTER TABLE assistant_scheduled_reminders
    VALIDATE CONSTRAINT assistant_scheduled_legacy_sent_check;
ALTER TABLE assistant_scheduled_reminders
    VALIDATE CONSTRAINT assistant_scheduled_state_metadata_check;

CREATE OR REPLACE FUNCTION sync_scheduled_reminder_legacy_sent()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    IF NEW.sent IS TRUE AND (
        TG_OP = 'INSERT' OR OLD.sent IS DISTINCT FROM TRUE
    ) THEN
        NEW.delivery_status := 'published';
        NEW.published_at := COALESCE(NEW.published_at, CURRENT_TIMESTAMP);
    ELSE
        NEW.sent := NEW.delivery_status <> 'pending';
    END IF;
    RETURN NEW;
END
$function$;

CREATE TRIGGER assistant_scheduled_reminders_sync_sent
BEFORE INSERT OR UPDATE ON assistant_scheduled_reminders
FOR EACH ROW
EXECUTE FUNCTION sync_scheduled_reminder_legacy_sent();

CREATE INDEX IF NOT EXISTS assistant_outbox_due_delivery_idx
ON assistant_outbox (
    tenant_id, dispatch_status, next_attempt_at, claimed_until, created_at
);

CREATE INDEX IF NOT EXISTS assistant_scheduled_reminders_delivery_idx
ON assistant_scheduled_reminders (
    tenant_id, delivery_status, next_attempt_at, notify_at, reminder_id
);
