# Redis Memory Pressure

## Symptoms
Evicted keys climbing, latency spikes above 50 ms, and `OOM command not allowed` errors in application logs indicate memory pressure. The cache cluster is configured with `maxmemory-policy allkeys-lru` and a 16 GB limit per node.

## Finding large keys
Run `redis-cli --bigkeys` against a replica, never the primary, because the scan adds load. For a precise per-key view use `MEMORY USAGE <key>`. Session keys without a TTL are the usual culprit.

## Mitigation
Scale the cluster by adding a shard and running `redis-cli --cluster rebalance`. As a quick fix, set a 24 hour TTL on session keys using the `scripts/expire_sessions.py` helper. Do not run `FLUSHALL` in production under any circumstances.
