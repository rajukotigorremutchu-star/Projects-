"""DuckDB ETL: raw request logs -> gap-free per-service per-minute features."""
from __future__ import annotations

import duckdb
import pandas as pd

FEATURE_SQL = """
WITH grid AS (
    SELECT s.service, m.minute
    FROM (SELECT DISTINCT service FROM logs) s
    CROSS JOIN (SELECT unnest(generate_series(?::TIMESTAMP, ?::TIMESTAMP, INTERVAL 1 MINUTE)) AS minute) m
), agg AS (
    SELECT service, date_trunc('minute', ts) AS minute,
           count(*) AS requests,
           avg(CASE WHEN status >= 500 THEN 1.0 ELSE 0.0 END) AS error_rate,
           quantile_cont(latency_ms, 0.95) AS p95_ms
    FROM logs GROUP BY 1, 2
)
SELECT g.service, g.minute,
       coalesce(a.requests, 0)::INTEGER AS requests,
       coalesce(a.error_rate, 0.0) AS error_rate,
       coalesce(a.p95_ms, 0.0) AS p95_ms
FROM grid g LEFT JOIN agg a USING (service, minute)
ORDER BY g.service, g.minute
"""


def build_features(logs: pd.DataFrame) -> pd.DataFrame:
    """Minutes with no traffic are kept (requests=0) so outages stay visible."""
    if logs.empty:
        raise ValueError("no log rows")
    lo = logs["ts"].min().floor("min")
    hi = logs["ts"].max().floor("min")
    con = duckdb.connect()
    try:
        con.register("logs", logs)
        return con.execute(FEATURE_SQL, [lo.to_pydatetime(), hi.to_pydatetime()]).df()
    finally:
        con.close()
