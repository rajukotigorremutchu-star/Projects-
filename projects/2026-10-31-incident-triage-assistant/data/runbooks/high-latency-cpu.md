# CPU saturation and high latency
## Symptoms
p99 latency spikes, health checks time out, "request took too long" or "deadline exceeded" while CPU is pinned at 100 percent and throttling is reported.
## Mitigation
1. Check CPU throttling metrics and top consumers with `top -H`.
2. Take a CPU profile for 30 seconds from one affected instance.
3. Scale out replicas or raise CPU limits as a stopgap.
4. Look for hot loops, regex backtracking or a thundering herd after cache expiry.
