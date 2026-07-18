-- One fixed-name row is enough to prove that at least one delivery worker is
-- making progress. Process IDs, hosts, tenants, recipients, and message IDs are
-- intentionally absent from this operational signal.
CREATE TABLE assistant_worker_heartbeats (
    worker_name TEXT PRIMARY KEY,
    heartbeat_at TIMESTAMPTZ NOT NULL
);
