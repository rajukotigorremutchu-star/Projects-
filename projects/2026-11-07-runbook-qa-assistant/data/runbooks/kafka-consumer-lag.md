# Kafka Consumer Lag

## Alert thresholds
A warning fires when consumer lag exceeds 100000 messages for 10 minutes. A critical page fires at 1 million messages. Lag is measured per consumer group and partition by the exporter named `kafka-lag-exporter`.

## Diagnosing slow consumers
Check whether the consumer group is rebalancing repeatedly; frequent rebalances usually mean the session timeout is too low or a consumer is crashing. Inspect pod logs for out-of-memory kills. If a single partition is lagging while others are healthy, suspect a hot key or a poison message.

## Skipping a poison message
Pause the consumer group, then use `kafka-consumer-groups.sh --reset-offsets --shift-by 1` to skip one offset on the affected partition. Always copy the skipped message to the `dead-letter` topic first so it can be analysed later. Resume the group and confirm lag is falling.
