-- Keep the legacy sent flag as a rollback guard without overriding canonical
-- delivery transitions written by the outbox dispatcher.
CREATE OR REPLACE FUNCTION sync_scheduled_reminder_legacy_sent()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    IF TG_OP = 'INSERT' AND NEW.delivery_status <> 'pending' THEN
        NEW.sent := TRUE;
    ELSIF TG_OP = 'UPDATE'
       AND NEW.delivery_status IS DISTINCT FROM OLD.delivery_status THEN
        NEW.sent := NEW.delivery_status <> 'pending';
    ELSIF NEW.sent IS TRUE AND (
        TG_OP = 'INSERT' OR OLD.sent IS DISTINCT FROM TRUE
    ) THEN
        NEW.delivery_status := 'published';
        NEW.published_at := COALESCE(NEW.published_at, CURRENT_TIMESTAMP);
    ELSE
        NEW.sent := NEW.delivery_status <> 'pending';
    END IF;
    NEW.payload := COALESCE(NEW.payload, '{}'::jsonb) || jsonb_build_object(
        'delivery_status', NEW.delivery_status,
        'sent', NEW.sent,
        'published_at', NEW.published_at
    );
    RETURN NEW;
END
$function$;

ALTER TABLE assistant_outbox
    DROP CONSTRAINT assistant_outbox_state_metadata_check;

ALTER TABLE assistant_outbox
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
            dispatch_status = 'failed'
            AND claim_token IS NULL
            AND claim_owner IS NULL
            AND claimed_until IS NULL
            AND published_at IS NULL
            AND last_error_category IS NOT NULL
            AND last_error_code IS NOT NULL
            AND last_error_at IS NOT NULL
            AND (
                (attempts = 0 AND sending_at IS NULL)
                OR (attempts > 0 AND sending_at IS NOT NULL)
            )
        ) OR (
            dispatch_status = 'uncertain'
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
    );

ALTER TABLE assistant_scheduled_reminders
    DROP CONSTRAINT assistant_scheduled_state_metadata_check;

ALTER TABLE assistant_scheduled_reminders
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
            delivery_status = 'failed'
            AND published_at IS NULL
            AND last_error_category IS NOT NULL
            AND last_error_code IS NOT NULL
            AND last_error_at IS NOT NULL
            AND (
                (attempts = 0 AND sending_at IS NULL)
                OR (attempts > 0 AND sending_at IS NOT NULL)
            )
        ) OR (
            delivery_status = 'uncertain'
            AND attempts > 0
            AND sending_at IS NOT NULL
            AND published_at IS NULL
            AND last_error_category IS NOT NULL
            AND last_error_code IS NOT NULL
            AND last_error_at IS NOT NULL
        )
    );
