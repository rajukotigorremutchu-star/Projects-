# Database connection pool exhausted
## Symptoms
Requests time out waiting for a database connection. Logs show "timeout acquiring connection from pool" or "too many clients already" from Postgres.
## Mitigation
1. Check active connections with `SELECT count(*) FROM pg_stat_activity` grouped by application.
2. Kill idle-in-transaction sessions older than 5 minutes.
3. Temporarily raise the pool size by at most 20 percent and restart one pod at a time.
4. Look for a recent deploy that leaks connections (missing close or commit).
