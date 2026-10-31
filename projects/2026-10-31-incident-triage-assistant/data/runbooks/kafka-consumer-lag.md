# Kafka consumer lag growing
## Symptoms
Consumer group lag increases, messages are processed late. Logs show rebalances, "commit failed", or "poll timeout exceeded max.poll.interval.ms".
## Mitigation
1. Run `kafka-consumer-groups --describe` to see which partitions lag.
2. Check for slow downstream calls inside the processing loop.
3. Increase max.poll.interval.ms or reduce max.poll.records.
4. Scale consumers up to the partition count.
