# PostgreSQL Failover

## Detecting primary failure
The on-call engineer is paged when the primary database stops answering health checks for 30 seconds. Confirm the failure by running `pg_isready` against the primary host and checking the replication dashboard. Do not fail over if replication lag is above 60 seconds without approval from the database lead.

## Promoting the replica
Run `patronictl failover --candidate replica-1` from the bastion host. Promotion normally completes within 45 seconds. After promotion, verify that `pg_is_in_recovery()` returns false on the new primary. Application connection strings use the `db-primary` DNS alias, which updates automatically within 20 seconds.

## Rebuilding the old primary
Never restart the old primary as a primary. Wipe its data directory and re-seed it as a replica using `pg_basebackup`. Seeding a 500 GB database takes roughly 40 minutes. Rejoin it to the cluster only after the base backup completes.
