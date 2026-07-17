-- Stable reminder effects are unique only inside their authenticated tenant.
--
-- Alpha data is never rewritten or deleted here.  The preflight raises 23505
-- before any index is created when historical duplicates need an explicit
-- operator decision.  A migration runner should execute this file in one
-- transaction with search_path set to the target tenant-independent schema.

DO $migration$
DECLARE
    target_schema TEXT := current_schema();
    identity_spec RECORD;
    duplicate_groups BIGINT;
BEGIN
    FOR identity_spec IN
        SELECT *
        FROM (
            VALUES
                ('assistant_events', 'event_id'),
                ('assistant_outbox', 'idempotency_key'),
                ('assistant_outbox', 'message_id'),
                ('assistant_outbox', 'event_id'),
                ('assistant_calendar_events', 'idempotency_key'),
                ('assistant_calendar_events', 'event_id'),
                ('assistant_scheduled_reminders', 'idempotency_key'),
                ('assistant_scheduled_reminders', 'reminder_id')
        ) AS identities(table_name, identity_column)
    LOOP
        IF to_regclass(
            format('%I.%I', target_schema, identity_spec.table_name)
        ) IS NULL THEN
            RAISE EXCEPTION USING
                ERRCODE = '42P01',
                MESSAGE = format(
                    'reminder identity migration requires table %I.%I',
                    target_schema,
                    identity_spec.table_name
                );
        END IF;

        EXECUTE format(
            'SELECT count(*) FROM ('
            ' SELECT tenant_id, %1$I'
            ' FROM %2$I.%3$I'
            ' GROUP BY tenant_id, %1$I'
            ' HAVING count(*) > 1'
            ') AS duplicate_identities',
            identity_spec.identity_column,
            target_schema,
            identity_spec.table_name
        ) INTO duplicate_groups;

        IF duplicate_groups > 0 THEN
            RAISE EXCEPTION USING
                ERRCODE = '23505',
                MESSAGE = format(
                    'alpha data contains duplicate tenant-scoped identities in %I.%I',
                    identity_spec.table_name,
                    identity_spec.identity_column
                ),
                DETAIL = format(
                    '%s duplicate group(s); reconcile them explicitly before retrying 0002',
                    duplicate_groups
                ),
                HINT = 'No rows were changed by this migration.';
        END IF;
    END LOOP;
END
$migration$;

CREATE UNIQUE INDEX IF NOT EXISTS assistant_events_tenant_event_id_uidx
    ON assistant_events (tenant_id, event_id);

CREATE UNIQUE INDEX IF NOT EXISTS assistant_outbox_tenant_idempotency_key_uidx
    ON assistant_outbox (tenant_id, idempotency_key);

CREATE UNIQUE INDEX IF NOT EXISTS assistant_outbox_tenant_message_id_uidx
    ON assistant_outbox (tenant_id, message_id);

CREATE UNIQUE INDEX IF NOT EXISTS assistant_outbox_tenant_event_id_uidx
    ON assistant_outbox (tenant_id, event_id);

CREATE UNIQUE INDEX IF NOT EXISTS assistant_calendar_events_tenant_idempotency_key_uidx
    ON assistant_calendar_events (tenant_id, idempotency_key);

CREATE UNIQUE INDEX IF NOT EXISTS assistant_calendar_events_tenant_event_id_uidx
    ON assistant_calendar_events (tenant_id, event_id);

CREATE UNIQUE INDEX IF NOT EXISTS assistant_scheduled_reminders_tenant_idempotency_key_uidx
    ON assistant_scheduled_reminders (tenant_id, idempotency_key);

CREATE UNIQUE INDEX IF NOT EXISTS assistant_scheduled_reminders_tenant_reminder_id_uidx
    ON assistant_scheduled_reminders (tenant_id, reminder_id);
